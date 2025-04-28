# VERSION: 1.0
# temp until NB can access GitLab

from __future__ import annotations

from typing import Final

from dcim.choices import CableTypeChoices, DeviceStatusChoices, LinkStatusChoices
from dcim.models import (
    Cable,
    ConsoleServerPort,
    Device,
    DeviceRole,
    DeviceType,
    FrontPort,
    Module,
    ModuleBay,
    ModuleType,
    Rack,
    RearPort,
    Site,
)
from extras.scripts import (
    ChoiceVar,
    IntegerVar,
    ObjectVar,
    Script,
    StringVar,
)
from tenancy.models import Tenant
from utilities.exceptions import AbortScript

NCS1010_MODELS: Final[list[str]] = [
    "NCS-1010 E-OLT-R-C",
    "NCS-1010 E-OLT-C",
    "NCS-1010 OLT-C",
]
HUBSITE_TENANT: Final = Tenant.objects.get(name="CENIC Hubsite")
PANEL_ROLES: Final[list[str]] = ["rj45-copper-panels"]
PANEL_TYPE: Final = DeviceType.objects.get(slug="24-port-cat6-panel")
TERMINAL_SERVER: Final = DeviceType.objects.get(slug="c1100")
TSERVER_ROLE: Final = DeviceRole.objects.get(slug="terminal-server")


def wrap_save(obj) -> None:
    """Wrapper for saving new objects."""
    obj.full_clean()
    obj.save()


def get_increment(hostname: str) -> str:
    """Return next increment for device name, i.e. alacc-cpe-02."""
    if not (device_list := Device.objects.filter(name__contains=hostname)):
        return "01"
    else:
        last_increment = device_list.last().name.split("-")[-1]
        return str(int(last_increment) + 1).rjust(2, "0")  # ensure a two-digit string


class NCS1010(Script):
    class Meta:
        name = "Deploy NCS-1010 Optical Transport"
        description = "Creates new NCS-1010s and passive shelves."
        scheduling_enabled = False
        fieldsets = (
            ("Device", ("site", "rack", "ru", "device_type", "ticket", "status", "remote_site")),
            ("Modules", ("brk_sa_ru", "md32_ru", "slot_0", "slot_1", "slot_2", "slot_3")),
        )

    site = ObjectVar(model=Site)
    rack = ObjectVar(model=Rack, query_params={"site_id": "$site"})
    ru = IntegerVar(label="Shelf Lowest RU", required=False, min_value=1, max_value=44)
    ticket = StringVar(label="Deployment Ticket", regex="^((NOC)|(COR)|(SYS)|(NENG)|(DEP))-[0-9]{1,7}$")
    status = ChoiceVar(label="Current Status", choices=DeviceStatusChoices)

    TYPE_CHOICES = tuple((i, i) for i in NCS1010_MODELS)
    device_type = ChoiceVar(label="Shelf Type", choices=TYPE_CHOICES)
    remote_site = ObjectVar(model=Site)

    brk_sa_ru = IntegerVar(
        label="BRK-SA Rack Unit",
        description="If an BRK-SA is being installed, select the lowest RU.",
        required=False,
        min_value=1,
        max_value=44,
    )
    md32_ru = IntegerVar(
        label="MD32 Rack Unit",
        description="If an MD32 is being installed, select the lowest RU.",
        required=False,
        min_value=1,
        max_value=44,
    )
    MODULE_CHOICES = (("NCS1K-BRK-24", "NCS1K-BRK-24"), ("NCS1K-BRK-8", "NCS1K-BRK-8"))
    slot_0 = ChoiceVar(label="BRK-SA Slot 0 Module", required=False, choices=MODULE_CHOICES)
    slot_1 = ChoiceVar(label="BRK-SA Slot 1 Module", required=False, choices=MODULE_CHOICES)
    slot_2 = ChoiceVar(label="BRK-SA Slot 2 Module", required=False, choices=MODULE_CHOICES)
    slot_3 = ChoiceVar(label="BRK-SA Slot 3 Module", required=False, choices=MODULE_CHOICES)

    def run(self, data, commit):
        def make_device(hostname: str, rack_unit: int, device_type: str, role: str):
            device = Device(
                name=hostname,
                site=data["site"],
                rack=data["rack"],
                position=rack_unit,
                face="front",
                device_type=DeviceType.objects.get(model=device_type),
                role=DeviceRole.objects.get(name=role),
                status=data["status"],
                tenant=Tenant.objects.get(name="CENIC Backbone"),
                custom_field_data={"deployment_ticket": data["ticket"]},
            )
            wrap_save(device)
            return device

        slots = (data["slot_0"], data["slot_1"], data["slot_2"], data["slot_3"])
        if data["brk_sa_ru"]:
            assert any(slots), "BRK-SA must have at least one module."

        role = "oltc" if data["device_type"] == "NCS-1010 OLT-C" else "olta"
        hostname = f"{data['site'].name.lower()}{role}-{data['remote_site'].name.lower()}-"
        hostname += get_increment(hostname)

        device = make_device(hostname, data["ru"], data["device_type"], "OLS Transport")
        self.log_success(f"Created shelf: `{device.name}`.")

        if data["device_type"] == "NCS-1010 OLT-C":
            if data["brk_sa_ru"]:
                dev_name = f"BRK-SA - {hostname}"
                _ = make_device(dev_name, data["brk_sa_ru"], "NCS1K-BRK-SA", "OLS / DCI Misc. Equipment")
                brk_sa = Device.objects.get(name=dev_name)  # fetching again prevents module loading issues
                self.log_success(f"Created `{brk_sa.name}`.")

                for i, slot in enumerate(slots):
                    if slot:
                        module = Module(
                            device=brk_sa,
                            module_bay=ModuleBay.objects.get(name=f"Slot {i}", device=brk_sa),
                            module_type=ModuleType.objects.get(model=slot),
                        )

                        wrap_save(module)
                        self.log_success(f"Added module to BRK-SA: {module.module_type.model}")

                com_ports = RearPort.objects.filter(device=brk_sa)
                com_to_ad_map = {"0": "A/D 4-11", "1": "A/D 12-19", "2": "A/D 20-27", "3": "A/D 28-33"}
                for port in com_ports:
                    slot = port.name.split()[1]
                    cable = Cable(
                        type=CableTypeChoices.TYPE_SMF,
                        a_terminations=[FrontPort.objects.get(device=device, name=com_to_ad_map[slot])],
                        b_terminations=[port],
                        status="connected",
                        label="BRK-SA",
                    )
                    wrap_save(cable)
                    self.log_success(
                        f"Created cable from BRK-SA `{cable.b_terminations[0].name}` to shelf port `{cable.a_terminations[0].name}`"
                    )

            if data["md32_ru"]:
                dev_name = f"MD32 - {hostname}"
                _ = make_device(dev_name, data["md32_ru"], "NCS1K-MD-320-C", "OLS / DCI Misc. Equipment")
                md32 = Device.objects.get(name=dev_name)
                self.log_success(f"Created `{md32.name}`.")

                cable = Cable(
                    type=CableTypeChoices.TYPE_SMF,
                    a_terminations=[FrontPort.objects.get(device=device, name="A/D 2 Rx/Tx")],
                    b_terminations=[RearPort.objects.get(device=md32, name="COM")],
                    status="connected",
                    label="MD32",
                )
                wrap_save(cable)
                self.log_success(
                    f"Created cable from MD-32 `{cable.b_terminations[0].name}` to shelf port `{cable.a_terminations[0].name}`"
                )


class TerminalServer(Script):
    class Meta:
        name = "Deploy C1100 Terminal Server"
        description = "Install new C1100 and cabling/paneling at a hubsite."
        scheduling_enabled = False
        fieldsets = (
            ("Terminal Server", ("site", "rack", "dev_unit", "octals")),
            ("Patch Panel - use one option", ("panel", "panel_unit")),
            ("Deployment", ("status", "ticket")),
        )

    site = ObjectVar(model=Site)
    rack = ObjectVar(model=Rack, query_params={"site_id": "$site"})
    dev_unit = IntegerVar(label="Terminal Server RU", min_value=1, max_value=44)
    octals = IntegerVar(
        label="Octals",
        description="Number of octal cables in use. Enter `0` if no octals are being installed.",
        min_value=0,
        max_value=4,
        default=1,
    )

    panel = ObjectVar(
        model=Device,
        description="Select an RJ45 panel, if using an existing panel",
        query_params={"rack_id": "rack", "role": PANEL_ROLES},
        required=False,
    )
    panel_unit = IntegerVar(
        label="Panel RU",
        description="Select an RU, if installing a new panel",
        min_value=1,
        max_value=44,
        required=False,
    )

    status = ChoiceVar(
        label="Install Status",
        required=False,
        choices=DeviceStatusChoices,
        default=DeviceStatusChoices.STATUS_ACTIVE,
    )
    ticket = StringVar(label="Deployment Ticket", regex="^((NOC)|(COR)|(SYS)|(NENG)|(DEP))-[0-9]{1,7}$")

    def run(self, data, commit):
        if not any((data["panel"], data["panel_unit"])):
            raise AbortScript("An existing panel or RU for a new panel must be input.")

        tserver = Device(
            name=get_increment(f"{data['site'].name}-ts-".lower()),
            site=data["site"],
            rack=data["rack"],
            position=data["dev_unit"],
            face="front",
            device_type=TERMINAL_SERVER,
            role=TSERVER_ROLE,
            status=data["status"],
            tenant=HUBSITE_TENANT,
            custom_field_data={"deployment_ticket": data["ticket"]},
        )
        wrap_save(tserver)
        self.log_success(f"Created `{tserver.name}`")

        if data["panel_unit"]:
            panel = Device(
                name=f"COP-{data['site'].name}-{data['rack'].name}-U{str(data['dev_unit']).rjust(2, '0')}",
                site=data["site"],
                rack=data["rack"],
                position=data["panel_unit"],
                face="front",
                device_type=PANEL_TYPE,
                role=DeviceRole.objects.get(slug=PANEL_ROLES[0]),
                status=data["status"],
                tenant=HUBSITE_TENANT,
                custom_field_data={"deployment_ticket": data["ticket"]},
            )
            wrap_save(panel)
            self.log_success(f"Created `{panel.name}`")
        else:
            panel = data["panel"]

        # create connections using the next available ConsoleServer and Rear Ports
        if data["octals"]:
            async_ports = ConsoleServerPort.objects.filter(device=tserver).iterator()
            panel_ports = RearPort.objects.filter(device=panel, cable=None).iterator()
            for _ in range(data["octals"]):
                for _ in range(1, 9):
                    cable = Cable(
                        type=CableTypeChoices.TYPE_CAT6,
                        a_terminations=[next(async_ports)],
                        b_terminations=[next(panel_ports)],
                        status=LinkStatusChoices.STATUS_CONNECTED,
                    )
                    wrap_save(cable)
            self.log_success(f"Connected {data['octals'] * 8} console cables to the panel.")
