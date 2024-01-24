SEGMENT = "ccc"
SITE_CODE = "ahcc"
NET = "dc"

speed = "10G"
clr = 1234
BASE = {"speed": speed, "clr": clr}

remote_device = "lax-agg10"
remote_interface = "et-0/0/0"
remote_logical = 10
REMOTE_DEVICE = {
    "remote_device": remote_device,
    "remote_interface": remote_interface,
    "remote_logical": remote_logical,
}

CPE_BACKBONE = {"local_role": "CPE Router", "remote_role": "Backbone Router"}
BACKBONE_CPE = {"remote_role": "CPE Router", "local_role": "Backbone Router"}


def make_description(data: dict[str, str], site_code: str) -> str:
    description = f'{data["speed"]} to '
    if data.get("handoff"):
        description += f"{site_code} Handoff"
    else:
        description += f'{data["remote_device"]} {data["remote_interface"]}'
        if data.get("remote_logical"):
            description += f'.{data["remote_logical"]}'
        description += f' CLR-{data["clr"]}'

    return description


def make_handoff_tags(
    data: dict[str, str], net: str, site_code: str, local_tenant: str
) -> tuple[str | None, str | None]:
    port_tags = f"[{net}:ext]"
    edge_tags = f"[{net}:{local_tenant}][{net}:site-{site_code}]"
    dms = f"[{net}:dms][{net}:l2edge]"
    subint = data.get("subinterface")
    l3 = f"[{net}:edge]"
    l2 = f"[{net}:l2edge]"

    match data.get("service"):
        case "Layer 3" if subint:
            return port_tags, l3 + edge_tags
        case "Layer 2" if subint:
            return port_tags, l2 + edge_tags
        case "DMS" if subint:
            return port_tags, dms + edge_tags

        case "Layer 3":
            return port_tags + l3 + edge_tags, None
        case "Layer 2":
            return port_tags + l2 + edge_tags, None
        case "DMS":
            return port_tags + dms + edge_tags, None

        case _:
            raise ValueError("Undefined tags")


def make_infra_tag(data: dict[str, str], net: str, port_tag: str | None = None) -> tuple[str | None, str | None]:
    port_tag = port_tag if port_tag else f"[{net}:core]"
    infra_tag = f"[{net}:infra]"
    subint = data.get("subinterface")
    match data.get("service"):
        case "Layer 3" if subint:
            return port_tag, infra_tag
        case "Layer 2" if subint:
            return None, port_tag

        case "Layer 3":
            return port_tag + infra_tag, None
        case "Layer 2":
            return port_tag, None

        case _:
            raise ValueError("Undefined tags")


def make_interconnect_tags(
    data: dict[str, str], net: str, site_code: str, local_role: str, remote_role: str
) -> tuple[str | None, str | None]:
    """For all with a local segment of CENIC Backbone"""
    cpe = "CPE Router" in (local_role, remote_role)
    port_tag = f"[{net}:core]"

    match (remote_role.split()[1], local_role.split()[1]):
        # Backbone to CPE
        case ("Router", "Router") if cpe:
            return make_infra_tag(data, net)
        case ("Switch", "Router") if cpe:
            return f"[{net}:core][{net}:l2acc]", None

        # Backbone to Backbone
        case ("Router", "Router"):
            return make_infra_tag(data, net, port_tag=f"[{net}:bb-{site_code}]")
        case ("Switch", "Router"):
            return port_tag + f"[{net}:asi]", None

        # Backbone to Backbone, L2
        case ("Switch", "Router") if not cpe:
            return port_tag + f"[{net}:asi]", None
        case ("Router", "Switch") if not cpe:
            return port_tag + f"[{net}:l2agg]", None
        case ("Switch", "Switch") if not cpe:
            return port_tag + f"[{net}:l2icl]", None

        case _:
            raise ValueError("Undefined tags")


def make_tags(
    data: dict[str, str],
    net: str,
    site_code: str,
    local_role: str | None = None,
    remote_role: str | None = None,
    local_tenant: str | None = None,
) -> tuple[str | None, str | None]:
    if data.get("handoff"):
        port_tags, subint_tags = make_handoff_tags(data, net, site_code, local_tenant)
    else:
        port_tags, subint_tags = make_interconnect_tags(data, net, site_code, local_role, remote_role)

    description = make_description(data, site_code)
    if data.get("subinterface"):
        return port_tags, subint_tags + " " + description
    return port_tags + " " + description, None


def test_ho_l3_subint():
    data = {"service": "Layer 3", "subinterface": True, "handoff": True, **BASE}
    port_desc, subint_desc = make_tags(data, NET, SITE_CODE, local_tenant=SEGMENT)
    assert port_desc == f"[{NET}:ext]"
    assert subint_desc == f"[{NET}:edge][{NET}:{SEGMENT}][{NET}:site-{SITE_CODE.lower()}] 10G to {SITE_CODE} Handoff"


def test_ho_l3():
    data = {"service": "Layer 3", "handoff": True, **BASE}
    port_desc, subint_desc = make_tags(data, NET, SITE_CODE, local_tenant=SEGMENT)
    assert (
        port_desc
        == f"[{NET}:ext][{NET}:edge][{NET}:{SEGMENT}][{NET}:site-{SITE_CODE.lower()}] 10G to {SITE_CODE} Handoff"
    )
    assert not subint_desc


def test_ho_l2():
    data = {"service": "Layer 2", "handoff": True, **BASE}
    port_desc, subint_desc = make_tags(data, NET, SITE_CODE, local_tenant=SEGMENT)
    assert (
        port_desc
        == f"[{NET}:ext][{NET}:l2edge][{NET}:{SEGMENT}][{NET}:site-{SITE_CODE.lower()}] 10G to {SITE_CODE} Handoff"
    )
    assert not subint_desc


def test_ho_dms():
    data = {"service": "DMS", "handoff": True, **BASE}
    port_desc, subint_desc = make_tags(data, NET, SITE_CODE, local_tenant=SEGMENT)
    assert (
        port_desc
        == f"[{NET}:ext][{NET}:dms][{NET}:l2edge][{NET}:{SEGMENT}][{NET}:site-{SITE_CODE.lower()}] 10G to {SITE_CODE} Handoff"
    )
    assert not subint_desc


def test_ic_cpe_l3_subint():
    data = {"service": "Layer 3", "subinterface": True, **BASE, **REMOTE_DEVICE}
    port_desc, subint_desc = make_tags(data, NET, SITE_CODE, **CPE_BACKBONE)
    assert port_desc == f"[{NET}:core]"
    assert subint_desc == f"[{NET}:infra] 10G to lax-agg10 et-0/0/0.10 CLR-1234"


def test_ic_cpe_l3():
    data = {"service": "Layer 3", **BASE, **REMOTE_DEVICE}
    port_desc, subint_desc = make_tags(data, NET, SITE_CODE, **CPE_BACKBONE)
    assert port_desc == f"[{NET}:core][{NET}:infra] 10G to lax-agg10 et-0/0/0.10 CLR-1234"
    assert not subint_desc


def test_ic_cpe_l2_subint():
    data = {"service": "Layer 2", "subinterface": True, **BASE, **REMOTE_DEVICE}
    port_desc, subint_desc = make_tags(data, NET, SITE_CODE, **CPE_BACKBONE)
    assert not port_desc
    assert subint_desc == f"[{NET}:core] 10G to lax-agg10 et-0/0/0.10 CLR-1234"


def test_ic_cpe_l2():
    data = {"service": "Layer 2", **BASE, **REMOTE_DEVICE}
    port_desc, subint_desc = make_tags(data, NET, SITE_CODE, **CPE_BACKBONE)
    assert port_desc == f"[{NET}:core] 10G to lax-agg10 et-0/0/0.10 CLR-1234"
    assert not subint_desc


def test_ic_bb_cpe_l3_subint():
    data = {"service": "Layer 3", "subinterface": True, **BASE, **REMOTE_DEVICE}
    port_desc, subint_desc = make_tags(data, NET, SITE_CODE, **BACKBONE_CPE)
    assert port_desc == f"[{NET}:core]"
    assert subint_desc == f"[{NET}:infra] 10G to lax-agg10 et-0/0/0.10 CLR-1234"


def test_ic_bb_cpe_l3():
    data = {"service": "Layer 3", **BASE, **REMOTE_DEVICE}
    port_desc, subint_desc = make_tags(data, NET, SITE_CODE, **BACKBONE_CPE)
    assert port_desc == f"[{NET}:core][{NET}:infra] 10G to lax-agg10 et-0/0/0.10 CLR-1234"
    assert not subint_desc


def test_ic_bb_bb():
    data = {"service": "Layer 3", "subinterface": True, **BASE, **REMOTE_DEVICE}
    port_desc, subint_desc = make_tags(
        data, NET, SITE_CODE, remote_role="Backbone Router", local_role="Backbone Router"
    )
    assert port_desc == f"[{NET}:bb-{SITE_CODE}]"
    assert subint_desc == f"[{NET}:infra] 10G to lax-agg10 et-0/0/0.10 CLR-1234"


def test_ic_bb_sw():
    data = {**BASE, **REMOTE_DEVICE}
    port_desc, subint_desc = make_tags(
        data, NET, SITE_CODE, remote_role="Backbone Switch", local_role="Backbone Router"
    )
    assert port_desc == f"[{NET}:core][{NET}:asi] 10G to lax-agg10 et-0/0/0.10 CLR-1234"
    assert not subint_desc


def test_ic_sw_sw():
    data = {**BASE, **REMOTE_DEVICE}
    port_desc, subint_desc = make_tags(
        data, NET, SITE_CODE, remote_role="Backbone Switch", local_role="Backbone Switch"
    )
    assert port_desc == f"[{NET}:core][{NET}:l2icl] 10G to lax-agg10 et-0/0/0.10 CLR-1234"
    assert not subint_desc
