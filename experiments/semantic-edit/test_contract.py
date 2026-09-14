"""Small invariant tests; the three real-image cases are the integration tests."""
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from probe_semantic_edit import dilate, feather, region_metrics, shift_mask, vector_patch


def test_untouched_metric_detects_one_channel_one_pixel_leak():
    baseline = np.zeros((20, 30, 3), dtype=np.uint8)
    actual = baseline.copy()
    editable = np.zeros((20, 30), bool)
    editable[5:10, 8:15] = True
    actual[editable] = 255
    assert region_metrics(Image.fromarray(baseline), Image.fromarray(actual), ~editable)['changed_pixels'] == 0
    actual[19, 29, 2] = 1
    result = region_metrics(Image.fromarray(baseline), Image.fromarray(actual), ~editable)
    assert result['changed_pixels'] == 1
    assert result['max_absolute_difference'] == 1
    assert result['mae_0_255'] == pytest.approx(1 / ((600-35)*3))


def test_feather_never_leaks_beyond_predeclared_margin():
    mask = np.zeros((30, 40), bool)
    mask[10:20, 10:25] = True
    alpha = feather(mask, 3)
    assert np.array_equal(alpha > 0, dilate(mask, 3))
    assert np.all(alpha[mask] == 1)
    assert np.all(alpha[~dilate(mask, 3)] == 0)


@pytest.mark.parametrize('dx,dy', [(-5, 0), (0, -5), (20, 0), (0, 20)])
def test_out_of_canvas_move_rejected_instead_of_wrapped(dx, dy):
    mask = np.zeros((10, 10), bool)
    mask[2:5, 2:5] = True
    with pytest.raises(ValueError, match='outside canvas'):
        shift_mask(mask, dx, dy)


def test_overlapping_translation_preserves_mask_area():
    mask = np.zeros((20, 20), bool)
    mask[5:10, 5:10] = True
    moved = shift_mask(mask, 2, 1)
    assert moved.sum() == 25
    assert np.array_equal(np.argwhere(moved), np.argwhere(mask) + [1, 2])


def test_vector_patch_keeps_sparse_mask_and_local_extent(tmp_path):
    from xml.etree import ElementTree as ET
    rgb = Image.new('RGB', (100, 100), (10, 20, 30))
    mask = np.zeros((100, 100), bool)
    mask[40:42, 60:64] = True
    mask[43, 65] = True
    path = tmp_path / 'patch.svg'
    box, segments = vector_patch(rgb, mask, path)
    assert box == [60, 40, 66, 44]
    assert segments == 2  # rectangle and isolated pixel; never fills the hole
    root = ET.parse(path).getroot()
    assert root.attrib['viewBox'] == '0 0 6 4'
    assert root[0].attrib['d'] == 'M0,0h4v2h-4zM5,3h1v1h-1z'
    assert not root.findall('.//{http://www.w3.org/2000/svg}image')


def test_empty_untouched_region_is_not_a_zero_error_success():
    image = Image.new('RGB', (5, 5))
    with pytest.raises(ValueError, match='empty region'):
        region_metrics(image, image, np.zeros((5, 5), bool))
