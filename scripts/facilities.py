from typing import Final

from dcim.choices import RackStatusChoices, SiteStatusChoices
from dcim.models import Rack, RackRole, Region, Site, SiteGroup
from django.utils.text import slugify
from extras.scripts import ObjectVar, Script, StringVar
from tenancy.models import Tenant

CPE_RACK_NAME: Final = "CPE_RACK"
RACK_ROLE: Final = RackRole.objects.get(slug="associate")

name = "Facilicites"


class FormFields:
    """Class for Form Fields that can be used amongst several Scripts."""

    site_code = StringVar(label="Site Code", description="New, unique site code")
    site_name = StringVar(label="Site Name", description="Name of the site")
    region = ObjectVar(model=Region)

    # likely group sites together, for example group all CCC ring sites to one main campus
    # still TBD on how to implement
    group = ObjectVar(
        model=SiteGroup,
        description="TBD",
    )

    # NOTE: Will likely either need to validate address or create a zip code CF for Accounting
    # Accounting will need to be able to generate reports by zip code
    address = StringVar(label="Physical Address")

    shipping = StringVar(label="Shipping Address", required=False)
    tenant = ObjectVar(model=Tenant, label="Segment")


class FacilitiesMixins:
    """Class for common methods that can be used amongst several Scripts."""

    @staticmethod
    def wrap_save(obj) -> None:
        obj.full_clean()
        obj.save()


class NewSiteScript(Script, FacilitiesMixins):
    class Meta:
        name = "New Site"
        description = "Create a new CENIC Member site & rack"
        scheduling_enabled = False

    site_code = FormFields.site_code
    site_name = FormFields.site_name
    region = FormFields.region

    group = FormFields.group

    address = FormFields.address
    shipping = FormFields.shipping
    tenant = FormFields.tenant

    def run(self, data, commit):
        site = Site(
            name=data["site_code"],
            slug=slugify(data["site_code"]),
            status=SiteStatusChoices.STATUS_ACTIVE,
            physical_address=data["address"],
            shipping_address=data["shipping"],
            region=data["region"],
            group=data["group"],
            tenant=data["tenant"],
        )
        self.wrap_save(site)
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
        self.wrap_save(rack)
        self.log_success(f"Created new rack: {rack}.")
