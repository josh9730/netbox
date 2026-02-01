from circuits.models import CircuitTermination
from dcim.choices import DeviceStatusChoices, LinkStatusChoices
from dcim.models import (
    Cable,
    CablePath,
    Device,
    DeviceRole,
    FrontPort,
    Interface,
    RearPort,
)
from extras.scripts import BooleanVar, ChoiceVar, IntegerVar, ObjectVar, Script


def wrap_save(obj) -> None:
    """Wrapper for saving new objects."""
    obj.full_clean()
    obj.save()


def non_panels_slugs() -> list:
    """Return list of slugs for non-panel devices."""
    query_results = DeviceRole.objects.exclude(name__contains="Panel")
    return [i.slug for i in query_results]


class CobberFreeForm(Script):
    class Meta:
        name = "Cobber FreeForm"
        description = "Print Cobber FreeForm(s) for a cable path based on selected Interface."
        commit_default = False
        scheduling_enabled = False

    device_slugs = non_panels_slugs()
    device = ObjectVar(
        model=Device, description="Router/Switch for one side of the Cable Path", query_params={"role": device_slugs}
    )
    interface = ObjectVar(model=Interface, query_params={"device_id": "$device", "cabled": True})
    reverse = BooleanVar(
        label="Reverse Freeform Output", description="Reverse A/Z for Freeform continuity", default=False
    )

    def run(self, data, commit) -> None:
        def get_cables_list(interface: Interface, reverse: bool) -> list[Cable]:
            """Retrieves a list of Cable objects connected to a given interface.

            - CablePath returns the form of [ Cable, Port, Port, Cable, etc]
            - only the literal Cable objects are returned, all other data can be retrieved from the Cable
            """
            cable_path = CablePath.objects.get(interface=interface).path_objects
            cable_list = [i[0] for i in cable_path if isinstance(i[0], Cable)]
            if reverse:
                cable_list.reverse()
            return cable_list

        def get_ports_list(cable: Cable, reverse: bool, start: int) -> list[Interface | FrontPort | RearPort]:
            for term in (cable.a_terminations, cable.b_terminations):
                assert len(term) == 1, "Too many Cable terminations to unpack!"
            ports_list = [cable.a_terminations[0], cable.b_terminations[0]]
            if isinstance(cable.a_terminations[0], CircuitTermination):
                ports_list.reverse()

            # Enforce starting with Interface
            if start == 0 and not isinstance(cable.a_terminations, Interface):
                ports_list.reverse()

            if reverse:
                ports_list.reverse()
            return ports_list

        def make_port(port: FrontPort | RearPort | Interface, side: str) -> str:
            # fmt: off
            return f"""**{side} Site**: `{port.device.site.name}`  
            **{side} Rack**: `{port.device.rack.name}`  
            **{side} Device**: `{port.device.name}`  
            **{side} Port**: `{port.name}`  
            """
            # fmt: on

        def make_circuitterm(port: CircuitTermination, side: str) -> str:
            # fmt: off
            return f"""**{side} Site**: `{port.termination.name}
            **{side} Rack**: "NOT CENIC MANAGED"  
            **{side} Device**: "NOT CENIC MANAGED"  
            **{side} Port**: "NOT CENIC MANAGED"  
            """
            # fmt: on

        def add_label(port: FrontPort | RearPort | Interface | CircuitTermination) -> str:
            output = f"\n**Cable Label**: `{port.cable.label}`"
            if isinstance(port, CircuitTermination):
                output += f"**Cross Connect ID**: `{port.circuit.cid}`"
            output += "\n\n"
            return output

        cable_list = get_cables_list(data["interface"], data["reverse"])

        for i, cable in enumerate(cable_list):
            ports_list = get_ports_list(cable, data["reverse"], i)

            cable_output = ""
            for j, port in enumerate(ports_list):
                side = "A" if j == 0 else "Z"
                if isinstance(port, CircuitTermination):
                    cable_output += make_circuitterm(port, side)
                else:
                    cable_output += make_port(port, side)
                if j == 0:
                    cable_output += add_label(port)
            self.log_success(cable_output)


class CircuitReadiness(Script):
    class Meta:
        name = "CLR Readiness"
        description = "Update CLR Statuses during Readiness / Decom"
        scheduling_enabled = False

    clr = IntegerVar(label="CLR ID", min_value=1000, max_value=50000)
    status = ChoiceVar(label="New Status", choices=LinkStatusChoices)

    def run(self, data, commit) -> None:
        cables = list(Cable.objects.filter(label__regex=rf'.*(CLR-{data["clr"]})\D*'))

        try:
            for cable in cables:
                old_status = cable.status
                cable.status = data["status"]
                wrap_save(cable)

                cable_output = ""
                for i, port in enumerate((cable.a_terminations[0], cable.b_terminations[0])):
                    side = "A" if i == 0 else "Z"
                    if isinstance(port, CircuitTermination):
                        cable_output += f"""**XConnect ID**: `{port.circuit.cid}`\n"""
                    else:
                        cable_output += f"""**Device {side}**: `{port.device.name}`  
                        **Port {side}**: `{port.name}`  
                        """
                self.log_success(cable_output)
            self.log_success(f"Updated all cables from `{old_status}` to `{data['status']}`.")
        except Exception as _:
            self.log_failure(f"CLR {data["clr"]} is not found in Netbox!")


class DeviceReadiness(Script):
    class Meta:
        name = "Device Readiness"
        description = "Update Device Status during Readiness / Decom"
        scheduling_enabled = False

    device_slugs = non_panels_slugs()
    device = ObjectVar(model=Device, query_params={"role": device_slugs})
    status = ChoiceVar(label="New Status", choices=DeviceStatusChoices)

    def run(self, data, commit) -> None:
        old_status = data["device"].status
        data["device"].status = data["status"]
        wrap_save(data["device"])

        self.log_success(
            f'Updated {data["device"].name} to `{data["status"]}` from `{old_status}`',
        )
