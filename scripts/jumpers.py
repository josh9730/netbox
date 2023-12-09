from typing import Final, TypeAlias

from dcim.choices import CableTypeChoices, LinkStatusChoices
from dcim.models import Cable, Device, DeviceRole, FrontPort, Interface, RearPort, Site
from extras.scripts import ChoiceVar, ObjectVar, Script, StringVar
from utilities.exceptions import AbortScript

name = "Jumper Creations"

Ports: TypeAlias = FrontPort | Interface | RearPort


class CableRunner:
    PANEL_ROLE: Final = DeviceRole.objects.get(slug="modular-panels")
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
    ) -> Cable:
        cable = Cable(
            type=cable_type,
            a_terminations=[port_1],
            b_terminations=[port_2],
            status=status,
            label=clr,
        )
        cable.full_clean()
        cable.save()
        return cable

    def _find_free_local_ports(self) -> None:
        """Get all free modular panel ports for each rack."""
        self.rack_1_free_modular_ports = list(
            FrontPort.objects.filter(device__role=self.PANEL_ROLE, device__rack=self.rack_1, cable=None)
        )
        self.rack_2_free_modular_ports = list(
            FrontPort.objects.filter(device__role=self.PANEL_ROLE, device__rack=self.rack_2, cable=None)
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


class FormFields:
    port_description = "One of {}-Side Interface, FrontPort, or RearPort must be selected."
    site = ObjectVar(
        model=Site,
    )
    a_device = ObjectVar(
        model=Device,
        label="A-Device",
        query_params={"site_id": "$site"},
    )
    a_interface = ObjectVar(
        model=Interface,
        label="A-Interface",
        description=port_description.format("A"),
        required=False,
        query_params={"device_id": "$a_device", "cabled": False},
    )
    a_frontport = ObjectVar(
        model=FrontPort,
        label="A-FrontPort/Optical Client",
        description=port_description.format("A"),
        required=False,
        query_params={"device_id": "$a_device", "cabled": False},
    )
    a_rearport = ObjectVar(
        model=RearPort,
        label="A-RearPort/Optical Trunk",
        description=port_description.format("A"),
        required=False,
        query_params={"device_id": "$a_device", "cabled": False},
    )

    z_device = ObjectVar(
        model=Device,
        label="Z-Device",
        query_params={"site_id": "$site"},
    )
    z_interface = ObjectVar(
        model=Interface,
        label="Z-Interface",
        description=port_description.format("Z"),
        required=False,
        query_params={"device_id": "$z_device", "cabled": False},
    )
    z_frontport = ObjectVar(
        model=FrontPort,
        label="Z-FrontPort|Optical",
        description=port_description.format("Z"),
        required=False,
        query_params={"device_id": "$z_device", "cabled": False},
    )
    z_rearport = ObjectVar(
        model=RearPort,
        label="Z-RearPort/Optical Trunk",
        description=port_description.format("A"),
        required=False,
        query_params={"device_id": "$z_device", "cabled": False},
    )

    status = ChoiceVar(
        label="Cable Status",
        choices=LinkStatusChoices,
        default=LinkStatusChoices.STATUS_CONNECTED,
    )
    clr = StringVar(
        label="CLR",
        description="Value should match `CLR-XXXX` or `nonprod STRING`.",
    )
    cable_type = ChoiceVar(
        label="Cable Type",
        choices=CableTypeChoices,
        default=CableTypeChoices.TYPE_SMF_OS2,
    )


class NewJumper(Script):
    class Meta:
        name = "Create Jumpers"
        description = (
            "Create new cabling between two endpoints within one rack, or between two racks using modular paneling."
        )
        scheduling_enabled = False

    site = FormFields.site
    a_device = FormFields.a_device
    a_interface = FormFields.a_interface
    a_frontport = FormFields.a_frontport
    a_rearport = FormFields.a_rearport
    z_device = FormFields.z_device
    z_interface = FormFields.z_interface
    z_frontport = FormFields.z_frontport
    z_rearport = FormFields.z_rearport
    status = FormFields.status
    clr = FormFields.clr
    cable_type = FormFields.cable_type

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

        def create_cable_log(data, cable: Cable) -> None:
            # fmt: off
            self.log_success(
                f"""Created Cable  
                    **Site**: `{data['site']}`  
                    **A Rack**: `{data['a_device'].rack}`  
                    **A Device**: `{cable.a_terminations[0].device}`  
                    **A Port**: `{cable.a_terminations[0].name}`  
                    **Label**: `{cable.label}`  
                    **Z Rack**: `{data['z_device'].rack}`  
                    **Z Device**: `{cable.b_terminations[0].device}`  
                    **Z Port**: `{cable.b_terminations[0].name}`  
                    """
            )
            # fmt: on

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
            create_cable_log(data, cable)

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
                create_cable_log(data, cable)
