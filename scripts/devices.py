from itertools import chain
from typing import Final

from dcim.choices import DeviceStatusChoices
from dcim.models import Device, DeviceRole, DeviceType, FrontPort, Rack, RearPort, Site
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


def non_panel_types() -> list[DeviceType]:
    """Return all non-Panel DeviceTypes by filtering on device_class custom field."""
    return list(DeviceType.objects.exclude(custom_field_data__contains={"device_class": "Panels"}))


def get_increment(hostname) -> str:
    """Return next increment for device name, i.e. alacc-cpe-02."""
    if not (device_list := Device.objects.filter(name__contains=hostname)):
        return "01"
    else:
        last_increment = device_list.last().name.split("-")[-1]
        return str(int(last_increment) + 1).rjust(2, "0")  # ensure a two-digit string


def create_name_role(data: dict) -> tuple[str, str]:
    """Create names and device roles.

    - Hostnames that are expected to have A/AAAA records should be all lowercase
    - Otherwise, all uppercase
    """
    device_class = data["device_type"].custom_field_data.get("device_class")
    tenant = data["tenant"].name
    site = data["site"].name
    rack = data["rack"].name
    rack_unit = data["rack_unit"]
    device_type_slug = data["device_type"].slug
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


class NewDevice(Script):
    class Meta:
        name = "New Device"
        description = (
            "Create new devices based on existing DeviceTypes. Modular Devices may need ports configured separately."
        )
        scheduling_enabled = False

    site = ObjectVar(model=Site)
    rack = ObjectVar(model=Rack, query_params={"site_id": "$site"})
    rack_unit = IntegerVar(label="Lowest RU", min_value=1, max_value=44)
    NON_PANEL_TYPES = tuple((i.model, i.model) for i in non_panel_types())
    device_type = ChoiceVar(
        label="Device Type",
        choices=NON_PANEL_TYPES,
    )
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
        query_params={
            "rack_id": "$rack",
            "role": OPTICAL_ROLES,
        },
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
        device_role, hostname = self.create_name_role(data)

        device = Device(
            name=hostname,
            site=data["site"],
            rack=data["rack"],
            position=data["rack_unit"],
            face="front",
            device_role=DeviceRole.objects.get(name=device_role),
            device_type=data["device_type"],
            status=data["status"],
            tenant=data["tenant"],
            custom_field_data={"deployment_ticket": data["ticket"]},
        )
        device.full_clean()
        device.save()

        self.log_success(
            # fmt: off
            f"""Created new device {device.name} with the following attributes:  
                **Site**: `{device.site.name}`  
                **Rack**: `{device.rack.name}`  
                **Device Type**: `{device.device_type.model}`  
                **Device Role**: `{device.device_role.name}`  
                **Tenant**: `{device.tenant.name}`  
                **Status**: `{device.status.name}`  
            """
            # fmt: on
        )


#
# CREATE INTERFACES
#


def non_panels_slugs() -> list:
    """Return list of slugs for non-panel devices."""
    query_results = DeviceRole.objects.exclude(name__contains="Panel")
    return [i.slug for i in query_results]


class ModuleNotSupported(Exception):
    """Raise Exception if a Module and DeviceType pairing is invalid."""

    def __init__(self, module, device_type):
        msg = f"""
        {device_type.upper()} is not supported for module {module} through this Job.
        Check that these inputs are correct. Interfaces *may* need to be added manually
        """
        super().__init__(msg)


class Module:
    """Class for Module attrs and port creation."""

    _NEW_OLS = ["15454-m6", "ncs-2006", "ncs-2015"]

    # trunk_client_map is {{ TRUNK_PORT }}: {{ LIST_OF_CLIENT_PORTS }}
    # all values must be of type int
    #
    # port_name and valid slots are per-chassis, not per-module, so they are set inside the
    # port_name and slot properties rather than in _MODULES directly.
    _MODULES: dict = {
        "10G TXP": {
            "valid_chassis": ["15454-m12", *_NEW_OLS],
            "trunk_client_map": {2: [1]},
            "description": "10G TXP XFP Client",
            "trunk_description": "10G TXP Builtin DWDM Trunk",
        },
        "OTU2 TXP": {
            "valid_chassis": ["15454-m12", *_NEW_OLS],
            "trunk_client_map": {3: [1], 4: [2]},
            "description": "OTU2 XFP Client",
            "trunk_description": "OTU2 DWDM XFP Trunk",
        },
        "10x10G TXP": {
            "valid_chassis": _NEW_OLS,
            "trunk_client_map": {k + 1: [k] for k in range(1, 11, 2)},
            "description": "10x10G TXP SFP+ Client",
            "trunk_description": "10x10G TXP DWDM SFP+ Trunk",
        },
        "10x10G MXP w/ Trunk Card": {
            "valid_chassis": _NEW_OLS,
            "trunk_client_map": {2: list(range(1, 11))},
            "multi_slot_mxp": True,
            "description": "10x10G MXP SFP+ Client",
            "trunk_description": "100G card Builtin DWDM Trunk",
        },
        "AR-MXP": {
            "valid_chassis": _NEW_OLS,
            "trunk_client_map": {9: list(range(1, 9))},
            "description": "1G SFP Client",
            "trunk_description": "10G XFP DWDM Trunk",
        },
        "100G TXP w/ CPAK": {
            "valid_chassis": _NEW_OLS,
            "trunk_client_map": {2: [1]},
            "description": "100G CPAK Client",
            "trunk_description": "100G Builtin DWDM Trunk",
        },
        "100G TXP w/ CXP": {
            "valid_chassis": _NEW_OLS,
            "trunk_client_map": {2: [1]},
            "description": "100G CXP Client",
            "trunk_description": "100G Builtin DWDM Trunk",
        },
        "MR-MXP w/ 200G TXP": {  # has clients in both the trunk and MR-MXP cards
            "valid_chassis": _NEW_OLS,
            "trunk_client_map": {2: [1]},
            "multi_slot_mxp": True,
            "description": "100G CPAK Client",
            "trunk_description": "200G Builtin DWDM Trunk",
        },
        "Waveserver 5": {
            "valid_chassis": ["waveserver-5"],
            "trunk_client_map": {
                1: list(range(3, 11)),
                2: list(range(11, 19)),
            },
            "description": "QSFP-DD/QSFP28 Client Ports",
            "trunk_description": "WaveLogic 5e Builtin DWDM Trunk",
        },
        "Waveserver Ai w/ 4x100G": {
            "valid_chassis": ["waveserver-ai"],
            "trunk_client_map": {1: list(range(3, 7))},
            "description": "QSFP28 Client",
            "trunk_description": "WaveLogic Ai Builtin DWDM Trunk",
        },
        "Waveserver Ai w/ 40x10G": {
            "valid_chassis": ["waveserver-ai"],
            "trunk_client_map": {1: list(range(3, 13))},
            "description": "QSFP+/QSFP28 Client",
            "trunk_description": "WaveLogic Ai Builtin DWDM Trunk",
        },
        "NCS1K4-2-QDD-C-K9": {
            "valid_chassis": ["ncs-1004"],
            "trunk_client_map": {
                0: list(range(2, 6)),
                1: list(range(6, 10)),
            },
            "description": "QSFP-DD/QSFP28 Client",
            "trunk_description": "800G Builtin DWDM Trunk",
        },
    }
    # MXP configurations that utilize more than (1) card for trunk/client ports
    # multi_slot_mxp bool is set in _MODULES for convenience
    _MULTI_SLOT_MXP = [k for k, v in _MODULES.items() if v.get("multi_slot_mxp")]

    def __init__(self, device: Device, module: str, slot: int, trunk_slot: int, port_name: str = None):
        self.device = device
        self.module = module
        self._slot = slot
        self._trunk_slot = trunk_slot  # default value set in IntegerVar
        self._port_name = port_name
        self.device_type = self.device.device_type.slug
        self.trunk_client_map = self._MODULES[self.module]["trunk_client_map"]

        # MR-MXP config has client in TXP and MR-MXP
        # special handling in create_l1_ports()
        self.mr_mxp = True if self.module == "MR-MXP w/ 200G TXP" else False

        self._check_chassis()

    def _check_chassis(self) -> None:
        """Check that the Module is valid for the device's DeviceType."""
        if self.device_type not in self._MODULES[self.module]["valid_chassis"]:
            raise ModuleNotSupported(self.module, self.device_type)

    @classmethod
    def list_modules(cls) -> list[str]:
        return list(cls._MODULES.keys())

    @staticmethod
    def wrap_save(obj) -> None:
        obj.full_clean()
        obj.save()

    @property
    def port_name(self) -> str:
        """Checks against slot. These are not performed in _MODULES attr as these
        requirements are per-chassis, not per-module.
        """
        match self.device_type:
            case self.device_type if "waveserver" in self.device_type:
                return "PORT-{}/{}"
            case "ncs-1004":
                return "Optics0/{}/0/{}"
            case _:
                return "Slot {} Port {}"

    @property
    def slot(self) -> int:
        """Checks against slot. These are not performed in _MODULES attr as these
        requirements are per-chassis, not per-module.
        """
        match self.device_type:
            case "15454-m6" | "ncs-2006":
                slots = range(2, 8)
            case "15454-m12" | "ncs-2015":
                slots = range(2, 18)
            case "waveserver-ai":
                slots = range(1, 4)
            case "waveserver-5" | "ncs-1004":
                slots = range(1, 5)
            case _:
                raise AssertionError("Slot configs not set for this module.")

        msg = "Given slot is invalid for the device type."
        assert self._slot in slots, msg
        return self._slot

    @property
    def trunk_slot(self) -> int:
        """Check that a trunk slot is configured by the user for multi-card MXP configs."""
        if self.module in self._MULTI_SLOT_MXP:
            assert self._trunk_slot != self.slot, "Trunk and Client slots may not be equal."
            assert self._trunk_slot > 0, f"Trunk Slot number must be defined for {self.module}."
            assert abs(self._trunk_slot - self.slot) == 1, "Client, Trunk cards must be in neighboring slots."
        else:
            self._trunk_slot = self.slot
        return self._trunk_slot

    def create_l1_ports(self) -> None:
        """Create Front and RearPorts per trunk/client mapping.

        This is only for Cisco OLS, Ciena Waveserver, and creates the ports as Front/RearPorts
        instead of interfaces. Front/RearPorts are being used because these are not really
        'endpoints' for circuits and function more like passthroughs. Using Front/Rearports:
            1. Allows Client ports to be 'mapped' to Trunk ports. This is not possible with
               Interface objects.
            2. Allows continuation of CablePaths that would otherwise end at an Interface.
               Continuing the CablePath helps with FreeForm exports. Using Interfaces would make
               the trunk side cabling more obtuse and likely lead to omission.

        Note that since optics are not tracked in Nautobot at this time, the optic types are not as relevant.
        RearPorts can safely be assumed to be LC/SMF, and FrontPorts are set to LC/SMF also for convenience.
        This is NOT expected to match reality, and the FreeForm export Job does not utilize these values.

        MR-MXPs have special handling. These are the exception and have Client ports in both the same slot as the
        trunk and an additional client in the neighbor card. This is specifically called out on the trunk_slots
        form variable.
        """
        for trunk_port, client_list in self.trunk_client_map.items():
            if self.mr_mxp:
                positions = 2
                trunk_slot = self.slot
            else:
                positions = len(client_list)
                trunk_slot = self.trunk_slot

            trunk = RearPort.objects.create(
                device=self.device,
                name=self.port_name.format(trunk_slot, trunk_port) + " Trunk",
                type="lc",
                positions=positions,
                custom_field_data={"jumper_type": "SMF"},
                description=self._MODULES[self.module]["trunk_description"],
            )
            self.wrap_save(trunk)

            if self.mr_mxp:
                fp = FrontPort.objects.create(
                    device=self.device,
                    name=self.port_name.format(self.trunk_slot, 1) + " Client",
                    type="sc",
                    rear_port=trunk,
                    rear_port_position=2,
                    custom_field_data={"jumper_type": "SMF"},
                    description=self._MODULES[self.module]["description"],
                )
                self.wrap_save(fp)

            for i, client_port in enumerate(client_list, start=1):
                fp = FrontPort.objects.create(
                    device=self.device,
                    name=self.port_name.format(self.slot, client_port) + " Client",
                    type="lc",
                    rear_port=trunk,
                    rear_port_position=i,
                    custom_field_data={"jumper_type": "SMF"},
                    description=self._MODULES[self.module]["description"],
                )
                self.wrap_save(fp)

    def create_l2_l3_ports(self) -> None:
        """Create Router/Switch ports as Interfaces. Not yet implemented."""
        pass

    @property
    def list_trunk_ports(self) -> list[int]:
        return list(self.trunk_client_map.keys())

    @property
    def list_client_ports(self) -> list[int]:
        return list({x for x in chain.from_iterable(self.trunk_client_map.values())})

    def generate_log(self) -> str:
        # fmt: off
        return (
            f"""Created ports as follows:  
                **Device**: `{self.device.name}`  
                **Device Type**: `{self.device.device_type}`    
                **Slot**: `{self.slot}`  
                **Module**: `{self.module}`    
                **Trunk Ports**: `{", ".join(map(str, self.list_trunk_ports))}`    
                **Trunk Description**: `{self._MODULES[self.module]["trunk_description"]}`    
                **Client Ports**: `{", ".join(map(str, self.list_client_ports))}`    
                **Client Description**: `{self._MODULES[self.module]["description"]}`    
                **Port Naming**: `{self.port_name}`    

                Note that a {self.module} was NOT created, only the Ports.
            """
        )
        # fmt: on


class CreateModule(Script):
    class Meta:
        name = "Modular Port Creation"
        description = "Create and add ports to devices with modular configurations."
        scheduling_enabled = False

    device_slugs = non_panels_slugs()
    device = ObjectVar(model=Device, query_params={"role": device_slugs})
    MODULE_CHOICES = tuple((i, i) for i in Module.list_modules())
    _module = ChoiceVar(label="Module", choices=MODULE_CHOICES)  # errors with name=module
    slot = IntegerVar(
        description="Slot for the new module",
        min_value=0,
        max_value=20,
    )
    trunk_slot = IntegerVar(
        label="Trunk/MR-MXP Slot",
        description="Slot number required for MXP configurations on Cisco OLS.",
        required=False,
        default=0,
        min_value=1,
        max_value=20,
    )

    def run(self, data, commit):
        def check_modular_device(device: Device) -> None:
            """Check that the DeviceType bool custom field 'Modular Device' is set."""
            msg = f"{device.name} is not a modular device - interfaces cannot be added."
            assert device.device_type.custom_field_data.get("modular_device"), msg

        def check_empty_slot(device: Device, slot: int, trunk_slot: int = None) -> None:
            """Check that the supplied slot is free on the device."""
            msg = f"{device.name} already has a module in Slot {slot}."
            front_slot_ports = FrontPort.objects.filter(device=device, name__contains=f"Slot {slot}")
            assert not front_slot_ports, msg

            if trunk_slot:
                rear_slot_ports = RearPort.objects.filter(device=device, name__contains=f"Slot {trunk_slot}")
                assert not rear_slot_ports, msg

        data["module"] = data.pop("_module")
        check_modular_device(data["device"])
        check_empty_slot(data["device"], data["slot"], data["trunk_slot"])

        module = Module(**data)
        module.create_l1_ports()
        log = module.generate_log()
        self.log_success(log)
