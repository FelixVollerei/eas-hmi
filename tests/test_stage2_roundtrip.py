from copy import deepcopy

import pytest
from lxml import etree

from eas_hmi.errors import EngineeringError
from eas_hmi.geometry import node_matrix, world_matrix
from eas_hmi.model import canonical_hash
from eas_hmi.operations.transaction import Store
from eas_hmi.svg.renderer import render
from eas_hmi.svg.synchronizer import frame_of, plan_sync


def element(root, id):
    return root.xpath("//*[@data-eas-id=$id]", id=id)[0]


def node(project, id):
    return next(n for n in project.all_nodes() if n.id == id)


def edit(project, id, **attrs):
    root = etree.fromstring(render(project, "probe"))
    target = element(root, id)
    for k, v in attrs.items():
        (frame_of(target) if k in ("x", "y", "width", "height", "viewBox") else target).set(k, str(v))
    return etree.tostring(root)


def test_render_deterministic_no_op_including_zero_and_opaque(small_project, tmp_path):
    before = canonical_hash(small_project)
    data = render(small_project, "probe")
    assert data == render(small_project, "probe")
    assert canonical_hash(small_project) == before
    plan = plan_sync(small_project, data)
    assert plan.changes == []
    assert canonical_hash(plan.candidate) == before
    store = Store(tmp_path / "project")
    store.initialize(small_project)
    head, history = store.head.read_bytes(), store.history()
    result = store.transaction("sync no-op probe", plan.apply, expected_revision=0, actor="human")
    assert result["committed"] is False
    assert store.head.read_bytes() == head
    assert store.history() == history


@pytest.mark.parametrize(
    "attrs,expected",
    [
        ({"x": "74", "y": "49"}, (74, 49, 100, 60, 0)),
        ({"width": "180", "height": "90"}, (30, 30, 180, 90, 0)),
        ({"transform": "translate(12,-4)"}, (42, 26, 100, 60, 0)),
        ({"transform": "rotate(30 30 30)"}, (30, 30, 100, 60, 30)),
        ({"transform": "translate(30 30) scale(2 3) translate(-30 -30)"}, (30, 30, 200, 180, 0)),
        ({"transform": "matrix(0 2 -3 0 300 10)"}, (210, 70, 200, 180, 90)),
        ({"x": "30px", "y": "40px"}, (30, 40, 100, 60, 0)),
    ],
)
def test_supported_geometry(small_project, attrs, expected):
    plan = plan_sync(small_project, edit(small_project, "BOX_A", **attrs))
    n = node(plan.candidate, "BOX_A")
    assert (n.x, n.y, n.width, n.height, n.rotation) == pytest.approx(expected)
    assert canonical_hash(small_project) != canonical_hash(plan.candidate)
    assert plan_sync(plan.candidate, render(plan.candidate, "probe")).changes == []


def test_nested_group_resize_does_not_double_transform_child(small_project):
    root = etree.fromstring(render(small_project, "probe"))
    frame_of(element(root, "ASSEMBLY")).set("width", "400")
    element(root, "ASSEMBLY").set("transform", "rotate(15 260 30)")
    frame_of(element(root, "CHILD")).set("x", "25")
    result = plan_sync(small_project, etree.tostring(root)).candidate
    child = node(result, "CHILD")
    assert (child.x, child.y, child.width, child.height) == (25, 15, 50, 30)
    index = {n.id: n for n in result.all_nodes()}
    assert world_matrix(child, index) != node_matrix(child)
    assert plan_sync(result, render(result, "probe")).changes == []


@pytest.mark.parametrize(
    "style,visible",
    [
        ("display:none", False),
        ("visibility:hidden", False),
        ("visibility:visible", True),
        ("display:inline", True),
    ],
)
def test_visibility(small_project, style, visible):
    original = "HIDDEN" if visible else "BOX_A"
    root = etree.fromstring(render(small_project, "probe"))
    e = element(root, original)
    e.attrib.pop("display", None)
    e.set("style", style)
    result = plan_sync(small_project, etree.tostring(root)).candidate
    assert node(result, original).visible == visible


def test_parent_hidden_preserves_child_own_visibility(small_project):
    plan = plan_sync(small_project, edit(small_project, "ASSEMBLY", display="none"))
    assert node(plan.candidate, "ASSEMBLY").visible is False
    assert node(plan.candidate, "CHILD").visible is True


@pytest.mark.parametrize("as_tspan", [False, True])
def test_text_plain_and_tspan(small_project, as_tspan):
    root = etree.fromstring(render(small_project, "probe"))
    text = frame_of(element(root, "LABEL"))[0]
    if as_tspan:
        text.text = None
        etree.SubElement(text, "{http://www.w3.org/2000/svg}tspan").text = "人工修改 — pump 07"
    else:
        text.text = "人工修改 — pump 07"
    candidate = plan_sync(small_project, etree.tostring(root)).candidate
    assert node(candidate, "LABEL").text == "人工修改 — pump 07"
    assert node(small_project, "LABEL").text != node(candidate, "LABEL").text


@pytest.mark.parametrize(
    "attrs",
    [
        {"transform": "skewX(20)"},
        {"transform": "scale(-1 1)"},
        {"transform": "matrix(1 0 0.5 1 0 0)"},
        {"transform": "scale(0 1)"},
        {"width": "-4"},
        {"x": "NaN"},
        {"x": "10%"},
        {"viewBox": "0 0 50 40"},
        {"style": "opacity:0.4"},
        {"transform": "translate(1)junk"},
    ],
)
def test_unsafe_rejected_without_partial_candidate(small_project, attrs):
    root = etree.fromstring(edit(small_project, "BOX_A", **attrs))
    frame_of(element(root, "LABEL"))[0].text = "This valid change must not sneak through"
    before = canonical_hash(small_project)
    with pytest.raises(EngineeringError) as caught:
        plan_sync(small_project, etree.tostring(root))
    assert caught.value.code == "UNSUPPORTED_HUMAN_EDIT"
    assert caught.value.details["node"] == "BOX_A"
    assert "attribute" in caught.value.details
    assert canonical_hash(small_project) == before


@pytest.mark.parametrize(
    "mode", ["stale", "project", "page", "duplicate", "missing", "reparent", "new", "root-scale"]
)
def test_identity_and_structure_guard(small_project, mode):
    root = etree.fromstring(render(small_project, "probe"))
    if mode in ("stale", "project", "page"):
        root.set(
            {"stale": "data-eas-revision", "project": "data-eas-project", "page": "data-eas-page"}[mode],
            "wrong",
        )
    elif mode == "duplicate":
        root.append(deepcopy(element(root, "BOX_A")))
    elif mode == "missing":
        element(root, "BOX_A").attrib.pop("data-eas-id")
    elif mode == "reparent":
        element(root, "ASSEMBLY").append(element(root, "BOX_A"))
    elif mode == "new":
        etree.SubElement(root, "{http://www.w3.org/2000/svg}rect", width="10", height="10")
    else:
        root.set("viewBox", "0 0 1600 960")
    before = canonical_hash(small_project)
    with pytest.raises(EngineeringError):
        plan_sync(small_project, etree.tostring(root))
    assert canonical_hash(small_project) == before


def test_opaque_art_edit_retained_and_no_op_after_rerender(small_project):
    root = etree.fromstring(render(small_project, "probe"))
    path = element(root, "OPAQUE_A").xpath('.//*[local-name()="path"]')[0]
    path.set("d", "M10 10L100 10L60 90Z")
    plan = plan_sync(small_project, etree.tostring(root))
    assert "M10 10L100 10L60 90Z" in node(plan.candidate, "OPAQUE_A").payload
    assert node(plan.candidate, "OPAQUE_B").payload == node(small_project, "OPAQUE_B").payload
    assert plan_sync(plan.candidate, render(plan.candidate, "probe")).changes == []


def test_plan_refuses_reuse_after_state_changed(small_project):
    plan = plan_sync(small_project, edit(small_project, "BOX_A", x="50"))
    changed = small_project.model_copy(deep=True)
    node(changed, "LABEL").text = "Concurrently changed"
    with pytest.raises(EngineeringError, match="changed"):
        plan.apply(changed)
