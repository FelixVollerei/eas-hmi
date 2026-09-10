import hashlib
import json
from collections import Counter

import pytest

from eas_hmi.demo.synthetic import MISSING_FAULT, fixture_data, make_project, statistics, write_demo
from eas_hmi.model import Project, canonical_hash
from eas_hmi.operations import edit
from eas_hmi.operations.transaction import Store
from eas_hmi.query import Query
from eas_hmi.svg.common import safe_parse
from eas_hmi.svg.renderer import SUPPORTED_KINDS, render
from eas_hmi.svg.synchronizer import identity_map, plan_sync
from eas_hmi.validation import validate, validate_raw


@pytest.fixture
def demo():
    return make_project()


def test_minimum_scale_and_semantic_inventory(demo):
    stats = statistics(demo)
    assert stats["equipment"] == {"pump": 20, "valve": 20, "sensor": 30, "ups": 12, "generator": 8}
    assert stats["points"] == 390 and stats["nodes"] == 672
    assert stats["pages"] == {"overview": 35, "cooling": 493, "electrical": 144}
    assert set(stats["node_kinds"]) == SUPPORTED_KINDS
    assert stats["node_kinds"]["opaque_svg"] == 95
    assert len(Query(demo).select(page="cooling", kind="equipment_card", equipment_type="pump")) == 20
    assert {
        n.id
        for n in Query(demo).select(kind="equipment_card", equipment_type="pump", missing_binding="fault")
    } == set(MISSING_FAULT)


def test_all_ids_points_and_artwork_are_synthetic_and_consistent(demo):
    for objects in (demo.pages, demo.all_nodes(), demo.points, demo.equipment, demo.templates):
        assert len(objects) == len({item.id for item in objects})
    equipment = {e.id: e for e in demo.equipment}
    points = {p.id: p for p in demo.points}
    assert all(e.metadata["synthetic"] for e in equipment.values())
    assert all(p.tag.startswith("SYN.DC.") and p.metadata["synthetic"] for p in points.values())
    for node in demo.all_nodes():
        for binding in node.bindings:
            point = points[binding.point_ref]
            assert point.equipment_ref == node.equipment_ref and point.role == binding.role
        if node.kind == "opaque_svg":
            assert "linearGradient" in node.payload and "clipPath" in node.payload
    assert sum(len(n.bindings) for n in demo.all_nodes()) == 657


def test_only_intentional_initial_warnings_and_strict_errors(demo):
    issues = validate(demo)
    assert {i["object_id"] for i in issues} == set(MISSING_FAULT)
    assert Counter((i["code"], i["severity"]) for i in issues) == {("MISSING_REQUIRED_BINDING", "WARNING"): 3}
    assert Counter(i["severity"] for i in validate(demo, strict=True)) == {"ERROR": 3}
    assert not any(i["code"] == "OUT_OF_BOUNDS" for i in issues)


def test_deterministic_generator_and_delivered_fixture(tmp_path, demo):
    assert canonical_hash(demo) == canonical_hash(make_project())
    left, right = tmp_path / "a", tmp_path / "b"
    assert write_demo(left) == write_demo(right)
    for path in left.rglob("*.json"):
        assert path.read_bytes() == (right / path.relative_to(left)).read_bytes()
    restored = Project.model_validate_json((left / "model.json").read_bytes())
    assert canonical_hash(restored) == canonical_hash(demo)
    assert json.loads((left / "statistics.json").read_text())["nodes"] >= 500


@pytest.mark.parametrize("page", ["overview", "cooling", "electrical"])
def test_large_page_deterministic_render_and_no_op_sync(demo, page):
    before = canonical_hash(demo)
    rendered = render(demo, page)
    assert rendered == render(demo, page)
    assert len(identity_map(safe_parse(rendered))) == len(next(p for p in demo.pages if p.id == page).nodes)
    assert plan_sync(demo, rendered).changes == []
    assert canonical_hash(demo) == before


@pytest.mark.parametrize(
    "name", ["invalid-equipment", "invalid-point", "duplicate-id", "out-of-bounds", "invalid-geometry"]
)
def test_broken_fixtures_isolate_expected_rules(demo, name):
    baseline = canonical_hash(demo)
    fixture = fixture_data(demo)[name]
    raw = json.dumps(fixture["model"], sort_keys=True)
    issues = validate_raw(fixture["model"])
    assert dict(Counter(i["code"] for i in issues)) == fixture["expected_issue_counts"]
    assert (any(i["severity"] == "ERROR" for i in issues)) == (fixture["exit_code"] == 2)
    assert json.dumps(fixture["model"], sort_keys=True) == raw
    assert canonical_hash(demo) == baseline
    if name == "out-of-bounds":
        assert Counter(i["severity"] for i in validate_raw(fixture["model"], out_of_bounds="ERROR")) == {
            "ERROR": 2
        }


def test_large_batch_and_binding_fix_preserve_invariants(demo, tmp_path):
    state = Store(tmp_path / "state")
    state.initialize(demo)
    for id in MISSING_FAULT:
        equipment_id = Query(state.load()).node(id).equipment_ref
        state.transaction("bind", lambda p: edit.bind(p, id, "fault", equipment_id + "_FAULT"))
    assert validate(state.load(), strict=True) == []
    before = state.load()
    state.transaction(
        "width",
        lambda p: edit.batch(
            p,
            "set",
            {"page": "cooling", "kind": "equipment_card", "equipment_type": "pump"},
            property_name="width",
            value=164,
        ),
    )
    ids = [n.id for n in Query(before).select(page="cooling", kind="equipment_card", equipment_type="pump")]
    state.transaction("grid", lambda p: edit.distribute(p, ids, "x", 20, columns=10))
    arranged = state.load()
    assert validate(arranged, strict=True) == []
    for index, id in enumerate(ids):
        card = Query(arranged).node(id)
        assert (card.x, card.y, card.width, card.height) == (
            40 + index % 10 * 184,
            index // 10 * 114,
            164,
            94,
        )
    # Parent resizing moves descendants through the affine frame without rewriting their local values.
    for n in before.all_nodes():
        if n.id not in ids:
            assert n.model_dump() == Query(arranged).node(n.id).model_dump()
    assert arranged.revision == 5 and len(state.history()) == 6


def test_context_is_bounded_on_large_project(demo):
    c = Query(demo).context("CHWP_CARD_07", depth=2, limit=40)
    assert c["target"]["id"] == "CHWP_CARD_07"
    assert len(c["nodes"]) <= 40 and len(c["points"]) <= 40
    assert len(json.dumps(c)) < 75000
    assert "payload" not in json.dumps(c).replace("payload_omitted", "")
    equipment = Query(demo).inspect("CHWP_07")
    assert len(equipment["points"]) == 6 and len(equipment["nodes"]) == 6


def test_fixture_write_does_not_change_existing_engineering_store(tmp_path, demo):
    store = Store(tmp_path / "project")
    store.initialize(demo)
    before = hashlib.sha256(store.head.read_bytes()).hexdigest()
    write_demo(tmp_path / "inputs")
    assert hashlib.sha256(store.head.read_bytes()).hexdigest() == before
