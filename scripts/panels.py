from dcim.models import Rack, Site, Device, DeviceType, DeviceRole, DeviceBay, RearPort, FrontPort, Cable
from tenancy.models import Tenant
from extras.models import Tag
from extras.scripts import IntegerVar, ObjectVar, Script, BooleanVar, ChoiceVar
from dcim.choices import DeviceStatusChoices, PortTypeChoices, LinkStatusChoices, CableTypeChoices
from typing import Final

name = "Panel Creations"

HUBSITE_TENANT: Final = Tenant.objects.get(name="CENIC Hubsite")
ENCLOSURE: Final = DeviceType.objects.get(model="FHD Enclosure, Blank")
PANEL_ROLE: Final = DeviceRole.objects.get(slug="modular-panels")
CASSETTE_ROLE: Final = DeviceRole.objects.get(slug="modular-panel-cassettes")
MODULAR_TAG_ID: Final = Tag.objects.get(slug='modular-trunk').id
MODULAR_TAG: Final = 'Modular Trunk'


class FormFields:
    site = ObjectVar(
        label="Site Name",
        model=Site,
    )
    rack_1 = ObjectVar(
        label="Rack A",
        model=Rack,
        query_params={
            "site_id": "$site",
        },
    )
    rack_1_position = IntegerVar(
        label="Rack A Position",
        description="Lowest RU filled by the new panel",
        min_value=1,
        max_value=44,
    )
    rack_2 = ObjectVar(
        label="Rack B",
        model=Rack,
        query_params={
            "site_id": "$site",
        },
    )
    rack_2_position = IntegerVar(
        label="Rack B Position",
        description="Lowest RU filled by the new panel",
        min_value=1,
        max_value=44,
    )
    panel_1 = ObjectVar(
        label="Panel",
        description="SPK/SS Panel containing **Type A** Cassette",
        model=Device,
        query_params={
            "rack_id": "$rack_1",
            "role_id": PANEL_ROLE.id,
        },
    )
    slot = IntegerVar(
        label="Slot Number",
        min_value=1,
        max_value=4,
    )
    run_jumpers = BooleanVar(
        label="Run Jumpers?",
        default=True,
        description="Optionally create max number of 'trunk' jumpers between the two cassettes.",
    )
    length = IntegerVar(
        label='Cable Length',
        description="Cable length, in meters.",
        required=False,
        max_value=100,
        min_value=1,
    )
    cable_status = ChoiceVar(
        label="Cable Status",
        required=False,
        choices=LinkStatusChoices,
        default=LinkStatusChoices.STATUS_CONNECTED,
    )
    device_status = ChoiceVar(
        label="Install Status",
        required=False,
        choices=DeviceStatusChoices,
        default=DeviceStatusChoices.STATUS_ACTIVE,
    )


class PanelsMixins:
    @staticmethod
    def wrap_save(obj):
        obj.full_clean()
        obj.save()

    @staticmethod
    def hub_spoke_check(data: dict) -> bool:
        """If any rack is the Hub, then the panel names are HUB-/SPK-. Else, names are SS-."""
        if any(
            (
                data["rack_1"].custom_field_data["hub_rack"],
                data["rack_2"].custom_field_data["hub_rack"],
            )
        ):
            return True
        else:
            return False

    @staticmethod
    def create_remote_panel_field(panels: list) -> None:
        """Create link between the new panels using the remote_panel custom field."""
        panels[0].custom_field_data['remote_panel'] = panels[1].id
        panels[1].custom_field_data['remote_panel'] = panels[0].id
        panels[0].save()
        panels[1].save()

    @staticmethod
    def get_remote_panel(panel: Device) -> Device:
        """Return remote_panel Device."""
        remote_panel_id = panel.custom_field_data['remote_panel']
        return Device.objects.get(id=remote_panel_id)

    @staticmethod
    def next_cable_id() -> str:
        """Retrieve all modular trunk cables by Tag, and return the next label ID.

        All modular trunk cables are expected to be tagged with the modular-trunk tag. All labels
        matching this tag are retrieved and sorted, then the next ID is returned using rjust.
        """
        cables = Cable.objects.filter(tags=MODULAR_TAG_ID)
        try:
            last_cable_label = sorted([i.label.split('--')[-1] for i in cables])[-1]
        except IndexError:
            return "C0001"
        else:
            last_id = last_cable_label[1:]
            return "C" + str(int(last_id) + 1).rjust(4, "0")

    def create_trunk_cable(self, ports: list[RearPort], length: int, status) -> None:
        cable_id = self.next_cable_id()
        trunk = Cable(
            type=CableTypeChoices.TYPE_SMF_OS2,
            a_terminations=[ports[0]],
            b_terminations=[ports[1]],
            status=status,
            label=f"COM--{ports[0].device.name}--{ports[1].device.name}--{cable_id}",
            length=length,
            length_unit="m",
            tenant=HUBSITE_TENANT,
        )
        self.wrap_save(trunk)
        trunk.tags.add(MODULAR_TAG)


class CreatePanels(Script, PanelsMixins):
    class Meta:
        name = "FHD Panel Creation"
        description = "Create pair of FHD panels (no cassettes)."
        scheduling_enabled = False

    site = FormFields.site
    rack_1 = FormFields.rack_1
    rack_1_position = FormFields.rack_1_position
    rack_2 = FormFields.rack_2
    rack_2_position = FormFields.rack_2_position
    status = FormFields.device_status

    def run(self, data, commit):
        hub_spoke = self.hub_spoke_check(data)

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
                site=data['site'],
                rack=data[f"rack_{i}"],
                position=int(rack_unit),
                face="front",
                device_type=ENCLOSURE,
                device_role=PANEL_ROLE,
                name=panel_name,
                status=data['status'],
                tenant=HUBSITE_TENANT,
            )
            self.wrap_save(panel)
            panels.append(panel)
            self.log_success(f"Created new panel: `{panel}`.")

        self.create_remote_panel_field(panels)
        self.log_success('Created `Remote Panel` link between the new panels.')


class AddCassettes(Script, PanelsMixins):
    class Meta:
        name = "FHD Cassette Creation"
        description = "Create & add cassettes to selected FHD panels, optionally create trunk jumpers."
        scheduling_enabled = False

    site = FormFields.site
    rack_1 = FormFields.rack_1
    panel_1 = FormFields.panel_1
    status = FormFields.device_status
    slot = FormFields.slot
    run_jumpers = FormFields.run_jumpers
    length = FormFields.length
    cable_status = FormFields.cable_status

    def run(self, data, commit):
        slot = f"Slot {data['slot']}"
        panel_1 = data['panel_1']
        panel_2 = self.get_remote_panel(panel_1)

        # check devicebays
        # re-use if empty?

        cassettes = []
        rps = []
        for panel in (panel_1, panel_2):
            # Create new cassette
            cassette_slug = "fhd-mpo-24lc-os2-cassette-type-a"
            if panel == panel_2:
                cassette_slug += "f"

            cassette = Device.objects.create(
                site=data["site"],
                rack=panel.rack,
                device_type=DeviceType.objects.get(slug=cassette_slug),
                device_role=CASSETTE_ROLE,
                name=f"{panel.name} -- {slot} MPO24-LC OS2",
                status=data['status'],
                tenant=HUBSITE_TENANT,
            )
            self.wrap_save(cassette)
            cassettes.append(cassette)
            self.log_success(f"Created new cassette: `{cassette}`.")

            # Create device bay in parent panel
            cassette_bay = DeviceBay(
                device=panel,
                name=slot,
                installed_device=cassette,
            )
            self.wrap_save(cassette_bay)
            self.log_success(f"Added `{cassette}` to parent panel `{panel}`.")

            # Create RearPort
            rp = RearPort(
                device=panel,
                name=f"{slot} Port 1 Rear",
                type=PortTypeChoices.TYPE_MPO,
                positions=12,
                custom_field_data={"jumper_type": "SMF"},
            )
            self.wrap_save(rp)
            rps.append(rp)
            self.log_success(f"Created (1) MPO-24 RearPort on `{panel}`.")

            # Create FrontPorts
            for i in range(1, 13):
                fp = FrontPort(
                    device=panel,
                    name=f"{slot} Port {2 * i - 1}/{2 * i} Front",
                    type=PortTypeChoices.TYPE_LC,
                    rear_port=rp,
                    rear_port_position=i,
                    custom_field_data={"jumper_type": "SMF"},
                )
                self.wrap_save(fp)
            self.log_success(f"Created (12) FrontPorts on `{panel}`.")

        self.create_remote_panel_field(cassettes)
        self.log_success('Created `Remote Panel` link between the new cassettes.')

        # Create Cable between the two RearPorts
        if data['run_jumpers']:
            self.create_trunk_cable(rps, data['length'], data['cable_status'])
            self.log_success('Created trunk cable between the new cassettes.')
