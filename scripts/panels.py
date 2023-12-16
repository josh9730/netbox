from typing import Final

from dcim.choices import CableTypeChoices, DeviceStatusChoices, LinkStatusChoices
from dcim.models import Cable, Device, DeviceRole, DeviceType, Rack, RearPort, Site
from extras.models import Tag
from extras.scripts import ChoiceVar, IntegerVar, ObjectVar, Script
from tenancy.models import Tenant

name = "Panel Creations"

HUBSITE_TENANT: Final = Tenant.objects.get(name="CENIC Hubsite")
ENCLOSURE: Final = DeviceType.objects.get(model="FHD Enclosure, Blank")
PANEL_ROLE: Final = DeviceRole.objects.get(slug="modular-panels")
MODULAR_TAG: Final = "Modular Trunk"
MODULAR_TAG_ID: Final = Tag.objects.get(slug="modular-trunk").id


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
        panels[0].custom_field_data["remote_panel"] = panels[1].id
        panels[1].custom_field_data["remote_panel"] = panels[0].id
        panels[0].save()
        panels[1].save()

    @staticmethod
    def get_remote_panel(panel: Device) -> Device:
        """Return remote_panel Device."""
        remote_panel_id = panel.custom_field_data["remote_panel"]
        return Device.objects.get(id=remote_panel_id)

    @staticmethod
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

    def create_trunk_cable(self, ports: list[RearPort], length: int, status: LinkStatusChoices) -> None:
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

    site = ObjectVar(label="Site Name", model=Site)
    rack_1 = ObjectVar(label="Rack A", model=Rack, query_params={"site_id": "$site"})
    rack_1_position = IntegerVar(
        label="Rack A Position", description="Lowest RU filled by the new panel.", min_value=1, max_value=44
    )

    # how to filter for available RUs?
    # r1_ru = Rack.objects.get(id="$rack_1").get_rack_units()
    # avail_u = [(i['name'], i['name']) for i in r1_ru if not i['occupied']]
    # rack_choices = ChoiceVar(choices=avail_u)

    rack_2 = ObjectVar(label="Rack B", model=Rack, query_params={"site_id": "$site"})
    rack_2_position = IntegerVar(
        label="Rack B Position", description="Lowest RU filled by the new panel.", min_value=1, max_value=44
    )
    status = ChoiceVar(
        label="Install Status", required=False, choices=DeviceStatusChoices, default=DeviceStatusChoices.STATUS_ACTIVE
    )

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
                site=data["site"],
                rack=data[f"rack_{i}"],
                position=int(rack_unit),
                face="front",
                device_type=ENCLOSURE,
                device_role=PANEL_ROLE,
                name=panel_name,
                status=data["status"],
                tenant=HUBSITE_TENANT,
            )
            self.wrap_save(panel)
            panels.append(panel)
            self.log_success(f"Created new panel: `{panel}`.")

        self.create_remote_panel_field(panels)
        self.log_success("Created `Remote Panel` link between the new panels.")


class CreatePanelTrunks(Script, PanelsMixins):
    class Meta:
        name = "FHD Trunk Creation"
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
        panel_2 = self.get_remote_panel(data["panel_1"])
        panel_2_port = panel_2.rearports.get(name=data["rp_1"].name)

        self.create_trunk_cable(
            [data["rp"], panel_2_port],
            length=data.get("length"),
            status=data["status"],
        )

        self.log_success(
            f"Created trunk cable between `{data['panel_1']} {data['rp_1']}` and `{panel_2} {panel_2_port}`."
        )
