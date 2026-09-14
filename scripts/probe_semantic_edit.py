"""Bounded, reproducible lazy semantic edit experiment; no EAS core changes.

Ground truth stays immutable. Only masked local patches are vectorized. Final
metrics are computed from the actual EAS/CLI/Inkscape output, not a masked diff.
Manual/Agent-assisted polygons and generation provenance are explicit inputs.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from itertools import pairwise
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from eas_hmi.model import Node
from eas_hmi.svg.importer import import_svg
from eas_hmi.svg.renderer import render

SVG = 'http://www.w3.org/2000/svg'
ET.register_namespace('', SVG)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rgb(path):
    with Image.open(path) as im:
        rgba = im.convert('RGBA')
        if rgba.getchannel('A').getextrema() != (255, 255):
            raise ValueError(f'Opaque reference/output required: {path}')
        return rgba.convert('RGB')


def mask_image(mask):
    return Image.fromarray(mask.astype(np.uint8) * 255)


def bbox(mask):
    yy, xx = np.where(mask)
    if not len(xx):
        raise ValueError('Empty mask')
    return [int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1]


def dilate(mask, radius):
    if radius == 0:
        return mask.copy()
    return np.asarray(mask_image(mask).filter(ImageFilter.MaxFilter(2 * radius + 1))) > 0


def shift_mask(mask, dx, dy):
    yy, xx = np.where(mask)
    if not len(xx) or (xx + dx).min() < 0 or (yy + dy).min() < 0:
        raise ValueError('Target mask outside canvas or empty')
    if (xx + dx).max() >= mask.shape[1] or (yy + dy).max() >= mask.shape[0]:
        raise ValueError('Target mask outside canvas')
    out = np.zeros_like(mask)
    out[yy + dy, xx + dx] = True
    return out


def region_metrics(reference, actual, region):
    if not region.any():
        raise ValueError('Cannot assess empty region')
    error = np.abs(np.asarray(reference, dtype=np.int16) - np.asarray(actual, dtype=np.int16))
    selected = error[region]
    changed = np.any(selected != 0, axis=1)
    return {'pixels': int(region.sum()), 'mae_0_255': float(selected.mean()),
            'changed_pixels': int(changed.sum()), 'changed_pixel_ratio': float(changed.mean()),
            'changed_pixel_percent': float(changed.mean() * 100),
            'max_absolute_difference': int(selected.max())}


def vector_patch(rgb, mask, path):
    """Only the masked ROI, with transparent remainder. Integer pixel geometry.

    Horizontal equal-color runs merged vertically; count subpaths, not just
    palette paths. No trace of the full composite and no bitmap embedding.
    """
    box = bbox(mask)
    x0, y0, x1, y1 = box
    a = np.asarray(rgb, dtype=np.uint32)[y0:y1, x0:x1]
    active_mask = mask[y0:y1, x0:x1]
    packed = ((a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]).astype(np.int64)
    packed[~active_mask] = -1
    active, done = {}, {}
    def finish(key, span):
        left, right, color = key
        top, bottom = span
        done.setdefault(color, []).append(f'M{left},{top}h{right-left}v{bottom-top}h{left-right}z')
    for y, row in enumerate(packed):
        splits = np.r_[0, np.flatnonzero(row[1:] != row[:-1]) + 1, len(row)]
        current = {}
        for left, right in pairwise(splits):
            color = int(row[left])
            if color < 0:
                continue
            key = int(left), int(right), color
            previous = active.pop(key, None)
            current[key] = (previous[0] if previous else y, y + 1)
        for key, span in active.items():
            finish(key, span)
        active = current
    for key, span in active.items():
        finish(key, span)
    root = ET.Element(f'{{{SVG}}}svg', width=str(x1-x0), height=str(y1-y0),
                      viewBox=f'0 0 {x1-x0} {y1-y0}', attrib={'shape-rendering': 'crispEdges'})
    for color, runs in sorted(done.items()):
        ET.SubElement(root, f'{{{SVG}}}path', fill=f'#{color:06x}', d=''.join(runs))
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)
    return box, sum(map(len, done.values()))


def patch_node(rgb, mask, path, node_id, page_id, z_index, visible=True):
    box, segments = vector_patch(rgb, mask, path)
    x0, y0, x1, y1 = box
    return Node(id=node_id, kind='opaque_svg', page_id=page_id, x=x0, y=y0,
                width=x1-x0, height=y1-y0, content_width=x1-x0, content_height=y1-y0,
                z_index=z_index, payload=path.read_text(encoding='utf-8'), visible=visible), segments


def feather(mask, radius):
    """Nonzero alpha only inside the PREDECLARED dilated edit support."""
    if radius == 0:
        return mask.astype(np.float64)
    alpha = mask.astype(np.float64)
    for r in range(1, radius + 1):
        ring = dilate(mask, r) & ~dilate(mask, r - 1)
        alpha[ring] = (radius + 1 - r) / (radius + 1)
    return alpha


def preview(output, title, original, baseline, edited, semantic, editable, zoom):
    a, b = np.asarray(baseline), np.asarray(edited)
    delta = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    heat = np.zeros_like(a)
    heat[..., 0] = np.minimum(delta * 8, 255)
    heat[..., 1] = np.minimum(delta * 2, 255)
    untouched = heat.copy()
    untouched[editable] = 0
    Image.fromarray(heat).save(output / 'diff.png')
    Image.fromarray(untouched).save(output / 'untouched-diff.png')
    mask_image(delta > 0).save(output / 'changed-mask.png')
    tinted = a.copy()
    tinted[editable] = np.round(.55 * a[editable] + .45 * np.array([20, 150, 255])).astype(np.uint8)
    tinted[semantic] = np.round(.45 * a[semantic] + .55 * np.array([255, 40, 60])).astype(np.uint8)
    frames = [('Original / 输入原图', original), ('Baseline / 64 色真值', baseline),
              ('Mask / 红：对象 蓝：允许编辑', Image.fromarray(tinted)),
              ('Edited / EAS 实际渲染', edited), ('Diff / 最大通道差 ×8', Image.fromarray(heat)),
              ('Untouched Diff / 黑色为零差异', Image.fromarray(untouched))]
    font_path = r'C:\Windows\Fonts\msyh.ttc'
    def sheet(path, crop=None):
        cw, ch = (520, 560) if crop is None else (520, 430)
        sheet = Image.new('RGB', (cw * 3 + 80, (ch + 55) * 2 + 100), '#edf2f8')
        d = ImageDraw.Draw(sheet)
        d.text((20, 16), title + (' / 局部最近邻放大' if crop else ' / 全图'),
               font=ImageFont.truetype(font_path, 28), fill='#16243a')
        for i, (label, image) in enumerate(frames):
            x, y = 20 + i % 3 * (cw + 20), 70 + i // 3 * (ch + 55)
            d.text((x, y), label, font=ImageFont.truetype(font_path, 22), fill='#16243a')
            pic = image.crop(crop) if crop else image.copy()
            scale = min(cw / pic.width, ch / pic.height)
            pic = pic.resize((max(1, int(pic.width * scale)), max(1, int(pic.height * scale))),
                             Image.Resampling.NEAREST if crop else Image.Resampling.LANCZOS)
            sheet.paste(pic, (x + (cw-pic.width)//2, y+35+(ch-pic.height)//2))
        sheet.save(path)
    sheet(output / 'comparison.png')
    sheet(output / 'detail.png', zoom)


def run_probe(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    commands = []
    started = time.perf_counter()
    save(out / 'run-status.json', {'status': 'running'})
    config = json.loads(args.config.read_text(encoding='utf-8'))
    save(out / 'config.json', config)
    save(out / 'invocation.json', {'argv': sys.argv, 'cwd': str(Path.cwd()),
                                  'script_sha256': digest(Path(__file__)),
                                  'versions': {n: importlib.metadata.version(n) for n in ('Pillow', 'numpy', 'eas-hmi')}})
    def run(name, argv):
        t = time.perf_counter()
        try:
            p = subprocess.run(list(map(str, argv)), capture_output=True, timeout=180, check=False,
                               env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1'))
            (out / (name + '.stdout')).write_bytes(p.stdout)
            (out / (name + '.stderr')).write_bytes(p.stderr)
            commands.append({'name': name, 'argv': list(map(str, argv)), 'exit_code': p.returncode,
                             'seconds': time.perf_counter()-t})
            save(out / 'commands.json', commands)
            if p.returncode:
                raise RuntimeError(f'{name}: exit {p.returncode}; no automatic retry')
        except subprocess.TimeoutExpired:
            commands.append({'name': name, 'argv': list(map(str, argv)), 'timeout_seconds': 180})
            save(out / 'commands.json', commands)
            raise
    def raster(svg, png, name):
        run(name, [args.inkscape, svg, '--export-area-page', '--export-type=png',
                   f'--export-width={baseline.width}', f'--export-height={baseline.height}',
                   '--export-png-color-mode=RGBA_8', f'--export-filename={png}'])
    try:
        original = read_rgb(args.input)
        baseline = read_rgb(args.baseline_png)
        if original.size != baseline.size:
            raise ValueError('Input and baseline dimensions differ')
        original.save(out / 'input.png')
        shutil.copy2(args.baseline_png, out / 'baseline.png')
        shutil.copy2(args.baseline_svg, out / 'baseline.svg')
        originals = {str(p.resolve()): digest(p) for p in (args.input, args.baseline_png, args.baseline_svg)}
        base_hash = digest(out / 'baseline.svg')
        shape = baseline.height, baseline.width
        polygon = config['polygon']
        if len(polygon) < 3 or any(len(p) != 2 or any(type(v) is not int for v in p) for p in polygon):
            raise ValueError('Polygon requires at least three integer coordinate pairs')
        if any(not (0 <= x < baseline.width and 0 <= y < baseline.height) for x, y in polygon):
            raise ValueError('Polygon outside canvas; silent clipping is not allowed')
        drawing = Image.new('L', baseline.size)
        ImageDraw.Draw(drawing).polygon([tuple(v) for v in config['polygon']], fill=255)
        mask = np.asarray(drawing) > 0
        box = bbox(mask)
        radius = int(config.get('margin_px', 0))
        if not 0 <= radius <= 8:
            raise ValueError('POC margin must be 0..8, declared before editing')
        mask_image(mask).save(out / 'object-mask.png')
        baseline.crop(box).save(out / 'source-region.png')
        extracted = baseline.crop(box).convert('RGBA')
        extracted.putalpha(mask_image(mask).crop(box))
        extracted.save(out / 'object-extracted.png')
        scene = {'schema': 'eas-lazy-semantic-poc/1', 'canvas': list(baseline.size),
                 'baseline': {'svg': 'baseline.svg', 'png': 'baseline.png', 'sha256': base_hash,
                              'immutable': True, 'reference': 'previous verified 64-color pixel-faithful EAS output',
                              'original_input': 'input.png', 'originals_sha256': originals},
                 'semantic_objects': [],
                 'residual_scene': {'base': 'baseline.svg', 'local_overlays': [],
                                    'strategy': 'immutable underlay plus bounded occlusion/repair; no whole-image retracing'},
                 'history': []}
        save(out / 'scene-initial.json', scene)
        semantic_id = 'semantic-object-001'
        obj = {'id': semantic_id, 'semantic_label': config['semantic_label'], 'bbox': box,
               'mask': 'object-mask.png', 'source_region': 'source-region.png',
               'extracted': 'object-extracted.png', 'vector_payload': 'object.svg',
               'transform': {'type': 'translation', 'dx': 0, 'dy': 0},
               'metadata': {'segmentation': 'Agent-assisted manually specified polygon; no automatic segmentation claim',
                            'polygon': config['polygon'], 'instruction': config['instruction']}}
        scene['semantic_objects'].append(obj)
        scene['history'].append({'operation': 'promote', 'object': semantic_id, 'new_objects': 1})
        project = import_svg((out / 'baseline.svg').read_bytes()).project
        page = project.pages[0]
        if len(project.pages) != 1 or (page.width, page.height) != baseline.size:
            raise ValueError('Expected matching single-page EAS baseline')
        for n in page.nodes:
            n.locked = True
        initial_payloads = {n.id: hashlib.sha256((n.payload or '').encode()).hexdigest() for n in page.nodes}
        object_node, object_segments = patch_node(baseline, mask, out / 'object.svg', semantic_id, page.id, 20)
        page.nodes.append(object_node)
        expected = np.asarray(baseline).copy()
        provider = None
        target = np.zeros(shape, dtype=bool)
        repair_mask = np.zeros(shape, dtype=bool)
        if config['operation'] == 'move':
            dx, dy = config['translation']
            if not isinstance(dx, int) or not isinstance(dy, int) or (dx == 0 and dy == 0):
                raise ValueError('POC move requires nonzero integer translation')
            target = shift_mask(mask, dx, dy)
            repair_mask = dilate(mask, radius)
            editable = repair_mask | target
            donor_box = config['repair_donor_bbox']
            x0, y0, x1, y1 = donor_box
            if not (0 <= x0 < x1 <= baseline.width and 0 <= y0 < y1 <= baseline.height):
                raise ValueError('Repair donor out of bounds')
            if mask[y0:y1, x0:x1].any():
                raise ValueError('Donor overlaps extracted object')
            donor = np.asarray(baseline)[y0:y1, x0:x1]
            Image.fromarray(donor).save(out / 'repair-donor.png')
            yy, xx = np.where(repair_mask)
            repaired = np.asarray(baseline).copy()
            repaired[yy, xx] = donor[(yy-y0) % (y1-y0), (xx-x0) % (x1-x0)]
            repaired_im = Image.fromarray(repaired)
            repaired_im.save(out / 'background-repaired.png')
            overlay, repair_segments = patch_node(repaired_im, repair_mask, out / 'repair.svg',
                                                  'residual-repair-001', page.id, 10, False)
            page.nodes.append(overlay)
            expected = repaired.copy()
            sy, sx = np.where(mask)
            expected[sy+dy, sx+dx] = np.asarray(baseline)[sy, sx]
            scene['residual_scene']['repair_method'] = {'name': 'deterministic tiled clone',
                    'donor_bbox': donor_box, 'blending': 'none', 'hidden_background_ground_truth': None,
                    'quality_claim': 'local texture estimate, not recovery of actual hidden floor'}
        elif config['operation'] == 'patch':
            editable = dilate(mask, radius)
            context_box = config['context_bbox']
            cx0, cy0, cx1, cy1 = context_box
            if not (0 <= cx0 < cx1 <= baseline.width and 0 <= cy0 < cy1 <= baseline.height):
                raise ValueError('Context outside image')
            eb = bbox(editable)
            if eb[0] < cx0 or eb[1] < cy0 or eb[2] > cx1 or eb[3] > cy1:
                raise ValueError('Editable mask is not inside context')
            context = baseline.crop(context_box)
            context.save(out / 'context.png')
            mask_image(mask).crop(context_box).save(out / 'context-mask.png')
            save(out / 'patch-request.json', {'context': 'context.png', 'mask': 'context-mask.png',
                    'context_bbox_global': context_box, 'prompt': config['instruction'],
                    'provider_input_scope': 'cropped patch only; never full image',
                    'output_contract': 'return same framing; explicit resize required if size differs',
                    'writeback': 'program-enforced mask plus predeclared margin'})
            if args.provider_patch:
                if not args.provider_record:
                    raise ValueError('External patch requires a provenance record')
                raw = read_rgb(args.provider_patch)
                raw.save(out / 'provider-raw.png')
                record = json.loads(args.provider_record.read_text(encoding='utf-8'))
                if record.get('input_sha256') != digest(out / 'context.png'):
                    raise ValueError('Provider provenance input hash does not match context')
                save(out / 'provider-provenance.json', record)
                if raw.size != context.size and not args.allow_patch_resize:
                    raise ValueError('Provider returned wrong dimensions; explicit --allow-patch-resize needed')
                candidate = raw.resize(context.size, Image.Resampling.LANCZOS) if raw.size != context.size else raw
                provider = {'kind': 'external-generated-patch', 'raw_size': list(raw.size),
                            'normalized_size': list(context.size), 'resize': 'LANCZOS' if raw.size != context.size else None,
                            'raw_sha256': digest(out / 'provider-raw.png'), 'provenance': record,
                            'generative_quality': 'requires visual review; one stochastic call is not a benchmark'}
            else:
                # Explicit mock. No model ability is claimed; the provider also
                # perturbs all surrounding context to test the writeback barrier.
                arr = np.asarray(context).copy().astype(np.int16)
                arr = np.clip(arr + np.array([2, -1, 1]), 0, 255).astype(np.uint8)
                candidate = Image.fromarray(arr)
                local_polygon = [(x-cx0, y-cy0) for x, y in config['polygon']]
                draw = ImageDraw.Draw(candidate)
                skin = baseline.getpixel(tuple(config['skin_sample']))
                draw.polygon(local_polygon, fill=skin)
                points = [(x-cx0, y-cy0) for x, y in config['mock_smile_points']]
                draw.line(points, fill=tuple(config['ink_rgb']), width=3)
                if config.get('mock_chin_points'):
                    draw.line([(x-cx0, y-cy0) for x, y in config['mock_chin_points']],
                              fill=tuple(config['ink_rgb']), width=4)
                provider = {'kind': 'deterministic-mock', 'model_called': False,
                            'generative_quality': 'UNVERIFIED: drawn smile is only a mock',
                            'outside_mask_context_tint': [2, -1, 1]}
            candidate.save(out / 'provider-normalized.png')
            local_mask = mask[cy0:cy1, cx0:cx1]
            provider['outside_requested_mask_before_writeback'] = region_metrics(context, candidate, ~local_mask)
            alpha = feather(mask, radius)[cy0:cy1, cx0:cx1, None]
            mixed = np.rint(np.asarray(context) * (1-alpha) + np.asarray(candidate) * alpha).astype(np.uint8)
            expected[cy0:cy1, cx0:cx1] = mixed
            Image.fromarray(expected).save(out / 'patch-composited.png')
            baseline.save(out / 'background-repaired.png')
            overlay, repair_segments = patch_node(Image.fromarray(expected), editable, out / 'patch.svg',
                                                  'semantic-edit-001', page.id, 30, False)
            page.nodes.append(overlay)
            scene['residual_scene']['repair_method'] = {'name': 'mouth replacement inside patch', 'separate_repair': False}
        else:
            raise ValueError('Only move and patch are supported in this POC')
        # Fixed support computed from declared inputs BEFORE EAS operations;
        # never expanded from observed differences to make metrics pass.
        if editable.mean() > .1:
            raise ValueError('This bounded POC disallows edit support larger than 10% of image')
        mask_image(editable).save(out / 'editable-mask.png')
        mask_image(~editable).save(out / 'untouched-mask.png')
        mask_image(repair_mask).save(out / 'repair-mask.png')
        mask_image(target).save(out / 'target-mask.png')
        scene['edit_contract'] = {'editable_mask': 'editable-mask.png', 'sha256': digest(out / 'editable-mask.png'),
                                 'margin_px': radius, 'fixed_before_edit': True,
                                 'untouched_mae_target': 0, 'untouched_changed_pixel_target': 0,
                                 'untouched_max_difference_target': 0, 'max_allowed_edit_area_percent': 10}
        save(out / 'scene-promoted.json', scene)
        (out / 'promoted.svg').write_bytes(render(project, page.id))
        save(out / 'promoted-project.json', project.model_dump(mode='json'))
        cli = [sys.executable, '-X', 'faulthandler', '-m', 'eas_hmi.cli', '--project', out / 'eas-project']
        run('import-promoted', [*cli, 'import-svg', out / 'promoted.svg'])
        run('render-promoted', [*cli, 'render', '--output', out / 'promoted-eas.svg'])
        raster(out / 'baseline.svg', out / 'baseline-verified.png', 'raster-baseline')
        raster(out / 'promoted-eas.svg', out / 'promoted.png', 'raster-promoted')
        if config['operation'] == 'move':
            run('enable-repair', [*cli, 'set', 'residual-repair-001', 'visible', 'true'])
            run('move-object', [*cli, 'move', semantic_id, '--dx', dx, '--dy', dy])
            obj['transform'].update(dx=dx, dy=dy)
            obj['target_bbox'] = bbox(target)
            scene['residual_scene']['local_overlays'].append({'node': 'residual-repair-001', 'mask': 'repair-mask.png'})
        else:
            run('hide-old-mouth', [*cli, 'set', semantic_id, 'visible', 'false'])
            run('enable-patch', [*cli, 'set', 'semantic-edit-001', 'visible', 'true'])
            obj['replacement'] = {'node': 'semantic-edit-001', 'vector_payload': 'patch.svg', 'provider': provider}
        run('render-edited', [*cli, 'render', '--output', out / 'edited.svg'])
        run('validate-strict', [*cli, 'validate', '--strict'])
        raster(out / 'edited.svg', out / 'edited.png', 'raster-edited')
        actual = read_rgb(out / 'edited.png')
        promotion = read_rgb(out / 'promoted.png')
        full = np.ones(shape, dtype=bool)
        untouched = region_metrics(baseline, actual, ~editable)
        promotion_metrics = region_metrics(baseline, promotion, full)
        output_project = import_svg((out / 'edited.svg').read_bytes()).project
        final_payloads = {n.id: hashlib.sha256((n.payload or '').encode()).hexdigest()
                          for n in output_project.pages[0].nodes if n.id in initial_payloads}
        data = {'reference': 'baseline.png, NOT original pre-quantization input.png',
                'baseline_vs_input': region_metrics(original, baseline, full),
                'untouched_vs_original_input': region_metrics(original, actual, ~editable),
                'baseline_rerender': region_metrics(baseline, read_rgb(out / 'baseline-verified.png'), full),
                'promotion_vs_baseline': promotion_metrics, 'untouched': untouched,
                'edited_region': region_metrics(baseline, actual, editable),
                'renderer_vs_expected_composite': region_metrics(Image.fromarray(expected), actual, full),
                'edit_mask_pixels': int(editable.sum()), 'edit_mask_area_percent': float(editable.mean()*100),
                'object_mask_pixels': int(mask.sum()), 'object_vector_subpaths': object_segments,
                'local_overlay_subpaths': repair_segments, 'provider': provider,
                'immutable_input_hashes_preserved': all(digest(Path(p)) == h for p, h in originals.items()),
                'baseline_copy_hash_preserved': digest(out / 'baseline.svg') == base_hash,
                'baseline_eas_payload_hashes_preserved': initial_payloads == final_payloads,
                'baseline_payload_hashes_before': initial_payloads, 'baseline_payload_hashes_after': final_payloads,
                'future_edit_success_axes': {'instruction_following': 'manual review',
                      'edited_region_quality': 'manual review; do not infer from untouched metrics',
                      'untouched_region_fidelity': untouched},
                'token_count': None, 'model_generation_reproducibility': 'replay saved patch; model re-sampling is not deterministic'}
        if config['operation'] == 'move':
            sy, sx = np.where(mask)
            object_error = np.abs(np.asarray(baseline, dtype=np.int16)[sy, sx] -
                                  np.asarray(actual, dtype=np.int16)[sy+dy, sx+dx])
            vacated = repair_mask & ~target
            data['structural'] = {'operation': 'move', 'translation': [dx, dy],
                'object_extracted': True, 'object_appearance_mae': float(object_error.mean()),
                'object_appearance_max_difference': int(object_error.max()),
                'object_appearance_changed_pixel_ratio': float(np.any(object_error != 0, axis=1).mean()),
                'target_placement_vs_expected': region_metrics(Image.fromarray(expected), actual, target),
                'vacated_source_pixels': int(vacated.sum()),
                'source_repair_vs_estimate': region_metrics(repaired_im, actual, vacated),
                'vacated_source_vs_baseline': region_metrics(baseline, actual, vacated),
                'source_repair_vs_true_hidden_background': None,
                'occlusion_at_target': 'target-covered baseline pixels retained in immutable underlay; no 3D reasoning',
                'edge_seam_quality': 'requires detail.png review; tile repair has no blending'}
        boundary = dilate(editable, 1) & ~editable
        data['outside_boundary_band'] = region_metrics(baseline, actual, boundary)
        # Positive/negative controls run against the same final metric function.
        tampered = np.asarray(actual).copy()
        ny, nx = np.argwhere(~editable)[0]
        tampered[ny, nx, 0] ^= np.uint8(1)
        negative = region_metrics(baseline, Image.fromarray(tampered), ~editable)
        save(out / 'negative-control.json', {'operation': 'flip one red-channel bit outside edit mask',
                    'coordinate': [int(nx), int(ny)], 'metrics': negative,
                    'detected': negative['changed_pixels'] > untouched['changed_pixels']})
        data['invariant_pass'] = all([
            untouched['changed_pixels'] == 0, promotion_metrics['changed_pixels'] == 0,
            data['baseline_rerender']['changed_pixels'] == 0,
            data['renderer_vs_expected_composite']['changed_pixels'] == 0,
            data['immutable_input_hashes_preserved'], data['baseline_copy_hash_preserved'],
            data['baseline_eas_payload_hashes_preserved'],
            negative['changed_pixels'] > untouched['changed_pixels']])
        data['seconds_excluding_external_generation'] = time.perf_counter()-started
        scene['history'].append({'operation': config['operation'], 'object': semantic_id,
                                 'commands': 'commands.json', 'changed_mask': 'changed-mask.png'})
        scene['output'] = {'svg': 'edited.svg', 'png': 'edited.png', 'metrics': 'metrics.json'}
        save(out / 'scene.json', scene)
        save(out / 'metrics.json', data)
        preview_title = config['title']
        if provider:
            preview_title += ' / 真实生成' if args.provider_patch else ' / MOCK（非模型）'
        preview(out, preview_title, original, baseline, actual, mask, editable, config['zoom_bbox'])
        save(out / 'run-status.json', {'status': 'completed', 'invariant_pass': data['invariant_pass']})
        print(json.dumps({'output': str(out), 'invariant_pass': data['invariant_pass'],
                          'untouched': untouched, 'edit_mask_area_percent': data['edit_mask_area_percent']}, ensure_ascii=False))
        return 0 if data['invariant_pass'] else 3
    except Exception as exc:
        (out / 'failure.txt').write_text(traceback.format_exc(), encoding='utf-8')
        save(out / 'run-status.json', {'status': 'failed', 'error': str(exc), 'automatic_retry': False})
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--baseline-png', type=Path, required=True)
    p.add_argument('--baseline-svg', type=Path, required=True)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--provider-patch', type=Path)
    p.add_argument('--provider-record', type=Path)
    p.add_argument('--allow-patch-resize', action='store_true')
    p.add_argument('--inkscape', default=r'C:\Program Files\Inkscape\bin\inkscape.com')
    sys.exit(run_probe(p.parse_args()))


if __name__ == '__main__':
    main()
