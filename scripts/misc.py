import netaddr
from extras.scripts import ChoiceVar, IPAddressWithMaskVar, Script
from utilities.exceptions import AbortScript


class V4toV6(Script):
    class Meta:
        name = "IPv4 to IPv6 Converter"
        description = "Convert 137.164.0.0/16 IPv4 addresses to an IPv6 address."
        read_only = True
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
                raise AbortScript('IP address cannot be formatted correctly, please contact an admin.')

        test_v4_value(data["v4"])

        v6_mask = "128" if data["v4"].prefixlen == 32 else "123"  # /31s are converted to /123s for legacy reasons

        v6_ips = []
        for ipv4 in data['v4'].iter_hosts():
            v6 = convert_v4(ipv4, data['net'], data["link"])
            v6_ips.append(f"{v6}/{v6_mask}")
        ipv6_str = ", ".join(v6_ips)

        # fmt: off
        log_msg = f"""**IPv4 Network:** `{data["v4"].network}/{data['v4'].prefixlen}`  
            **IPv6 Addresses:** `{ipv6_str}`  
            """
        # fmt: on

        self.log_success(log_msg)
