from dcim.choices import LinkStatusChoices, CableTypeChoices
from dcim.models import Device, FrontPort, Interface, Site, Cable, DeviceRole, Rack
from extras.scripts import ChoiceVar, ObjectVar, Script, StringVar
from utilities.exceptions import AbortScript

from typing import Final

name = "Jumper Creations"


class CableRunner:
    PANEL_ROLE: Final = DeviceRole.objects.get(slug="modular-panels")
    rack_1_free_modular_ports: list[FrontPort] = None
    rack_2_free_modular_ports: list[FrontPort] = None
    connections: list[list[FrontPort | Interface]] = None

    def __init__(
        self,
        rack_1: Rack,
        rack_1_port: FrontPort | Interface,
        rack_2: Rack,
        rack_2_port: FrontPort | Interface,
        clr: str,
        status: LinkStatusChoices = LinkStatusChoices.STATUS_CONNECTED,
        cable_type: CableTypeChoices = CableTypeChoices.TYPE_SMF_OS2,
    ) -> None:
        self.rack_1 = rack_1
        self.rack_1_port = rack_1_port
        self.rack_2 = rack_2
        self.rack_2_port = rack_2_port

        self.data = {'clr': clr, 'status': status, 'cable_type': cable_type}
        self._find_free_local_ports()

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
    def create_cable_single(
        port_1: FrontPort | Interface,
        port_2: FrontPort | Interface,
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

    def _validate_free_ports(self, ports: list[FrontPort]) -> list[FrontPort]:
        """Validate all locally free ports are also free on the remote side.

        - Find the remote_panel, if ValueError, then the two panels are not cabled
        - validate port is uncabled, and return new list of valid free ports

        Note the returned list is in the form of list[list[LOCALPORT, REMOTEPORT]], where local indicates the
        rack that was checked.
        """
        valid_remote_ports = []
        for port in ports:
            try:
                remote_panel = port.rear_port.link_peers[0].device
            except ValueError:  # rear_port not connected
                continue

            remote_port = FrontPort.objects.get(device=remote_panel, rear_port_position=port.rear_port_position)
            if not remote_port.cable:
                valid_remote_ports.append([port, remote_port])

        if not valid_remote_ports:
            raise AbortScript('No valid modular ports found.')
        return valid_remote_ports

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

        def filter_ports_direct_connect(ports: list[list[FrontPort]]) -> None | list[FrontPort]:
            """Check if remote ports are on rack_2, i.e. Rack1 and Rack2 are directly connected."""
            direct_connect_ports = [p for p in ports if p[1].device.rack == self.rack_2]
            if direct_connect_ports:
                return direct_connect_ports[0]
            else:
                None

        rack_1_panel_ports = self._validate_free_ports(self.rack_1_free_modular_ports)

        direct_ports = filter_ports_direct_connect(rack_1_panel_ports)

        # SPK-SPK or SPK-HUB
        if direct_ports:
            self.connections = [[self.rack_1_port, direct_ports[0]], [self.rack_2_port, direct_ports[1]]]

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
            self.data.update({'port_1': connection[0], 'port_2': connection[1]})
            cables.append(self.create_cable_single(**self.data))
        return cables


class FormFields:
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
        description="One of A-Interface or A-FrontPort must be selected.",
        required=False,
        query_params={"device_id": "$a_device", 'cabled': False},
    )
    a_frontport = ObjectVar(
        model=FrontPort,
        label="A-FrontPort/Optical",
        description="One of A-Interface or A-FrontPort must be selected.",
        required=False,
        query_params={"device_id": "$a_device", 'cabled': False},
    )

    z_device = ObjectVar(
        model=Device,
        label="Z-Device",
        query_params={"site_id": "$site"},
    )
    z_interface = ObjectVar(
        model=Interface,
        label="Z-Interface",
        description="One of Z-Interface or Z-FrontPort must be selected.",
        required=False,
        query_params={"device_id": "$z_device", 'cabled': False},
    )
    z_frontport = ObjectVar(
        model=FrontPort,
        label="Z-FrontPort|Optical",
        description="One of Z-Interface or Z-FrontPort must be selected.",
        required=False,
        query_params={"device_id": "$z_device", 'cabled': False},
    )
    status = ChoiceVar(
        label="Cable Status",
        choices=LinkStatusChoices,
        default=LinkStatusChoices.STATUS_CONNECTED,
    )
    clr = StringVar(label="CLR", description="Value should match `CLR-XXXX` or `nonprod STRING`.", default="CLR-1234")
    cable_type = ChoiceVar(
        label="Cable Type",
        choices=CableTypeChoices,
        default=CableTypeChoices.TYPE_SMF_OS2,
    )


class JumperMixins:
    @staticmethod
    def wrap_save(obj):
        obj.full_clean()
        obj.save()

    @staticmethod
    def check_port_selections(data: dict) -> None:
        """Only one of Interface/FrontPort should be selected for A and Z Side."""

        def check_pairs(port_1: ObjectVar, port_2: ObjectVar, side: str) -> None:
            msg = "Select **one** of `{} FrontPort / Interface`."
            if all((port_1, port_2)):
                raise AbortScript(msg.format(side))
            if not any((port_1, port_2)):
                raise AbortScript(msg.format(side))

        check_pairs(data['a_interface'], data['a_frontport'], "A")
        check_pairs(data['z_interface'], data['z_frontport'], "Z")

    @staticmethod
    def get_valid_ports(data: dict) -> tuple[[FrontPort | Interface], [FrontPort | Interface]]:
        def find_port(side: str) -> FrontPort | Interface:
            return data[f"{side}_interface"] if data[f"{side}_interface"] else data[f"{side}_frontport"]

        return find_port('a'), find_port('z')


class NewJumper(Script, JumperMixins):
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
    z_device = FormFields.z_device
    z_interface = FormFields.z_interface
    z_frontport = FormFields.z_frontport
    status = FormFields.status
    clr = FormFields.clr
    cable_type = FormFields.cable_type

    def run(self, data, commit):
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

        self.check_port_selections(data)
        rack_1_port, rack_2_port = self.get_valid_ports(data)

        # Intra-rack connection
        if data['a_device'].rack == data['z_device'].rack:
            cable = CableRunner.create_cable_single(
                rack_1_port,
                rack_2_port,
                data['clr'],
                data['status'],
                data['cable_type'],
            )
            create_cable_log(data, cable)

        # Inter-rack connection
        else:
            runner = CableRunner(
                data['a_device'].rack,
                rack_1_port,
                data['z_device'].rack,
                rack_2_port,
                data['clr'],
                data['status'],
                data['cable_type'],
            )
            runner.get_connections()
            cables = runner.create_cables()
            for cable in cables:
                create_cable_log(data, cable)
