from dcim.choices import DeviceStatusChoices
from dcim.models import Device, FrontPort, Interface, Site
from extras.scripts import ChoiceVar, IntegerVar, ObjectVar, Script
from utilities.exceptions import AbortScript

name = "Jumper Creations"


class NewJumper(Script):
    class Meta:
        name = "New Jumpers"
        description = ""
        scheduling_enabled = False

    site = ObjectVar(
        model=Site,
    )

    a_device = ObjectVar(
        model=Device,
        query_params={"site_id": "$site"},
    )
    a_interface = ObjectVar(
        model=Interface,
        required=False,
        query_params={"device_id": "$device"},
    )
    a_frontport = ObjectVar(
        model=FrontPort,
        required=False,
        query_params={"device_id": "$device"},
    )

    z_device = ObjectVar(
        model=Device,
        query_params={"site_id": "$site"},
    )
    z_interface = ObjectVar(
        model=Interface,
        required=False,
        query_params={"device_id": "$device"},
    )
    z_frontport = ObjectVar(
        model=FrontPort,
        required=False,
        query_params={"device_id": "$device"},
    )
    status = ChoiceVar(label="Cable Status", choices=DeviceStatusChoices)
    clr = IntegerVar(label="CLR")

    def check_port_selections(self, data: dict):
        """Only one of Interface/FrontPort should be selected for A and Z Side."""
        if all((data['a_interface'], data['z_interface'])) or all((data['a_frontport'], data['z_frontport'])):
            raise AbortScript("Do not select both Interface and FrontPort for A/Z Device.")

    def run(self, data, commit):
        """

        jumper CLR should be Int or start with 'nonprod'

        X check a_interfaces and a_frontport only have one set (same for z)
        - If not same rack
            - get modular panels in a_device.rack and z_device.rack
            - check available ports
            - check if a_dev.rack.remote == z_dev.rack
            -
        """
        self.check_port_selections(data)

        if data['a_device'].rack == data['z_device'].rack:
            ...
        else:
            ...
