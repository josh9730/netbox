from typing import Final, TypeAlias

from circuits.choices import CircuitStatusChoices
from circuits.models import Circuit, CircuitTermination, CircuitType, Provider
from dcim.choices import CableTypeChoices, LinkStatusChoices
from dcim.models import Device, DeviceRole, FrontPort, Interface, Rack, RearPort, Site
from extras.models import Tag
from extras.scripts import ChoiceVar, IntegerVar, ObjectVar, Script, StringVar
from tenancy.models import Tenant
from utilities.exceptions import AbortScript

from scripts.common import utils

name = "Jumper Creations"

Ports: TypeAlias = FrontPort | Interface | RearPort

PANEL_ROLE: Final = DeviceRole.objects.get(slug="modular-panels")
MODULAR_TAG: Final = "Modular Trunk"
MODULAR_TAG_ID: Final = Tag.objects.get(slug="modular-trunk").id
XCONNECT_ROLE: Final = "xconnect-panels"
CROSS_CONNECT: Final = "Cross Connect"


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
            """Only one of Interface/FrontPort/RearPort should be selected for A and Z Side."""

            def check_pairs(port_1: ObjectVar, port_2: ObjectVar, port_3: ObjectVar, side: str) -> Ports:
                msg = "Select **one** of `{} FrontPort / Interface | RearPort`."
                ports_input = [i for i in [port_1, port_2, port_3] if i]
                if len(ports_input) > 1:
                    raise AbortScript(msg.format(side))
                return ports_input[0]

            a_port = check_pairs(data["a_interface"], data["a_frontport"], data["a_rearport"], "A")
            z_port = check_pairs(data["z_interface"], data["z_frontport"], data["z_rearport"], "Z")
            return a_port, z_port

        rack_1_port, rack_2_port = check_port_selections(data)

        # Intra-rack connection
        if data["a_device"].rack == data["z_device"].rack:
            cable = utils.CableRunner.create_cable_single(
                rack_1_port,
                rack_2_port,
                data["clr"],
                data["status"],
                data["cable_type"],
            )
            output = utils.CableRunner.create_cable_log(cable)
            self.log_success(output)

        # Inter-rack connection
        else:
            runner = utils.CableRunner(
                rack_1_port,
                rack_2_port,
                data["clr"],
                data["status"],
                data["cable_type"],
            )
            runner.get_connections()
            cables = runner.create_cables()
            for cable in cables:
                output = utils.CableRunner.create_cable_log(cable)
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

        panel_2 = get_remote_panel(data["panel_1"])
        panel_2_port = panel_2.rearports.get(name=data["rp_1"].name)

        output = utils.create_modular_trunk(data["panel_1"], data["rp_1"], panel_2, panel_2_port, data["status"])
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
                f"""Cross Connect Handoff Info:  
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
        utils.wrap_save(circuit)

        circuit_term = CircuitTermination(
            circuit=circuit,
            term_side="A",
            site=data["site"],
            xconnect_id=data["circuit_id"],
            pp_info=f'Panel: {data["xconnect_panel"]}, Port: {data["xcpanel_port"]}',
            description=data["description"],
        )
        utils.wrap_save(circuit_term)

        cable_type = panel_type(data["xconnect_panel"].device_type.slug.split("-")[-1])
        _ = utils.CableRunner.create_cable_single(
            data["xcpanel_port"],
            circuit_term,
            data["clr"],
            data["status"],
            cable_type,
        )

        cage, rack_id = get_rack_info(data["xconnect_panel"].rack)
        xconn_log = xconnect_log(data, cage, rack_id, cable_type)
        self.log_success(xconn_log)
