from typing import Final, TypeAlias

from dcim.choices import CableTypeChoices, LinkStatusChoices
from dcim.models import Cable, Device, DeviceRole, FrontPort, Interface, RearPort
from extras.models import Tag
from utilities.exceptions import AbortScript

Ports: TypeAlias = FrontPort | Interface | RearPort

PANEL_ROLE: Final = DeviceRole.objects.get(slug="modular-panels")
MODULAR_TAG_ID: Final = Tag.objects.get(slug="modular-trunk").id
MODULAR_TAG: Final = "Modular Trunk"


def wrap_save(obj) -> None:
    """Wrapper for saving new objects."""
    obj.full_clean()
    obj.save()


def create_modular_trunk(
    panel_1: Device, port_1: Ports, panel_2: Device, port_2: Ports, status: LinkStatusChoices
) -> str:
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

    cable_id = next_cable_id()
    cable = CableRunner.create_cable_single(
        port_1,
        port_2,
        f"COM--{panel_1}--{panel_2}--{cable_id}",
        status,
    )
    cable.tags.add(MODULAR_TAG)
    return CableRunner.create_cable_log(cable)


class CableRunner:
    """Create 1-3 cables between two devices."""

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
        """Wrapper to create multiple cables based on the connection list."""
        cables = []
        for connection in self.connections:
            self.data.update({"port_1": connection[0], "port_2": connection[1]})
            cables.append(self.create_cable_single(**self.data))
        return cables
