from typing import Final

from dcim.models import Site
from extras.scripts import ObjectVar, Script
from ipam.models import ASN, ASNRange, RIR
from utilities.exceptions import AbortScript

PRIVATE_ASN_RANGE: Final[str] = "Private ASN"
RIR: Final[RIR] = RIR.objects.get(name="RFC-6996")


def wrap_save(obj) -> None:
    """Wrapper for saving new objects."""
    obj.full_clean()
    obj.save()


class AssignASN(Script):
    class Meta:
        name = "Assign Private ASN"
        description = "Assign an available private ASN to a Site"
        scheduling_enabled = False

    site = ObjectVar(model=Site)
    org_asn = ObjectVar(
        model=ASN,
        label="Existing Organization ASN",
        description="Input Org ASN, if applicable, otherwise leave blank to assign a new ASN.",
        query_params={"rir_id": RIR.id},
        required=False,
    )

    def run(self, data, commit):
        def check_current_asn() -> None:
            """Verify given site does not have an ASN already."""
            if site.asns.exists():
                raise AbortScript("Site already has an assigned ASN.")

        def get_next_asn() -> ASN:
            """Get next available ASN."""
            try:
                next_asn = ASNRange.objects.get(name=PRIVATE_ASN_RANGE).get_available_asns()[0]
            except IndexError:
                raise AbortScript("No available ASNs found!")

            asn = ASN.objects.create(asn=next_asn, rir=RIR)
            wrap_save(asn)
            return asn

        site = data["site"]
        asn = data["org_asn"]

        check_current_asn()

        if not asn:
            asn = get_next_asn()
        asn.sites.add(site)

        self.log_success(f"Assigned {asn.asn} to {site.name}.")
