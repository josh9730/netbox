# VERSION: 1.0
# temp until NB can access GitLab

import hashlib

import netaddr
from dcim.models import Device, DeviceRole, Interface
from extras.scripts import BooleanVar, ChoiceVar, IPAddressWithMaskVar, IntegerVar, ObjectVar, Script
from tenancy.models import Tenant
from utilities.exceptions import AbortScript

name = "Misc. Scripts"


def make_choices(choices: list[str]) -> tuple[tuple[str, str]]:
    return tuple((i, i) for i in choices)


def filter_devices():
    roles_names = ["Backbone Router", "Backbone Switch", "CPE Router"]
    roles = DeviceRole.objects.filter(name__in=roles_names)
    return [i.id for i in roles]


class V4toV6(Script):
    class Meta:
        name = "IPv4 to IPv6 Converter"
        description = "Convert 137.164.0.0/16 IPv4 addresses to an IPv6 address."
        commit_default = False
        scheduling_enabled = False

    v4 = IPAddressWithMaskVar(
        label="IPv4 Address",
        description="IPv4 Address with mask in slash notation. 137.164.0.0/16 only.",
    )
    NET_CHOICES = (("1", "DC"), ("0", "HPR"), ("3", "PeerNet"))
    LINK_CHOICES = (("0", "Internal Link"), ("1", "External Link"))
    net = ChoiceVar(label="Network", choices=NET_CHOICES, default="1")
    link = ChoiceVar(
        label="Link Type",
        description="Choose type of link. Internal means all IPs are configured only on CENIC devices",
        choices=LINK_CHOICES,
        default="0",
    )

    def run(self, data, commit):
        def test_v4_value(ipv4: netaddr.IPNetwork) -> None:
            """Validate IPv4 network."""
            try:
                supernets = ipv4.supernet(prefixlen=16)
                assert str(supernets[0]) == "137.164.0.0/16"
                assert ipv4.prefixlen in (31, 32)
            except (IndexError, ValueError, AssertionError):  # ValueError if input is > 16
                raise AbortScript("Converter is only valid for /31s and /32s in 137.164.0.0/16")

        def convert_v4(ipv4: netaddr.IPAddress, net: str, link: str) -> netaddr.IPAddress:
            """Convert IPv4 to IPv6."""
            v4_hex = hex(ipv4).lstrip("0x")
            hex_a, hex_b, hex_c = v4_hex[0], v4_hex[1:5], v4_hex[5 : len(v4_hex)]
            v6_str = f"2607:f380:000{link}:0:0:01{net}{hex_a}:{hex_b}:{hex_c}1"

            try:
                return netaddr.IPAddress(v6_str, 6)
            except netaddr.core.AddrFormatError:
                raise AbortScript("IP address cannot be formatted correctly, please contact an admin.")

        test_v4_value(data["v4"])

        v6_mask = "128" if data["v4"].prefixlen == 32 else "123"  # /31s are converted to /123s for legacy reasons

        v6_ips = []
        for ipv4 in data["v4"].iter_hosts():
            v6 = convert_v4(ipv4, data["net"], data["link"])
            v6_ips.append(f"{v6}/{v6_mask}")
        ipv6_str = ", ".join(v6_ips)

        # fmt: off
        log_msg = f"""**IPv4 Network:** `{data["v4"].network}/{data['v4'].prefixlen}`  
            **IPv6 Addresses:** `{ipv6_str}`  
            """
        # fmt: on

        self.log_success(log_msg)


class InterfaceTag(Script):
    class Meta:
        name = "Interface Tag Generator"
        description = "Generate an interface tag for a given port. Tag is NOT pushed to the device."
        commit_default = False
        scheduling_enabled = False
        fieldsets = (
            ("Devices", ("device", "subinterface", "remote_device", "remote_interface", "remote_logical", "handoff")),
            ("Services", ("speed", "clr", "network", "service")),
            ("Other", ("segment",)),
        )

    valid_roles = filter_devices()
    device = ObjectVar(model=Device, query_params={"role_id": valid_roles})
    subinterface = BooleanVar(
        label="Logical Unit", default=True, description="Uncheck if Cisco and not using a subinterface."
    )

    remote_device = ObjectVar(model=Device, description="Required if not a handoff", required=False)
    remote_interface = ObjectVar(
        model=Interface,
        description="Required if not a handoff",
        query_params={"device_id": "$remote_device"},
        required=False,
    )
    remote_logical = IntegerVar(min_value=0, max_value=5000, label="Remote Logical Unit", required=False)
    handoff = BooleanVar(description="Select if customer-facing.", required=False)

    SPEED_CHOICES = ["1G", "10G", "40G", "100G", "100M", "500M"]
    NETWORK_CHOICES = ["DC", "HPR"]
    SERVICE_CHOICES = ["Layer 3", "Layer 2", "DMS"]

    clr = IntegerVar(min_value=1000, max_value=99999, label="CLR")
    speed = ChoiceVar(choices=make_choices(SPEED_CHOICES))
    network = ChoiceVar(choices=make_choices(NETWORK_CHOICES), default="dc")
    service = ChoiceVar(choices=make_choices(SERVICE_CHOICES))

    msg = "Segment defaults to the segment of the Device. This is only required if the Device's segment needs to be overridden, ex: Handoff on a BB Device."
    segment = ObjectVar(model=Tenant, required=False, description=msg)

    def run(self, data, commit):
        def make_description(data: dict[str, str], site_code: str) -> str:
            description = f'{data["speed"]} to '
            if data.get("handoff"):
                description += f"{site_code} Handoff"
            else:
                description += f'{data["remote_device"]} {data["remote_interface"]}'
                if data.get("remote_logical"):
                    description += f'.{data["remote_logical"]}'
                description += f' CLR-{data["clr"]}'

            return description

        def make_handoff_tags(
            data: dict[str, str], net: str, site_code: str, local_tenant: str
        ) -> tuple[str | None, str | None]:
            port_tags = f"[{net}:ext]"
            edge_tags = f"[{net}:{local_tenant}][{net}:site-{site_code}]"
            dms = f"[{net}:dms][{net}:l2edge]"
            subint = data.get("subinterface")
            l3 = f"[{net}:edge]"
            l2 = f"[{net}:l2edge]"

            match data.get("service"):
                case "Layer 3" if subint:
                    return port_tags, l3 + edge_tags
                case "Layer 2" if subint:
                    return port_tags, l2 + edge_tags
                case "DMS" if subint:
                    return port_tags, dms + edge_tags

                case "Layer 3":
                    return port_tags + l3 + edge_tags, None
                case "Layer 2":
                    return port_tags + l2 + edge_tags, None
                case "DMS":
                    return port_tags + dms + edge_tags, None

                case _:
                    raise AbortScript("Tags not defined for this configuration.")

        def make_infra_tag(
            data: dict[str, str], net: str, port_tag: str | None = None
        ) -> tuple[str | None, str | None]:
            port_tag = port_tag if port_tag else f"[{net}:core]"
            infra_tag = f"[{net}:infra]"
            subint = data.get("subinterface")
            match data.get("service"):
                case "Layer 3" if subint:
                    return port_tag, infra_tag
                case "Layer 2" if subint:
                    return None, port_tag

                case "Layer 3":
                    return port_tag + infra_tag, None
                case "Layer 2":
                    return port_tag, None

                case _:
                    raise AbortScript("Tags not defined for this configuration.")

        def make_interconnect_tags(
            data: dict[str, str], net: str, site_code: str, local_role: str, remote_role: str
        ) -> tuple[str | None, str | None]:
            """For all with a local segment of CENIC Backbone"""
            cpe = "CPE Router" in (local_role, remote_role)
            port_tag = f"[{net}:core]"

            match (remote_role.split()[1], local_role.split()[1]):
                # Backbone to CPE
                case ("Router", "Router") if cpe:
                    return make_infra_tag(data, net)
                case ("Router", "Switch") if cpe:
                    return f"[{net}:core][{net}:l2acc]", None

                # Backbone to Backbone
                case ("Router", "Router"):
                    return make_infra_tag(data, net, port_tag=f"[{net}:bb-{site_code}]")
                case ("Switch", "Router"):
                    return port_tag + f"[{net}:asi]", None

                # Backbone to Backbone, L2
                case ("Switch", "Router") if not cpe:
                    return port_tag + f"[{net}:asi]", None
                case ("Router", "Switch") if not cpe:
                    return port_tag + f"[{net}:l2agg]", None
                case ("Switch", "Switch") if not cpe:
                    return port_tag + f"[{net}:l2icl]", None

                case _:
                    raise AbortScript("Tags not defined for this configuration.")

        def make_tags(
            data: dict[str, str],
            net: str,
            site_code: str,
            local_role: str | None = None,
            remote_role: str | None = None,
            local_tenant: str | None = None,
        ) -> tuple[str | None, str | None]:
            if data.get("handoff"):
                port_tags, subint_tags = make_handoff_tags(data, net, site_code, local_tenant)
            else:
                port_tags, subint_tags = make_interconnect_tags(data, net, site_code, local_role, remote_role)

            description = make_description(data, site_code)
            if data.get("subinterface"):
                return port_tags, subint_tags + " " + description
            return port_tags + " " + description, None

        def test_values(data: dict[str, str]) -> None:
            if all((data["handoff"], data["remote_device"])):
                raise AbortScript("Cannot select both Handoff and Remote Device.")
            if not any((data["handoff"], data["remote_device"])):
                raise AbortScript("Must select one of Handoff or Remote Device.")
            if not data.get("handoff") and not data.get("clr"):
                raise AbortScript("Must enter a CLR for a non-Handoff connection.")
            if data.get("subinterface") and "Switch" in data["device"].role.name:
                raise AbortScript("Cannot have a logical unit defined for a Switch role local device.")

        test_values(data)

        net = data["network"].lower()
        site_code = data["device"].site.name.lower()
        local_role = data["device"].role.name

        if data["segment"]:
            local_tenant = data["segment"].name.lower()
        else:
            local_tenant = data["device"].tenant.name.lower()
        if data["remote_device"]:
            remote_role = data["remote_device"].role.name
        else:
            remote_role = None

        port_desc, subint_desc = make_tags(
            data, net, site_code, local_role=local_role, remote_role=remote_role, local_tenant=local_tenant
        )

        # fmt: off
        self.log_success(
            f"""Generated Tags:  
            **Port Tag**: `{port_desc if port_desc else ""}`  
            **Unit Tag**: `{subint_desc if subint_desc else ""}`  
            """
        )
        # fmt: on


class MD5Gen(Script):
    """The MD5 hash algorith is the first 10 digits of '{{ CENIC ASN }}:{{ ASSOCIATE ASN }}\n'
    Note that the '\n' added to the algorithm is being maintained for legacy reasons.
    """

    class Meta:
        name = "BGP Password Generator"
        description = "Generate CalREN BGP password via an MD5 hash"
        commit_default = False
        scheduling_enabled = False

    CENIC_CHOICES = (
        ("2152", "2152"),
        ("2153", "2153"),
    )
    cenic_as = ChoiceVar(label="CENIC ASN", description="Select the CENIC ASN", choices=CENIC_CHOICES, default="2152")
    their_as = IntegerVar(
        label="Associate ASN", description="Input the Associate ASN", min_value=1, max_value=4294967295
    )

    def run(self, data, commit):
        asn_string = f'{data["cenic_as"]}:{data["their_as"]}\n'
        output = hashlib.md5(asn_string.encode()).hexdigest()[:10]

        self.log_success(message=f"`MD5 hash: {output}`")
        return output
