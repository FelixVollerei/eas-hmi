"""Small, fully synthetic all-kinds fixture; the large demo belongs to stage 5."""

import base64
from io import BytesIO
from pathlib import Path

from PIL import Image

from eas_hmi.model import Binding, Equipment, Node, Page, Point, Project, Template


def make_project():
    stream = BytesIO()
    Image.new("RGBA", (8, 8), (30, 180, 210, 160)).save(stream, "PNG")
    uri = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
    opaque = (Path(__file__).resolve().parents[1] / "examples/stage2/opaque-source.svg").read_text(
        encoding="utf-8"
    )

    def node(id, kind, x, y, w=100, h=60, **kwargs):
        return Node(
            id=id,
            kind=kind,
            page_id="components",
            x=x,
            y=y,
            width=w,
            height=h,
            content_width=w,
            content_height=h,
            **kwargs,
        )

    nodes = [
        node("GROUP", "group", 30, 20, 400, 100),
        node("TITLE", "text", 10, 10, 300, 30, parent_id="GROUP", text="Synthetic component catalog"),
        node("SHAPE", "shape", 40, 140, style={"fill": "#14b8a6"}),
        node("IMAGE", "image_asset", 200, 140, image_uri=uri),
        node("OPAQUE", "opaque_svg", 360, 140, 120, 100, payload=opaque),
        node(
            "CARD_A",
            "equipment_card",
            40,
            300,
            150,
            90,
            text="SYN-P01",
            equipment_ref="PUMP_01",
            template_ref="PUMP",
            bindings=[Binding(role="run", point_ref="P01_RUN")],
        ),
        node(
            "CARD_B",
            "equipment_card",
            270,
            320,
            180,
            90,
            text="SYN-P02",
            equipment_ref="PUMP_02",
            template_ref="PUMP",
            bindings=[Binding(role="run", point_ref="P02_RUN"), Binding(role="fault", point_ref="P02_FLT")],
        ),
        node(
            "VALUE",
            "data_slot",
            530,
            300,
            120,
            36,
            text="-- kPa",
            equipment_ref="SENSOR_01",
            template_ref="SENSOR",
            bindings=[Binding(role="value", point_ref="S01_VALUE")],
        ),
        node("STATUS", "status_indicator", 540, 380, 70, 32, text="--", equipment_ref="PUMP_01"),
        node("LINK", "connection", 190, 340, 80, 20, source_ref="CARD_A", target_ref="CARD_B"),
        node("LABEL", "text", 530, 250, 200, 32, text="Pressure · synthetic"),
    ]
    return Project(
        id="SYNTHETIC_COMPONENTS",
        name="Stage 4 synthetic component catalog",
        pages=[
            Page(id="components", name="Components", width=800, height=500, nodes=nodes),
            Page(id="spare", name="Empty page", width=320, height=180),
        ],
        equipment=[
            Equipment(id="PUMP_01", name="Synthetic pump 01", type="pump", system="CHW"),
            Equipment(id="PUMP_02", name="Synthetic pump 02", type="pump", system="CHW"),
            Equipment(id="SENSOR_01", name="Synthetic sensor 01", type="sensor", system="CHW"),
        ],
        points=[
            Point(
                id=f"P0{i}_{suffix}",
                tag=f"SYN.P0{i}.{suffix}",
                datatype="bool",
                equipment_ref=f"PUMP_0{i}",
                role=role,
            )
            for i in (1, 2)
            for suffix, role in (("RUN", "run"), ("FLT", "fault"))
        ]
        + [
            Point(
                id="S01_VALUE",
                tag="SYN.S01.PRESSURE",
                datatype="float",
                unit="kPa",
                equipment_ref="SENSOR_01",
            )
        ],
        templates=[
            Template(id="PUMP", required_bindings=["run", "fault"]),
            Template(id="SENSOR", required_bindings=["value"]),
        ],
    )


if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "examples/stage4/model.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(make_project().model_dump_json(indent=2), encoding="utf-8")
    print(path)
