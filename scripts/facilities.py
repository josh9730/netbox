# VERSION: 1.0
# temp until NB can access GitLab

from typing import Final

from dcim.choices import RackStatusChoices, SiteStatusChoices
from dcim.models import Rack, RackRole, Region, Site
from django.utils.text import slugify
from extras.scripts import IntegerVar, ObjectVar, Script, StringVar
from tenancy.models import Tenant

name = "Facilicites"

CPE_RACK_NAME: Final = "CPE_RACK"
RACK_ROLE: Final = RackRole.objects.get(slug="associate")


def wrap_save(obj) -> None:
    obj.full_clean()
    obj.save()


class NewSiteScript(Script):
    class Meta:
        name = "New Site"
        description = "Create a new CENIC Member site & rack "
        scheduling_enabled = False

    site_code = StringVar(label="Site Code", description="New, unique site code")
    site_name = StringVar(label="Site Name", description="Name of the site")
    region = ObjectVar(model=Region, default=Region.objects.get(name="California"))
    tenant = ObjectVar(model=Tenant, label="Segment")
    address = StringVar(label="Physical Address")
    zip = IntegerVar(label="ZIP Code")
    shipping = StringVar(label="Shipping Address", required=False)

    def run(self, data, commit):
        site = Site(
            name=data["site_code"],
            slug=slugify(data["site_code"]),
            status=SiteStatusChoices.STATUS_ACTIVE,
            physical_address=data["address"],
            shipping_address=data["shipping"],
            region=data["region"],
            tenant=data["tenant"],
            custom_field_data={"zip": data["zip"]},
        )
        wrap_save(site)
        self.log_success(f"Created new site: {site}.")

        rack = Rack(
            name=CPE_RACK_NAME,
            site=site,
            status=RackStatusChoices.STATUS_ACTIVE,
            tenant=data["tenant"],
            role=RACK_ROLE,
            comments="# NOT A REAL RACK\nThis is an abstraction only, Associate racks are not tracked or managed by CENIC.",
            custom_field_data={"hub_rack": False},
        )
        wrap_save(rack)
        self.log_success(f"Created new rack: {rack}.")
