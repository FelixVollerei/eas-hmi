from pathlib import Path

import pytest

from eas_hmi.model import Node, Page, Project


@pytest.fixture
def component_project():
    return Project.model_validate_json(
        (Path(__file__).parents[1] / "examples/stage4/model.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def opaque_source():
    return (Path(__file__).parent / "fixtures" / "opaque.svg").read_text(encoding="utf-8")


@pytest.fixture
def small_project(opaque_source):
    def node(id, kind, x, y, w, h, **kwargs):
        return Node(
            id=id,
            kind=kind,
            page_id="probe",
            x=x,
            y=y,
            width=w,
            height=h,
            content_width=w or 10,
            content_height=h or 10,
            **kwargs,
        )

    return Project(
        id="SYNTHETIC_PROBE",
        name="Stage 2 synthetic adapter probe",
        pages=[
            Page(
                id="probe",
                name="SVG round trip",
                width=800,
                height=480,
                background="#172131",
                nodes=[
                    node("BOX_A", "shape", 30, 30, 100, 60, style={"fill": "#17b890"}),
                    node("LABEL", "text", 30, 105, 220, 32, text="CHWP-07 · synthetic"),
                    node("ASSEMBLY", "group", 260, 30, 200, 120),
                    node("CHILD", "shape", 15, 15, 50, 30, parent_id="ASSEMBLY", style={"fill": "#e69b34"}),
                    node("OPAQUE_A", "opaque_svg", 30, 190, 120, 100, payload=opaque_source),
                    node("OPAQUE_B", "opaque_svg", 210, 190, 120, 100, payload=opaque_source),
                    node("ZERO", "shape", 0, 0, 0, 20),
                    node("HIDDEN", "shape", 500, 30, 40, 30, visible=False),
                ],
            )
        ],
    )
