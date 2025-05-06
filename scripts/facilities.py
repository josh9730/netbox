# VERSION: 2.0
# temp until NB can access GitLab

import re
from typing import Final

from dcim.choices import PowerFeedPhaseChoices, PowerFeedSupplyChoices, RackStatusChoices, SiteStatusChoices
from dcim.models import Cable, Device, DeviceRole, PowerFeed, PowerPanel, PowerPort, Rack, RackRole, Region, Site
from django.utils.text import slugify
from extras.scripts import BooleanVar, ChoiceVar, IntegerVar, ObjectVar, Script, StringVar
from tenancy.models import Tenant
from utilities.exceptions import AbortScript

name = "Facilities"

CPE_RACK_NAME: Final = "CPE_RACK"
CPE_RACK_ROLE: Final = RackRole.objects.get(slug="associate")
PDU_ROLE: Final = DeviceRole.objects.get(slug="pdu")


def wrap_save(obj) -> None:
    obj.full_clean()
    obj.save()


class NewPower(Script):
    class Meta:
        name = "New Power Circuit"
        description = "Add new facility power to a rack"
        scheduling_enabled = False
        fieldsets = (
            ("Site", ("site", "rack")),
            ("Power", ("power_type", "amperage", "voltage", "phase", "redundant_feed", "billing_id")),
            ("Connection", ("pdu",)),
        )

    site = ObjectVar(model=Site)
    rack = ObjectVar(model=Rack, query_params={"site_id": "$site"})

    redundant_feed = BooleanVar(label="Redundant Feed?", default=True)
    power_type = ChoiceVar(label="Power Type", choices=PowerFeedSupplyChoices, default="dc")
    amperage = IntegerVar(max_value=200, min_value=0)
    voltage_choices = (("48", "48"), ("120", "120"), ("208", "208"))
    voltage = ChoiceVar(choices=voltage_choices, default="48")
    phase = ChoiceVar(choices=PowerFeedPhaseChoices, default="single-phase")
    billing_id = StringVar(label="Facility Billing ID", required=False)

    pdu = ObjectVar(
        label="PDU",
        description="Optional PDU for feed connection(s)",
        model=Device,
        required=False,
        query_params={"rack_id": "$rack", "role_id": PDU_ROLE.id},
    )

    def run(self, data, commit):
        def get_next_increment() -> int:
            """Create list of Feed #s, get max, add one to create the next Feed increment."""
            if rack_feeds:
                list_feed_nums = [re.search(r"Feed \d", i.name).group().split(" ")[1] for i in rack_feeds]
                return int(max(list_feed_nums)) + 1
            return 1

        power_panel = PowerPanel.objects.get(site=data["site"], location=data["rack"].location)
        rack_feeds = PowerFeed.objects.filter(rack=data["rack"])
        phase = "single-phase" if data["power_type"] == "dc" else data["phase"]  # DC should always be single-phase
        next_increment = get_next_increment()
        num_feeds = ["A", "B"] if data["redundant_feed"] else ["A"]

        feeds = []
        for side in num_feeds:
            name = f"{data['rack'].name} {data['power_type'].upper()} Feed {next_increment} - {side} Side"
            feed = PowerFeed(
                name=name,
                power_panel=power_panel,
                rack=data["rack"],
                supply=data["power_type"],
                voltage=data["voltage"],
                amperage=data["amperage"],
                phase=phase,
                custom_field_data={"power_id": data["billing_id"]},
            )
            wrap_save(feed)
            self.log_success(f"Created new feed: `{name}`")
            feeds.append(feed)

        if data["pdu"]:
            power_ports = PowerPort.objects.filter(device=data["pdu"])
            port_names = [i.name for i in power_ports]
            if not port_names == num_feeds:
                raise AbortScript("Selected PDU's Power Ports do not match the created Power Feeds.")

            for pdu_port, feed in zip(power_ports, feeds):
                cable = Cable(
                    type="power",
                    status="connected",
                    a_terminations=[pdu_port],
                    b_terminations=[feed],
                )
                wrap_save(cable)
                self.log_success(f"Created connection: `{feed.name}` to `{data['pdu'].name} Port {pdu_port.name}`")


class NewSiteScript(Script):
    class Meta:
        name = "New Site"
        description = "Create a new CENIC Member site & rack"
        scheduling_enabled = False

    site_code = StringVar(label="Site Code", description="New, unique site code")
    site_name = StringVar(label="Site Name", description="Name of the site")
    region = ObjectVar(model=Region, default=Region.objects.get(name="California"))
    tenant = ObjectVar(model=Tenant, label="Segment")
    address = StringVar(label="Physical Address")
    zip_code = IntegerVar(label="ZIP Code")
    shipping = StringVar(label="Shipping Address", required=False)

    def run(self, data, commit):
        site = Site(
            name=data["site_code"],
            slug=slugify(data["site_code"]),
            description=data["site_name"],
            status=SiteStatusChoices.STATUS_ACTIVE,
            physical_address=data["address"],
            shipping_address=data["shipping"],
            region=data["region"],
            tenant=data["tenant"],
            custom_field_data={"zip": data["zip_code"]},
        )
        wrap_save(site)
        self.log_success(f"Created new site: `{site.name}`.")

        rack = Rack(
            name=CPE_RACK_NAME,
            site=site,
            status=RackStatusChoices.STATUS_ACTIVE,
            tenant=data["tenant"],
            role=CPE_RACK_ROLE,
            comments="# NOT A REAL RACK\nThis is an abstraction only, Associate racks are not tracked or managed by CENIC.",
            custom_field_data={"hub_rack": False},
        )
        wrap_save(rack)
        self.log_success(f"Created new rack: `{rack}`.")
