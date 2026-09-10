"""Generate the stage 5 data center from original geometry and synthetic registries."""

import base64
import json
from collections import Counter
from copy import deepcopy
from io import BytesIO
from pathlib import Path

from PIL import Image

from ..model import Binding, Equipment, Node, Page, Point, Project, Template, canonical_hash
from ..operations.transaction import atomic_write

CATALOG = (
    (
        "pump",
        "CHWP",
        20,
        "cooling",
        "CHW",
        ("run", "fault"),
        ("local", "remote", "command", "speed"),
        "speed",
        "rpm",
        "#27c4ad",
    ),
    (
        "valve",
        "VALVE",
        20,
        "cooling",
        "CHW",
        ("status", "fault"),
        ("command", "position"),
        "position",
        "%",
        "#5ea9ed",
    ),
    ("sensor", "SENSOR", 30, "cooling", "CHW", ("value",), ("alarm", "quality"), "value", "kPa", "#b49bfa"),
    (
        "ups",
        "UPS",
        12,
        "electrical",
        "POWER",
        ("status", "fault"),
        ("load", "battery", "voltage"),
        "load",
        "%",
        "#e7b85c",
    ),
    (
        "generator",
        "GEN",
        8,
        "electrical",
        "POWER",
        ("run", "fault"),
        ("command", "fuel", "power"),
        "power",
        "kW",
        "#ee8c77",
    ),
)
MISSING_FAULT = ("CHWP_CARD_03", "CHWP_CARD_07", "CHWP_CARD_14")


def icon(color):
    # Original abstract equipment symbol, including a gradient and clipped geometry.
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
<defs><linearGradient id="tone"><stop stop-color="{color}"/><stop offset="1" stop-color="#23364f"/></linearGradient>
<clipPath id="rim"><circle cx="16" cy="16" r="13"/></clipPath></defs>
<circle cx="16" cy="16" r="14" fill="none" stroke="{color}" stroke-width="2"/>
<g clip-path="url(#rim)"><path d="M2 14h28v4H2z M14 2h4v28h-4z" fill="url(#tone)"/>
<circle cx="16" cy="16" r="5" fill="{color}"/></g></svg>'''


def add(page, id, kind, x, y, width, height, **kwargs):
    node = Node(
        id=id,
        kind=kind,
        page_id=page.id,
        x=x,
        y=y,
        width=width,
        height=height,
        content_width=kwargs.pop("content_width", width or 1),
        content_height=kwargs.pop("content_height", height or 1),
        **kwargs,
    )
    page.nodes.append(node)
    return node


def label(page, id, text, x, y, width=1800, size=18, color="#b3c3d7", **kwargs):
    return add(
        page,
        id,
        "text",
        x,
        y,
        width,
        max(28, size + 4),
        text=text,
        style={"font-size": size, "fill": color},
        **kwargs,
    )


def make_project():
    pages = {
        id: Page(id=id, name=name, background="#101b2c")
        for id, name in (
            ("overview", "Synthetic data center / overview"),
            ("cooling", "Cooling equipment"),
            ("electrical", "Electrical equipment"),
        )
    }
    project = Project(
        id="SYNTHETIC_DC_STAGE5",
        name="EAS / synthetic data center laboratory",
        pages=list(pages.values()),
        metadata={
            "synthetic": True,
            "origin": "Original generated geometry and fictional point registry",
            "runtime": "Design values only; no live equipment or industrial connection",
        },
    )
    for page in pages.values():
        add(
            page, f"HEADER_{page.id.upper()}", "shape", 0, 0, 1920, 98, style={"fill": "#192940"}, z_index=-10
        )
        label(page, f"TITLE_{page.id.upper()}", page.name.upper(), 40, 26, size=30, color="#f0f5fc")
        label(
            page,
            f"SUBTITLE_{page.id.upper()}",
            "EAS-HMI  /  SYNTHETIC ENGINEERING LAB  /  DESIGN VALUES ONLY",
            40,
            65,
            size=13,
        )
        label(
            page,
            f"FOOTER_{page.id.upper()}",
            "No customer data. No live measurements. Connections carry explicit drawing geometry.",
            40,
            1038,
            size=13,
        )

    for type, prefix, count, page_id, system, required, optional, value_role, unit, color in CATALOG:
        page = pages[page_id]
        template_id = type.upper()
        project.templates.append(
            Template(
                id=template_id,
                required_bindings=list(required),
                optional_bindings=list(optional),
                metadata={"synthetic": True},
            )
        )
        if type in ("pump", "valve", "sensor"):
            top = {"pump": 154, "valve": 416, "sensor": 676}[type]
            columns, step_x, step_y, w, h = 10, 184, 112, 164, 94
            group_height = 330 if type == "sensor" else 220
        elif type == "ups":
            top, columns, step_x, step_y, w, h, group_height = 168, 6, 308, 162, 280, 134, 330
        else:
            top, columns, step_x, step_y, w, h, group_height = 622, 4, 450, 162, 400, 134, 330
        group = f"SECTION_{template_id}"
        add(page, group, "group", 0, top, 1920, group_height)
        label(
            page,
            f"SECTION_LABEL_{template_id}",
            f"{count:02d}  /  {type.upper()} EQUIPMENT",
            40,
            top - 38,
            size=18,
            color=color,
        )
        for i in range(1, count + 1):
            equipment_id, card_id = f"{prefix}_{i:02d}", f"{prefix}_CARD_{i:02d}"
            project.equipment.append(
                Equipment(
                    id=equipment_id,
                    name=f"Synthetic {type} {i:02d}",
                    type=type,
                    system=system,
                    metadata={"synthetic": True, "zone": f"ZONE_{(i - 1) // columns + 1:02d}"},
                )
            )
            roles = (*required, *optional)
            for role in roles:
                numeric = role in (
                    "speed",
                    "position",
                    "value",
                    "load",
                    "battery",
                    "voltage",
                    "fuel",
                    "power",
                )
                point_unit = (
                    unit
                    if role == value_role
                    else ({"battery": "%", "voltage": "V", "fuel": "%"}.get(role, ""))
                )
                project.points.append(
                    Point(
                        id=f"{equipment_id}_{role.upper()}",
                        tag=f"SYN.DC.{system}.{equipment_id}.{role.upper()}",
                        datatype="float" if numeric else "bool",
                        unit=point_unit,
                        equipment_ref=equipment_id,
                        role=role,
                        metadata={"synthetic": True},
                    )
                )
            bindings = [
                Binding(role=role, point_ref=f"{equipment_id}_{role.upper()}")
                for role in roles
                if not (card_id in MISSING_FAULT and role == "fault")
            ]
            x = 40 + ((i - 1) % columns) * step_x
            y = ((i - 1) // columns) * step_y
            # Deliberately uneven pump widths/offsets for the structured layout task.
            card_width = (156, 160, 168)[(i - 1) % 3] if type == "pump" else w
            x += 3 * ((i - 1) % 2) if type == "pump" else 0
            add(
                page,
                card_id,
                "equipment_card",
                x,
                y,
                card_width,
                h,
                content_width=164,
                content_height=94,
                parent_id=group,
                text=f"{prefix}-{i:02d}",
                equipment_ref=equipment_id,
                template_ref=template_id,
                bindings=bindings,
                style={"fill": "#203249", "stroke": color},
                z_index=2,
            )
            shared = dict(parent_id=card_id, equipment_ref=equipment_id)
            add(page, f"{equipment_id}_ICON", "opaque_svg", 132, 7, 24, 24, payload=icon(color), **shared)
            primary = "quality" if type == "sensor" else required[0]
            secondary = "alarm" if type == "sensor" else "fault"
            for role, text, xpos, width in (
                (primary, "QLT" if type == "sensor" else "STATE", 10, 65),
                (secondary, "ALM" if type == "sensor" else "FAULT", 82, 72),
            ):
                add(
                    page,
                    f"{equipment_id}_{role.upper()}_IND",
                    "status_indicator",
                    xpos,
                    35,
                    width,
                    22,
                    text=text,
                    bindings=[Binding(role=role, point_ref=f"{equipment_id}_{role.upper()}")],
                    style={"fill": "#35475b", "stroke": "#6b7f94"},
                    **shared,
                )
            add(
                page,
                f"{equipment_id}_VALUE",
                "data_slot",
                10,
                64,
                144,
                26,
                text=f"-- {unit}",
                bindings=[Binding(role=value_role, point_ref=f"{equipment_id}_{value_role.upper()}")],
                **shared,
            )
            add(
                page,
                f"{equipment_id}_DIVIDER",
                "shape",
                10,
                59,
                144,
                1,
                shape="line",
                style={"fill": "none", "stroke": "#496078", "stroke-width": 1},
                **shared,
            )
            # Explicit schematic segments between adjacent cards; not automatic routing.
            if i % columns != 0 and i < count:
                add(
                    page,
                    f"{prefix}_LINK_{i:02d}_{i + 1:02d}",
                    "connection",
                    40 + ((i - 1) % columns) * step_x + w,
                    y + h / 2,
                    step_x - w,
                    1,
                    parent_id=group,
                    source_ref=card_id,
                    target_ref=f"{prefix}_CARD_{i + 1:02d}",
                    style={"stroke": color, "stroke-width": 1, "opacity": 0.45},
                    z_index=1,
                )

    overview = pages["overview"]
    label(
        overview,
        "OV_CATALOG_LABEL",
        "ENGINEERING INVENTORY / 90 EQUIPMENT / 390 SYNTHETIC POINTS",
        40,
        133,
        size=22,
        color="#e2ecfa",
    )
    for index, (type, prefix, count, page, system, required, optional, role, unit, color) in enumerate(
        CATALOG
    ):
        card = f"OV_{type.upper()}"
        add(
            overview,
            card,
            "equipment_card",
            40 + index * 370,
            210,
            340,
            160,
            content_width=340,
            content_height=160,
            text=type.upper(),
            style={"fill": "#203249", "stroke": color},
        )
        label(
            overview,
            card + "_COUNT",
            f"{count:02d} equipment",
            20,
            65,
            250,
            size=30,
            color=color,
            parent_id=card,
        )
        label(
            overview,
            card + "_POINTS",
            f"{count * (len(required) + len(optional))} points / {system}",
            20,
            118,
            260,
            size=17,
            parent_id=card,
        )
        add(overview, card + "_ICON", "opaque_svg", 274, 18, 42, 42, parent_id=card, payload=icon(color))
    for id, title, x, color, text_lines in (
        (
            "OV_COOLING",
            "COOLING / 70 EQUIPMENT",
            40,
            "#27c4ad",
            (
                "20 pumps + 20 valves + 30 sensors",
                "290 registered synthetic points",
                "Pump cards 03, 07, 14 start without fault binding",
            ),
        ),
        (
            "OV_ELECTRICAL",
            "ELECTRICAL / 20 EQUIPMENT",
            1000,
            "#e7b85c",
            (
                "12 UPS + 8 generators",
                "100 registered synthetic points",
                "Explicit geometry and traceable semantic endpoints",
            ),
        ),
    ):
        add(
            overview,
            id,
            "equipment_card",
            x,
            510,
            860,
            310,
            content_width=860,
            content_height=310,
            text=title,
            style={"fill": "#192940", "stroke": color},
        )
        for index, text in enumerate(text_lines):
            label(overview, id + f"_LINE_{index}", text, 24, 80 + 62 * index, 810, size=21, parent_id=id)
    label(
        overview,
        "OV_WORKFLOW",
        "A QUERY  >  B BIND  >  C LAYOUT  >  D SVG EDIT  >  E DIFF  >  F UNDO  >  G EXPORT",
        40,
        915,
        size=23,
        color="#dce8fa",
    )
    badge = BytesIO()
    Image.new("RGBA", (8, 8), (39, 196, 173, 180)).save(badge, "PNG")
    add(
        overview,
        "OV_DESIGN_BADGE",
        "image_asset",
        1830,
        32,
        24,
        24,
        image_uri="data:image/png;base64," + base64.b64encode(badge.getvalue()).decode("ascii"),
    )
    return project


def statistics(project):
    return dict(
        pages={p.id: len(p.nodes) for p in project.pages},
        nodes=len(project.all_nodes()),
        node_kinds=dict(sorted(Counter(n.kind for n in project.all_nodes()).items())),
        equipment=dict(sorted(Counter(e.type for e in project.equipment).items())),
        points=len(project.points),
        templates=len(project.templates),
        canonical_hash=canonical_hash(project),
        missing_fault_cards=list(MISSING_FAULT),
    )


def fixture_data(project):
    raw = project.model_dump(mode="python")
    # Start independent broken fixtures from a fully bound baseline to isolate each rule.
    for page in raw["pages"]:
        for node in page["nodes"]:
            if node["id"] in MISSING_FAULT:
                node["bindings"].append(
                    Binding(role="fault", point_ref=node["equipment_ref"] + "_FAULT").model_dump()
                )
    results = {}
    for name in ("invalid-equipment", "invalid-point", "duplicate-id", "out-of-bounds", "invalid-geometry"):
        data = deepcopy(raw)
        index = {n["id"]: n for p in data["pages"] for n in p["nodes"]}
        if name == "invalid-equipment":
            for i in (1, 2):
                index[f"CHWP_CARD_{i:02d}"]["equipment_ref"] = f"MISSING_EQUIPMENT_{i}"
            expected, exit_code = {"INVALID_EQUIPMENT_REFERENCE": 2}, 2
        elif name == "invalid-point":
            for i in (1, 2):
                index[f"CHWP_CARD_{i:02d}"]["bindings"][0]["point_ref"] = f"MISSING_POINT_{i}"
            expected, exit_code = {"INVALID_POINT_REFERENCE": 2}, 2
        elif name == "duplicate-id":
            data["pages"][0]["nodes"].append(deepcopy(index["OV_WORKFLOW"]))
            expected, exit_code = {"DUPLICATE_ID": 1}, 2
        elif name == "out-of-bounds":
            index["TITLE_COOLING"]["x"] = 1910
            index["TITLE_ELECTRICAL"]["x"] = 1910
            expected, exit_code = {"OUT_OF_BOUNDS": 2}, 0
        else:
            index["CHWP_CARD_01"]["width"] = -1
            expected, exit_code = {"SCHEMA_VALIDATION": 1}, 2
        results[name] = dict(model=data, expected_issue_counts=expected, exit_code=exit_code)
    return results


def write_demo(directory):
    directory = Path(directory)
    project = make_project()
    atomic_write(directory / "model.json", project.model_dump_json(indent=2))
    atomic_write(directory / "statistics.json", json.dumps(statistics(project), indent=2))
    fixture_index = {}
    for name, fixture in fixture_data(project).items():
        atomic_write(
            directory / "broken" / (name + ".json"),
            json.dumps(fixture.pop("model"), indent=2, allow_nan=False),
        )
        fixture_index[name] = fixture
    atomic_write(directory / "broken/index.json", json.dumps(fixture_index, indent=2))
    return statistics(project)
