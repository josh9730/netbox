from typing import Final

from dcim.choices import DeviceStatusChoices
from dcim.models import Device, DeviceRole, DeviceType, ModuleBay, Rack, Site
from extras.scripts import ChoiceVar, IntegerVar, ObjectVar, Script, StringVar
from tenancy.models import Tenant

name = "Devices"

_TERMINAL_SITES: Final = (  # list of optical OSPF gateway nodes
    "LOSA2",
    "LOSA4",
    "SNVL2",
    "SACR1",
    "SACR2",
    "SAND1",
    "RIVE1",
)
_DCI_OLS_PANELS: Final = (  # panels that need to be associated with a shelf, ie tric1ca51o - Shelf 1 MD40
    "2150",
    "md48",
    "md40",
    "mpo-8lc",
)
OPTICAL_ROLES: Final = [
    "ols-transport",
    "dci-optical",
]
HUBSITE_TENANT: Final = Tenant.objects.get(name="CENIC Hubsite")
ENCLOSURE: Final = DeviceType.objects.get(model="FHD Enclosure, Blank")
PANEL_ROLE: Final = DeviceRole.objects.get(slug="modular-panels")
XCONNECT_PANEL: Final = "48-port-lc-lc-"
XCONNECT_ROLE: Final = "xconnect-panels"


def wrap_save(obj) -> None:
    obj.full_clean()
    obj.save()


def non_panel_types() -> list[DeviceType]:
    """Return all non-Panel DeviceTypes by filtering on device_class custom field."""
    return list(DeviceType.objects.exclude(custom_field_data__contains={"device_class": "Panels"}))


def get_increment(hostname: str) -> str:
    """Return next increment for device name, i.e. alacc-cpe-02."""
    if not (device_list := Device.objects.filter(name__contains=hostname)):
        return "01"
    else:
        last_increment = device_list.last().name.split("-")[-1]
        return str(int(last_increment) + 1).rjust(2, "0")  # ensure a two-digit string


class NewDevice(Script):
    class Meta:
        name = "New Device"
        description = "Create a new, non-panel, device"
        scheduling_enabled = False

    site = ObjectVar(model=Site)
    rack = ObjectVar(model=Rack, query_params={"site_id": "$site"})
    rack_unit = IntegerVar(label="Lowest RU", min_value=1, max_value=44)

    NON_PANEL_TYPES = tuple((i.model, i.model) for i in non_panel_types())
    device_type = ChoiceVar(label="Device Type", choices=NON_PANEL_TYPES)

    tenant = ObjectVar(label="Device Segment", model=Tenant)
    optical_route = IntegerVar(
        label="Optical Route",
        description="Numerical Optical Route ID, required if Layer 1 Device",
        required=False,
        min_value=1,
        max_value=255,
    )

    optical_chassis = ObjectVar(
        label="Optical Chassis",
        description="Primary optical chassis, required if adding OLS/DCI Misc. devices. Ex: select the appropriate optical shelf when adding a Cisco MD-48.",
        required=False,
        model=Device,
        query_params={"rack_id": "$rack", "role": OPTICAL_ROLES},
    )
    shelf_id = IntegerVar(
        label="Shelf ID",
        description="Numerical Shelf #, required if adding a Cisco NCS-2006/15 Chassis",
        required=False,
        min_value=1,
        max_value=20,
    )
    server_name = StringVar(
        label="Server Name",
        required=False,
        description="Device Name, only used for Server/CDN",
        min_length=3,
        max_length=15,
    )
    ticket = StringVar(label="Deployment Ticket", regex="^((NOC)|(COR)|(SYS)|(NENG)|(DEP))-[0-9]{1,7}$")
    status = ChoiceVar(label="Current Status", choices=DeviceStatusChoices)

    def run(self, data, commit):
        def create_name_role(data: dict, device_type: DeviceType) -> tuple[str, str]:
            """Create names and device roles.

            - Hostnames that are expected to have A/AAAA records should be all lowercase
            - Otherwise, all uppercase
            """
            device_class = device_type.custom_field_data.get("device_class")
            tenant = data["tenant"].name
            site = data["site"].name
            rack = data["rack"].name
            rack_unit = data["rack_unit"]
            device_type_slug = device_type.slug
            optical_route = data.get("optical_route")
            optical_chassis = data.get("optical_chassis")
            hostname = site.lower()

            match device_class:
                case "Router" | "Switch":
                    match tenant:
                        case "CENIC Enterprise":
                            device_role = f"Management {device_class}"
                            role_name = "-mgmt-"
                        case "CENIC Backbone" | "PacWave CENIC":
                            device_role = f"Backbone {device_class}"
                            role_name = "-agg-" if tenant == "CENIC Backbone" else "-pw-"
                        case _:
                            device_role = f"CPE {device_class}"
                            role_name = "-cpe-"

                    if ("CENIC" in tenant) and (device_class == "Switch"):
                        role_name += "sw-"
                    hostname += role_name
                    hostname += get_increment(hostname)

                case "DCI":
                    assert optical_route, "Optical devices must have an optical route defined."
                    device_role = "DCI Optical"
                    role_name = "dci-"
                    if tenant == "PacWave CENIC":
                        role_name += "pw-"
                    hostname += f"-{optical_route}-{role_name}"
                    hostname += get_increment(hostname)

                case "OLS":
                    assert optical_route, "Optical devices must have an optical route defined."

                case "OLS_DCI_Misc":
                    assert optical_route, "Optical devices must have an optical route defined."
                    msg = "OLS/DCI Misc. Equipment must be in either the CENIC Backbone or Associate tenancy"
                    assert tenant not in ("CENIC Enterprise", "CENIC Hubsite"), msg

                    device_role = "OLS / DCI Misc. Equipment"
                    if device_type_slug in _DCI_OLS_PANELS:
                        assert optical_chassis, "DCI/OLS panels must have a linked Optical Chassis defined"
                        hostname = f"{optical_chassis} {device_type_slug.upper()}"
                    else:
                        hostname = f"{rack}-{device_type_slug}-U{rack_unit}".upper()

                case "PDU":
                    assert tenant == "CENIC Hubsite", "PDUs belong to the CENIC Hubsite tenant."
                    device_role = "PDU"
                    hostname += f"-{rack}-pdu-u{rack_unit}"

                case "Terminal Server":
                    assert tenant == "CENIC Enterprise", "Terminal Servers belong to the CENIC Enterprise tenant."
                    device_role = "Terminal Server"
                    hostname += "-ts-"
                    hostname += get_increment(hostname)

                case "OOB":
                    msg = "OOBs must have an Associate tenant"
                    assert all(x != tenant for x in ("CENIC Enterprise", "CENIC Backbone", "CENIC Hubsite")), msg
                    device_role = "CPE OOB"
                    hostname += "-oob-"
                    hostname += get_increment(hostname)

                case "Server":
                    assert (tenant == "CENIC Enterprise") or (
                        data["tenant"].group.name == "CDN"
                    ), "Servers must be CENIC Enterprise or CDN tenants."
                    assert data.get("server_name"), "Enterprise Servers must have a Server Name defined"
                    if tenant == "CENIC Enterprise":
                        device_role = "Enterprise Server"
                    else:
                        device_role = "CDN"
                    hostname = data["server_name"].upper() + f" - U{rack_unit}"

                case _:
                    device_role = "Misc. Non-CENIC Managed"
                    hostname = f"{rack}-{device_type_slug}-U{rack_unit}".upper()

            return device_role, hostname

        device_type = DeviceType.objects.get(model=data["device_type"])
        device_role, hostname = create_name_role(data, device_type)

        device = Device(
            name=hostname,
            site=data["site"],
            rack=data["rack"],
            position=data["rack_unit"],
            face="front",
            device_role=DeviceRole.objects.get(name=device_role),
            device_type=device_type,
            status=data["status"],
            tenant=data["tenant"],
            custom_field_data={"deployment_ticket": data["ticket"]},
        )
        wrap_save(device)

        self.log_success(
            # fmt: off
            f"""Created new device {device.name} with the following attributes:  
                **Site**: `{device.site.name}`  
                **Rack**: `{device.rack.name}`  
                **Device Type**: `{device.device_type.model}`  
                **Device Role**: `{device.device_role.name}`  
                **Tenant**: `{device.tenant.name}`  
                **Status**: `{device.status}`  
            """
            # fmt: on
        )


class CreatePanels(Script):
    class Meta:
        name = "New Modular Panels"
        description = "Create pair of FHD panels (no cassettes)"
        scheduling_enabled = False

    site = ObjectVar(label="Site Name", model=Site)
    rack_1 = ObjectVar(label="Rack A", model=Rack, query_params={"site_id": "$site"})
    rack_1_position = IntegerVar(
        label="Rack A Position", description="Lowest RU filled by the new panel.", min_value=1, max_value=44
    )
    rack_2 = ObjectVar(label="Rack B", model=Rack, query_params={"site_id": "$site"})
    rack_2_position = IntegerVar(
        label="Rack B Position", description="Lowest RU filled by the new panel.", min_value=1, max_value=44
    )
    status = ChoiceVar(
        label="Install Status", required=False, choices=DeviceStatusChoices, default=DeviceStatusChoices.STATUS_ACTIVE
    )
    ticket = StringVar(label="Deployment Ticket", regex="^((NOC)|(COR)|(SYS)|(NENG)|(DEP))-[0-9]{1,7}$")

    def run(self, data, commit):
        def hub_spoke_check(rack_1: Rack, rack_2: Rack) -> bool:
            """If any rack is the Hub, then the panel names are HUB-/SPK-. Else, names are SS-."""
            return True if any((rack_1.custom_field_data["hub_rack"], rack_2.custom_field_data["hub_rack"])) else False

        def create_remote_panel_field(panels: list[Device]) -> None:
            """Create link between the new panels using the remote_panel custom field."""
            panels[0].custom_field_data["remote_panel"] = panels[1].id
            panels[1].custom_field_data["remote_panel"] = panels[0].id
            wrap_save(panels[0])
            wrap_save(panels[1])

        def create_module_bays(panels: list[Device]) -> None:
            """Create ModuleBays, due to bug when assigning remote_panel link, which makes modules un-viewable."""
            for panel in panels:
                for i in range(1, 5):
                    mod = ModuleBay(device=panel, position=i, name=f"Slot {i}")
                    wrap_save(mod)

        hub_spoke = hub_spoke_check(data["rack_1"], data["rack_2"])

        panels = []
        for i in range(1, 3):
            rack_unit = str(data[f"rack_{i}_position"]).rjust(2, "0")
            panel_name = f'-{data["site"]}-' f'{data[f"rack_{i}"].name.split(" (")[0]}-' f"U{rack_unit}"

            if hub_spoke:
                rack_type = "HUB" if data[f"rack_{i}"].custom_field_data["hub_rack"] else "SPK"
                panel_name = rack_type + panel_name
            else:
                panel_name = "SS" + panel_name

            panel = Device(
                site=data["site"],
                rack=data[f"rack_{i}"],
                position=int(rack_unit),
                face="front",
                device_type=ENCLOSURE,
                device_role=PANEL_ROLE,
                name=panel_name,
                status=data["status"],
                tenant=HUBSITE_TENANT,
                custom_field_data={"deployment_ticket": data["ticket"]},
            )
            wrap_save(panel)
            panels.append(panel)
            self.log_success(f"Created new panel: `{panel}`.")

        create_remote_panel_field(panels)
        self.log_success("Created `Remote Panel` link between the new panels.")

        create_module_bays(panels)
        self.log_success("Created (4) ModuleBays in each panel, for adding FHD cassettes.")


class NewXConnect(Script):
    class Meta:
        name = "New Cross Connect Panel"
        description = "Create a new 48-port Cross Connect Panel"
        scheduling_enabled = False

    site = ObjectVar(model=Site)
    rack = ObjectVar(model=Rack, query_params={"site_id": "$site"})
    position = IntegerVar(
        label="Rack Position", description="Lowest RU filled by the new panel.", min_value=1, max_value=44
    )
    TYPE_CHOICES = (("os2", "Single-mode"), ("om4", "Multi-mode"))
    cable_type = ChoiceVar(label="Cable Type", choices=TYPE_CHOICES, default="smf")
    status = ChoiceVar(label="Install Status", choices=DeviceStatusChoices, default=DeviceStatusChoices.STATUS_ACTIVE)
    ticket = StringVar(label="Deployment Ticket")

    def run(self, data, commit):
        panel_name = f"XCP-{data['site']}-{data['rack']}-U{str(data['position']).rjust(2, '0')}"
        panel = Device(
            site=data["site"],
            rack=data["rack"],
            position=data["position"],
            face="front",
            device_type=DeviceType.objects.get(slug=f"{XCONNECT_PANEL}{data['cable_type']}"),
            device_role=XCONNECT_ROLE,
            name=panel_name,
            tenant=HUBSITE_TENANT,
            custom_field_data={"deployment_ticket": data["ticket"]},
        )
        wrap_save(panel)
        self.log_success(f"Created cross connect panel `{panel}`.")
