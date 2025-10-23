import csv
import io
from typing import TYPE_CHECKING, Final

from circuits.models import Circuit
from dcim.models import PowerFeed, RackReservation
from extras.models import Tag
from extras.scripts import Script

if TYPE_CHECKING:
    import _csv

PT_TAG_ID: Final[str] = Tag.objects.get(slug="passthrough-assets").id


name = "Accounting Reports"


class AccountingPassthroughAssets(Script):
    """Name needs to contain 'Accounting' to pass through the permissions for the accounting team."""

    class Meta:
        name = "Passthrough Assets Report"
        description = "Generate a CSV output of all passthrough rack, power, and cross-connect assets."
        scheduling_enabled = False
        commit_default = False

    def run(self, data, commit):
        def round_rack_rsv(val: int, max_val: int) -> float:
            """Round reservation up to nearest quarter and output multiplier."""
            match output := val / max_val:
                case output if 0 <= output <= 0.25:
                    return 0.25
                case output if 0.25 < output <= 0.5:
                    return 0.5
                case output if 0.5 < output <= 0.75:
                    return 0.75
                case output if 0.75 < output <= 1:
                    return 1.0
                case _:
                    return 0.0
                
        def get_pt_racks(writer: "_csv._writer") -> None:
            """Get racks by reservation instead of directly. The rack doesn't have the actual member, and one rack can be sub-leased to multiple customers."""
            pt_racks = RackReservation.objects.filter(tags=PT_TAG_ID)
            for rack_rsv in pt_racks:
                asset_type = "Rack"
                site_code = rack_rsv.rack.site.name
                address = rack_rsv.rack.site.physical_address
                name = rack_rsv.rack.name
                vendor_id = rack_rsv.rack.custom_field_data["billing_id"]
                member = rack_rsv.custom_field_data["pt_member"]
                rack_count = rack_rsv.rack.site.racks.count()  # for apportioning per-rack costs based on suite cost

                units = rack_rsv.units[-1] - rack_rsv.units[0] + 1  # total RUs reserved
                rack_units = rack_rsv.rack.u_height

                writer.writerow(
                    [
                        asset_type,
                        site_code,
                        address,
                        name,
                        vendor_id,
                        member,
                        round_rack_rsv(units, rack_units),
                        f"Suite Racks: {rack_count}",
                    ]
                )

        def get_pt_power(writer: "_csv._writer") -> None:
            pt_power = PowerFeed.objects.filter(tags=PT_TAG_ID)
            for power in pt_power:
                asset_type = "Power"
                site_code = power.rack.site.name
                address = power.rack.site.physical_address
                name = power.rack.name
                vendor_id = power.custom_field_data["power_id"]
                member = power.custom_field_data["pt_member"]

                current = power.amperage
                power_type = power.supply
                voltage = power.voltage
                phase = power.phase
                note = f"{current}A {voltage}V{power_type.upper()} {phase}"

                writer.writerow([asset_type, site_code, address, name, vendor_id, member, 1.0, note])

        def get_pt_circuits(writer: "_csv._writer") -> None:
            pt_circuits = Circuit.objects.filter(tags=PT_TAG_ID)
            for circuit in pt_circuits:
                asset_type = circuit.type.name  # Cross Connect or Dark Fiber
                site_code = ""
                address = ""
                name = circuit.custom_field_data["circuit_ticket"]
                vendor_id = circuit.cid
                member = circuit.custom_field_data["ownership"]
                writer.writerow([asset_type, site_code, address, name, vendor_id, member, 1.0, ""])

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            ["Asset Type", "Site Code", "Address", "CENIC ID", "Vendor ID", "Member", "Allocation", "Notes"]
        )

        get_pt_racks(writer)
        get_pt_power(writer)
        # get_pt_circuits(writer)

        return output.getvalue()
