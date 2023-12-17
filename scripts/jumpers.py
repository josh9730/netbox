from typing import Final, TypeAlias

from circuits.choices import CircuitStatusChoices
from circuits.models import Circuit, CircuitTermination, CircuitType, Provider
from dcim.choices import CableTypeChoices, LinkStatusChoices
from dcim.models import Cable, Device, DeviceRole, FrontPort, Interface, Rack, RearPort, Site
from extras.models import Tag
from extras.scripts import ChoiceVar, IntegerVar, ObjectVar, Script, StringVar
from tenancy.models import Tenant
from utilities.exceptions import AbortScript

name = "Jumper Creations"

Ports: TypeAlias = FrontPort | Interface | RearPort

PANEL_ROLE: Final = DeviceRole.objects.get(slug="modular-panels")
MODULAR_TAG: Final = "Modular Trunk"
MODULAR_TAG_ID: Final = Tag.objects.get(slug="modular-trunk").id
XCONNECT_ROLE: Final = "xconnect-panels"
CROSS_CONNECT: Final = "Cross Connect"


def wrap_save(obj) -> None:
    obj.full_clean()
    obj.save()


class CableRunner:
    rack_1_free_modular_ports: list[FrontPort]
    rack_2_free_modular_ports: list[FrontPort]
    connections: list[list[Ports]]

    def __init__(
        self,
        rack_1_port: Ports,
        rack_2_port: Ports,
        clr: str,
        status: LinkStatusChoices = LinkStatusChoices.STATUS_CONNECTED,
        cable_type: CableTypeChoices = CableTypeChoices.TYPE_SMF_OS2,
    ) -> None:
        self.rack_1 = rack_1_port.device.rack
        self.rack_1_port = rack_1_port
        self.rack_2 = rack_2_port.device.rack
        self.rack_2_port = rack_2_port
        self.data = {"clr": clr, "status": status, "cable_type": cable_type}

    @staticmethod
    def create_cable_single(
        port_1: Ports,
        port_2: Ports,
        clr: str,
        status: LinkStatusChoices = LinkStatusChoices.STATUS_CONNECTED,
        cable_type: CableTypeChoices = CableTypeChoices.TYPE_SMF_OS2,
        length: int = 0,
    ) -> Cable:
        cable = Cable(
            type=cable_type,
            a_terminations=[port_1],
            b_terminations=[port_2],
            status=status,
            label=clr,
            length=length,
            length_unit="m",
        )
        wrap_save(cable)
        return cable

    @staticmethod
    def create_cable_log(cable: Cable) -> str:
        # fmt: off
        return (
            f"""Created Cable  
                **Site**: `{cable.a_terminations[0].device.site}`  
                **A Rack**: `{cable.a_terminations[0].device.rack}`  
                **A Device**: `{cable.a_terminations[0].device}`  
                **A Port**: `{cable.a_terminations[0].name}`  
                **Label**: `{cable.label}`  
                **Z Rack**: `{cable.b_terminations[0].device.rack}`  
                **Z Device**: `{cable.b_terminations[0].device}`  
                **Z Port**: `{cable.b_terminations[0].name}`  
                """
        )
        # fmt: on

    def _find_free_local_ports(self) -> None:
        """Get all free modular panel ports for each rack."""
        self.rack_1_free_modular_ports = list(
            FrontPort.objects.filter(device__role=PANEL_ROLE, device__rack=self.rack_1, cable=None)
        )
        self.rack_2_free_modular_ports = list(
            FrontPort.objects.filter(device__role=PANEL_ROLE, device__rack=self.rack_2, cable=None)
        )
        if not all((self.rack_1_free_modular_ports, self.rack_1_free_modular_ports)):
            raise AbortScript("No free modular panel ports found between the selected racks.")

    @staticmethod
    def _validate_free_ports(ports: list[FrontPort]) -> list[FrontPort]:
        """Validate all locally free ports are also free on the remote side.

        - Find the remote_panel, if ValueError, then the two panels are not cabled
        - validate port is uncabled, and return new list of valid free ports

        Note the returned list is in the form of list[list[LOCALPORT, REMOTEPORT]], where local indicates the
        rack that was checked.
        """
        valid_ports = []
        for port in ports:
            try:
                remote_panel = port.rear_port.link_peers[0].device
            except ValueError:  # rear_port not connected
                continue

            remote_port = FrontPort.objects.get(device=remote_panel, rear_port_position=port.rear_port_position)
            if not remote_port.cable:
                valid_ports.append([port, remote_port])

        if not valid_ports:
            raise AbortScript("No valid modular ports found.")
        return valid_ports

    def get_connections(self) -> None:
        """Get valid connections, where the connections are a list of endpoints that will be passed directly
        to cable creation.

        - Valid and free ports are first retrieved for Rack1
        - Then all of Rack1's valid ports are checked to see if any ports directly connect to Rack2
            - i.e. SPK-SPK or SPK-HUB
            - if so, then Rack2's free ports are not checked, and the connection object is created
        - If Rack1 is not directly connected to Rack2, then this is SPK-HUB-SPK
            - Rack2's free and valid ports are retrived (but not checked for connectivity to Rack1)
            - the connection object is then created

        Note that this depends on _validate_free_ports() returning each list of ports in a specific order.

        SPK-SPK & SPK-HUB will have a connection object with a length of two.
        SPK-HUB-SPK will be a length of three.
        """

        def filter_ports_direct_connect(
            ports: list[list[FrontPort]],
        ) -> None | list[FrontPort]:
            """Check if remote ports are on rack_2, i.e. Rack1 and Rack2 are directly connected."""
            direct_connect_ports = [p for p in ports if p[1].device.rack == self.rack_2]
            if direct_connect_ports:
                return direct_connect_ports[0]
            else:
                return None

        self._find_free_local_ports()
        rack_1_panel_ports = self._validate_free_ports(self.rack_1_free_modular_ports)
        direct_ports = filter_ports_direct_connect(rack_1_panel_ports)

        # SPK-SPK or SPK-HUB
        if direct_ports:
            self.connections = [
                [self.rack_1_port, direct_ports[0]],
                [self.rack_2_port, direct_ports[1]],
            ]

        # SPK-HUB-SPK
        else:
            rack_1_panel_ports = rack_1_panel_ports[0]
            rack_2_panel_ports = self._validate_free_ports(self.rack_2_free_modular_ports)[0]
            self.connections = [
                [self.rack_1_port, rack_1_panel_ports[0]],
                [rack_1_panel_ports[1], rack_2_panel_ports[1]],
                [self.rack_2_port, rack_2_panel_ports[0]],
            ]

    def create_cables(self) -> list[Cable]:
        cables = []
        for connection in self.connections:
            self.data.update({"port_1": connection[0], "port_2": connection[1]})
            cables.append(self.create_cable_single(**self.data))
        return cables


class NewJumper(Script):
    class Meta:
        name = "New Jumpers"
        description = (
            "Create new cabling between two endpoints within one rack, or between two racks using modular paneling."
        )
        scheduling_enabled = False
        fieldsets = (
            ("Cable Data", ("site", "status", "clr", "cable_type")),
            ("A Side", ("a_device", "a_interface", "a_frontport", "a_rearport")),
            ("Z Side", ("z_device", "z_interface", "z_frontport", "z_rearport")),
        )

    port_description = "One of Interface, FrontPort, or RearPort must be selected."
    site = ObjectVar(model=Site)

    a_device = ObjectVar(model=Device, label="Device", query_params={"site_id": "$site"})
    a_dev_common = {
        "description": port_description,
        "required": False,
        "query_params": {"device_id": "$a_device", "cabled": False},
    }
    a_interface = ObjectVar(model=Interface, label="Interface", **a_dev_common)
    a_frontport = ObjectVar(model=FrontPort, label="FrontPort/Optical Client", **a_dev_common)
    a_rearport = ObjectVar(model=RearPort, label="RearPort/Optical Trunk", **a_dev_common)

    z_device = ObjectVar(model=Device, label="Device", query_params={"site_id": "$site"})
    z_dev_common = {
        "description": port_description.format,
        "required": False,
        "query_params": {"device_id": "$z_device", "cabled": False},
    }
    z_interface = ObjectVar(model=Interface, label="Interface", **z_dev_common)
    z_frontport = ObjectVar(model=FrontPort, label="FrontPort|Optical", **z_dev_common)
    z_rearport = ObjectVar(model=RearPort, label="RearPort/Optical Trunk", **z_dev_common)

    status = ChoiceVar(label="Cable Status", choices=LinkStatusChoices, default=LinkStatusChoices.STATUS_CONNECTED)
    clr = StringVar(label="CLR", description="Value should match `CLR-XXXX` or `nonprod STRING`.")
    cable_type = ChoiceVar(label="Cable Type", choices=CableTypeChoices, default=CableTypeChoices.TYPE_SMF_OS2)

    def run(self, data, commit):
        def check_port_selections(data: dict) -> tuple[Ports, Ports]:
            """Only one of Interface/FrontPort should be selected for A and Z Side."""

            def check_pairs(port_1: ObjectVar, port_2: ObjectVar, port_3: ObjectVar, side: str) -> Ports:
                msg = "Select **one** of `{} FrontPort / Interface | RearPort`."
                ports_input = [i for i in [port_1, port_2, port_3] if i]
                if len(ports_input) > 1:
                    raise AbortScript(msg.format(side))
                return ports_input[0]

            a_port = check_pairs(data["a_interface"], data["a_frontport"], data["a_rearport"], "A")
            z_port = check_pairs(data["z_interface"], data["z_frontport"], data["a_rearport"], "Z")
            return a_port, z_port

        rack_1_port, rack_2_port = check_port_selections(data)

        # Intra-rack connection
        if data["a_device"].rack == data["z_device"].rack:
            cable = CableRunner.create_cable_single(
                rack_1_port,
                rack_2_port,
                data["clr"],
                data["status"],
                data["cable_type"],
            )
            output = CableRunner.create_cable_log(cable)
            self.log_success(output)

        # Inter-rack connection
        else:
            runner = CableRunner(
                rack_1_port,
                rack_2_port,
                data["clr"],
                data["status"],
                data["cable_type"],
            )
            runner.get_connections()
            cables = runner.create_cables()
            for cable in cables:
                output = CableRunner.create_cable_log(cable)
                self.log_success(output)


class CreatePanelTrunks(Script):
    class Meta:
        name = "New FHD Trunk"
        description = "Create trunk cables between two modular panels."
        scheduling_enabled = False

    site = ObjectVar(label="Site Name", model=Site)
    panel_1 = ObjectVar(
        label="Modular Panel",
        description="Select one of the paired panels",
        model=Device,
        query_params={"rack_id": "$rack_1", "role_id": PANEL_ROLE.id},
    )
    rp_1 = ObjectVar(
        label="Rear Port",
        description="Select the RearPort",
        model=RearPort,
        query_params={"device_id": "$panel_1", "cabled": False},
    )
    length = IntegerVar(
        label="Cable Length", description="Cable length, in meters", required=False, max_value=100, min_value=1
    )
    status = ChoiceVar(
        label="Cable Status", required=False, choices=LinkStatusChoices, default=LinkStatusChoices.STATUS_CONNECTED
    )

    def run(self, data, commit):
        def get_remote_panel(panel: Device) -> Device:
            """Return remote_panel Device."""
            remote_panel_id = panel.custom_field_data["remote_panel"]
            return Device.objects.get(id=remote_panel_id)

        def next_cable_id() -> str:
            """Retrieve all modular trunk cables by Tag, and return the next label ID.

            All modular trunk cables are expected to be tagged with the modular-trunk tag. All labels
            matching this tag are retrieved and sorted, then the next ID is returned using rjust.
            """
            cables = Cable.objects.filter(tags=MODULAR_TAG_ID)
            try:
                last_cable_label = sorted([i.label.split("--")[-1] for i in cables])[-1]
            except IndexError:
                return "C0001"
            else:
                last_id = last_cable_label[1:]
                return "C" + str(int(last_id) + 1).rjust(4, "0")

        panel_2 = get_remote_panel(data["panel_1"])
        panel_2_port = panel_2.rearports.get(name=data["rp_1"].name)

        cable_id = next_cable_id()
        cable = CableRunner.create_cable_single(
            data["rp_1"],
            panel_2_port,
            f"COM--{data['panel_1']}--{panel_2}--{cable_id}",
            data["status"],
        )
        cable.tags.add(MODULAR_TAG)
        output = CableRunner.create_cable_log(cable)
        self.log_success(output)


class NewCrossConnect(Script):
    class Meta:
        name = "New Cross Connect"
        description = "Create a new Cross Connect object and terminate to panel"
        scheduling_enabled = False
        fieldsets = (
            (
                "Cross Connect",
                ("provider", "circuit_id", "circuit_billing", "tenant", "ticket", "description", "status"),
            ),
            ("Panel Termination", ("site", "xconnect_panel", "xcpanel_port", "clr")),
        )

    site = ObjectVar(model=Site)
    provider = ObjectVar(label="Cross Connect Provider", model=Provider)
    circuit_id = StringVar(label="Cross Connect ID", description="Cross Connect Serial/Order ID, or ticket")
    clr = StringVar(label="CLR", description="Value should match `CLR-XXXX` or `nonprod STRING`.")
    ticket = StringVar()
    status = ChoiceVar(choices=CircuitStatusChoices, default=CircuitStatusChoices.STATUS_PLANNED)
    tenant = ObjectVar(label="Segment", model=Tenant)

    circuit_billing = StringVar(label="Billing/Order ID", required=False)
    description = StringVar(label="Description")
    xconnect_panel = ObjectVar(
        label="Cross Connect Panel", model=Device, query_params={"site_id": "$site", "role": XCONNECT_ROLE}
    )
    xcpanel_port = ObjectVar(
        label="Cross Connect Rear Port", model=RearPort, query_params={"device_id": "$xconnect_panel", "cabled": False}
    )

    def run(self, data, commit):
        def check_empty_frontport(rp: RearPort) -> None:
            """Check if the FrontPort for the given RearPort is in use.

            Note that the CLRs may not match, so there's no real way to enforce anything.
            """
            fp = FrontPort.objects.filter(rear_port=rp)[0]  # only one FP per RP for this panel type
            if fp.cable:
                self.log_warning(f"The selected RearPort is being used for `{fp.cable}`. Verify that this is expected!")

        def get_rack_info(rack: Rack) -> tuple[str, str]:
            """Fetch Cage/Suite name if present."""
            row = rack.location
            cage = row.parent.name if row.parent else ""
            rack_cf = rack.custom_field_data

            if rack.facility_id:
                rack_id = rack.facility_id
                rack_id += f"/ {rack_cf['billing_id']}" if rack_cf.get("billing_id") else ""
                rack_id += f"/ {rack_cf['space_id']}" if rack_cf.get("space_id") else ""
            else:
                rack_id = rack.name

            return cage, rack_id

        def panel_type(panel_type: str) -> str:
            """Find valid cable_type from end of the device_type slug."""
            match panel_type:
                case "os2":
                    return "smf-os2"
                case "om4":
                    return "mmf-om4"

        def xconnect_log(data: dict, cage: str, rack_identifier: str, cable_type: str) -> str:
            # fmt: off
            return (
                f"""Created Cable  
                    **Site**: `{data['site']}`  
                    **Cage**: `{cage}`  
                    **Rack**: `{rack_identifier}`  
                    **Device**: `{data['xconnect_panel']}`  
                    **Port**: `{data['xcpanel_port']}`  
                    **Port Type**: `{data['xcpanel_port'].type.upper()}`  
                    **Cable Type**: `{cable_type}`  
                    """
            )
            # fmt: on

        check_empty_frontport(data["xcpanel_port"])

        circuit = Circuit(
            cid=data["circuit_id"],
            provider=data["provider"],
            type=CircuitType.objects.get(name=CROSS_CONNECT),
            status=data["status"],
            description=data["description"],
            custom_field_data={
                "circuit_ticket": data["ticket"],
                "circuit_billing": data.get("circuit_billing"),
            },
            tenant=data["tenant"],
        )
        wrap_save(circuit)

        circuit_term = CircuitTermination(
            circuit=circuit,
            term_side="A",
            site=data["site"],
            xconnect_id=data["circuit_id"],
            pp_info=f'Panel: {data["xconnect_panel"]}, Port: {data["xcpanel_port"]}',
            description=data["description"],
        )
        wrap_save(circuit_term)

        cable_type = panel_type(data["xconnect_panel"].device_type.slug.split("-")[-1])
        self.log_info(cable_type)

        _ = CableRunner.create_cable_single(
            data["xcpanel_port"],
            circuit_term,
            data["clr"],
            data["status"],
            cable_type,
        )

        cage, rack_id = get_rack_info(data["xconnect_panel"].rack)
        xconn_log = xconnect_log(data, cage, rack_id, cable_type)
        self.log_success(xconn_log)
