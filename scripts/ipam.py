from typing import Final

from dcim.models import Site
from extras.scripts import IntegerVar, ObjectVar, Script
from ipam.models import ASN, ASNRange, RIR
from utilities.exceptions import AbortScript

PRIVATE_ASN_RANGE: Final[ASNRange] = ASNRange.objects.get(name="Private ASN")
PUBLIC_ASN_RANGE: Final[ASNRange] = ASNRange.objects.get(name="Public ASN")
PRIVATE_RIR: Final[RIR] = RIR.objects.get(name="RFC-6996")
PUBLIC_RIR: Final[RIR] = RIR.objects.get(name="IANA")


def wrap_save(obj) -> None:
    """Wrapper for saving new objects."""
    obj.full_clean()
    obj.save()


class AssignASN(Script):
    class Meta:
        name = "Assign ASN to a Site"
        description = "Assign a Public or Private ASN to a Site"
        scheduling_enabled = False

    site = ObjectVar(model=Site)
    new_asn = IntegerVar(
        label="ASN",
        description="Existing Organizational Private ASN or Public ASN. Leave blank for next available Private ASN.",
        required=False,
    )

    def run(self, data, commit):
        def coerce_asn(asn: int | None) -> int | ASN | None:
            """Return the user-supplied ASN integer as an ASN Object if it exists in Netbox."""
            try:
                return ASN.objects.get(asn=asn)
            except ASN.DoesNotExist:
                return asn

        def get_rir(asn: int | ASN | None) -> RIR:
            """Get RIR for a given ASN."""
            if isinstance(asn, ASN):
                if asn.rir in (PRIVATE_RIR, PUBLIC_RIR):
                    return asn.rir
                else:
                    raise AbortScript("ASN is already created in an unexpected RIR.")

            elif isinstance(asn, int):
                if asn in range(PRIVATE_ASN_RANGE.start, PRIVATE_ASN_RANGE.end + 1):
                    return PRIVATE_RIR
                return PUBLIC_RIR

            return PRIVATE_RIR

        def test_site_asns(site: Site, new_rir: RIR, new_asn: int | ASN | None) -> None:
            """Test ASNs assigned to a Site.
            - A Site should not be assigned another ASN if it is already assigned a Private ASN
            - Prevent duplicate assignments (which likely doesnt affect anything)
            - A Site should not be assigned a Public ASN if it is already assigned a Private ASN
                - This likely means the Private ASN just needs to be removed.
            """
            if site.asns.exists():
                if new_rir == PRIVATE_RIR:
                    raise AbortScript("Site already has ASN(s) assigned, cannot assign a new Private ASN.")
                else:
                    for asn in site.asns.values():
                        if isinstance(new_asn, ASN):
                            if asn["id"] == new_asn.id:
                                raise AbortScript(f"{new_asn} is already assigned to this Site.")
                        if asn["rir_id"] == PRIVATE_RIR.id:
                            raise AbortScript("Site already has a Private ASN, cannot assign another ASN.")

        def check_other_assignment(asn: int | ASN | None, new_site: Site) -> None:
            """Test Sites assigned to a user-supplied ASN.
            - An ASN should not be shared between Segments
            - Organizations are not in Netbox (yet), so per-Org testing is not possible
                - Instead, a list of assigned sites is given to the user to check
            """
            if isinstance(asn, ASN):
                if asn.sites.exists():
                    site_names = []
                    for site in asn.sites.values():
                        if not site["tenant_id"] == new_site.tenant.id:
                            raise AbortScript(
                                f"Cannot assign the same ASN to members of two different segments ({new_site} and {site['name']})."
                            )
                        site_names.append(site["name"])

                self.log_warning(f"{asn} is already assigned to {site_names}. Make sure this is expected!")

        def get_or_create_asn(rir: RIR, asn: int | ASN | None) -> ASN:
            """Fetch ASN, if existing, or create a new ASN object."""

            def create_next_private_asn() -> ASN:
                """Get next available Private ASN."""
                try:
                    next_asn: int = PRIVATE_ASN_RANGE.get_available_asns()[0]
                except IndexError:
                    raise AbortScript("No available ASNs found!")
                return create_asn(next_asn, PRIVATE_RIR)

            def create_asn(asn: int, rir: RIR) -> ASN:
                new_asn = ASN.objects.create(asn=asn, rir=rir)
                wrap_save(new_asn)
                return new_asn

            if isinstance(asn, ASN):
                return asn
            if isinstance(asn, int):
                return create_asn(asn, rir)
            else:
                return create_next_private_asn()

        site = data["site"]
        new_asn = coerce_asn(data["new_asn"])

        rir = get_rir(new_asn)
        test_site_asns(site, rir, new_asn)
        check_other_assignment(new_asn, site)

        asn = get_or_create_asn(rir, new_asn)
        asn.sites.add(site)

        self.log_success(f"Assigned {asn.asn} to {site.name}.")
