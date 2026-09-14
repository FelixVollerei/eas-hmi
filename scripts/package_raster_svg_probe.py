"""Package this dated experiment, including honest limits and phone previews."""
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from probe_raster_svg import inspect_svg, metrics

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'build/raster-svg'
OUT = ROOT / 'deliverables/editable-agentic-scene-probe-20260914'
INPUTS = BASE / '20260914-web-inputs'
SAMPLES = [
    ('floorplan-yunxi', '楼盘户型 A', 'yunxi-smooth', (220, 170, 448, 310)),
    ('floorplan-phoenix', '楼盘户型 B', 'floorplan-phoenix-smooth', (190, 290, 420, 430)),
    ('anime-wikipe', '少女插画 A', 'wikipe-smooth', (300, 315, 630, 600)),
    ('anime-sailor', '少女插画 B', 'anime-sailor-smooth', (380, 365, 710, 650)),
]
FONT = r'C:\Windows\Fonts\msyh.ttc'


def text(draw, xy, value, size=25, color='#16243a'):
    draw.text(xy, value, font=ImageFont.truetype(FONT, size), fill=color)


def compare(title, frames, path, cell_w=620, cell_h=800, nearest=False):
    canvas = Image.new('RGB', (cell_w * 2 + 60, (cell_h + 60) * 2 + 160), '#edf2f8')
    draw = ImageDraw.Draw(canvas)
    text(draw, (25, 20), title, 32)
    text(draw, (25, 66), '64 色；处理保留原始分辨率。此处仅为手机预览缩放。', 22)
    for i, (label, frame) in enumerate(frames):
        x, y = 20 + (i % 2) * (cell_w + 20), 110 + (i // 2) * (cell_h + 60)
        text(draw, (x, y), label)
        pic = frame.copy()
        pic.thumbnail((cell_w, cell_h), Image.Resampling.NEAREST if nearest else Image.Resampling.LANCZOS)
        if nearest:
            factor = min(cell_w / frame.width, cell_h / frame.height)
            pic = frame.resize((int(frame.width * factor), int(frame.height * factor)), Image.Resampling.NEAREST)
        draw.rectangle((x, y + 38, x + cell_w, y + 38 + cell_h), fill='white')
        canvas.paste(pic, (x + (cell_w - pic.width) // 2, y + 38 + (cell_h - pic.height) // 2))
    canvas.save(path)


def foreground_metrics(reference, actual):
    a, b = np.asarray(reference, dtype=np.int16), np.asarray(actual, dtype=np.int16)
    # Fixed mask from original: >12 channel distance from median border color.
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    background = np.median(border, axis=0)
    mask = np.abs(a - background).max(axis=2) > 12
    error = np.abs(a - b)[mask]
    return {'mask_rule': 'original max-channel distance >12 from median border RGB',
            'background_rgb': background.tolist(), 'pixel_count': int(mask.sum()),
            'rgb_mae_0_255': float(error.mean()),
            'pixels_max_channel_error_le_8_percent': float((error.max(axis=1) <= 8).mean() * 100)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for sub in ('png', 'svg', 'previews', 'sources', 'measurements', 'reproduce'):
        (OUT / sub).mkdir(exist_ok=True)
    sources = json.loads((INPUTS / 'sources.json').read_text(encoding='utf-8'))
    selected_sources = [r for r in sources if r['id'] in {s[0] for s in SAMPLES}]
    (OUT / 'sources/sources.json').write_text(json.dumps(selected_sources, ensure_ascii=False, indent=2), encoding='utf-8')
    rows, phone_lines = [], []
    for sid, title, smooth_name, crop in SAMPLES:
        original = Image.open(INPUTS / f'{sid}.png').convert('RGB')
        exact_dir = BASE / f'20260914-{sid}-exact'
        smooth_dir = BASE / f'20260914-{smooth_name}'
        quant = Image.open(exact_dir / 'quantized-64.png').convert('RGB')
        exact = Image.open(exact_dir / 'c64-exact-runs/eas.png').convert('RGB')
        smooth = Image.open(smooth_dir / 'c64-spline/eas.png').convert('RGB')
        assert np.array_equal(np.asarray(quant), np.asarray(exact))
        for label, source in [('pixel-faithful', exact_dir / 'c64-exact-runs'), ('smooth', smooth_dir / 'c64-spline')]:
            shutil.copy2(source / 'eas.png', OUT / f'png/{sid}-{label}.png')
            shutil.copy2(source / 'eas.svg', OUT / f'svg/{sid}-{label}.svg')
        original.save(OUT / f'sources/{sid}-original.png')
        quant.save(OUT / f'sources/{sid}-quantized-64.png')
        src = next(r for r in selected_sources if r['id'] == sid)
        download = next(INPUTS.glob(f'{sid}-download.*'))
        shutil.copy2(download, OUT / 'sources' / download.name)
        frames = [('① 原始位图', original), ('② 减至 64 色', quant),
                  ('③ EAS 像素保真版（矢量小区域）', exact), ('④ EAS 平滑描摹版', smooth)]
        compare(title + ' / 全图对照', frames, OUT / f'previews/{sid}-comparison.png')
        compare(title + ' / 局部放大（最近邻，不修图）', [(label, pic.crop(crop)) for label, pic in frames],
                OUT / f'previews/{sid}-detail.png', cell_h=420, nearest=True)
        er = json.loads((exact_dir / 'report.json').read_text(encoding='utf-8'))
        sr = json.loads((smooth_dir / 'report.json').read_text(encoding='utf-8'))
        ev = next(v for v in er['variants'] if v['name'] == 'c64-exact-runs')
        sv = next(v for v in sr['variants'] if v['name'] == 'c64-spline')
        def measured_seconds(report, row):
            return row['tracing_seconds'] + sum(c['seconds'] for c in report['commands'] if c['name'].startswith(row['name'] + '-'))
        rows.append(dict(id=sid, title=title, size=original.size, crop=crop, source=src,
                         exact=ev, smooth=sv, exact_stats=inspect_svg(OUT / f'svg/{sid}-pixel-faithful.svg'),
                         exact_measured_seconds=measured_seconds(er, ev), smooth_measured_seconds=measured_seconds(sr, sv),
                         foreground_exact_vs_original=foreground_metrics(original, exact),
                         foreground_smooth_vs_original=foreground_metrics(original, smooth),
                         detail_exact_vs_original=metrics(original.crop(crop), exact.crop(crop)),
                         detail_smooth_vs_original=metrics(original.crop(crop), smooth.crop(crop))))
    reports = sorted(BASE.glob('20260914-*/report.json'))
    all_reports = [json.loads(p.read_text(encoding='utf-8')) for p in reports]
    for p in reports:
        shutil.copy2(p, OUT / 'measurements' / (p.parent.name + '.json'))
    for script in ('probe_raster_svg.py', 'package_raster_svg_probe.py'):
        shutil.copy2(ROOT / 'scripts' / script, OUT / 'reproduce' / script)
    summary = dict(samples=rows, variants=sum(len(r['variants']) for r in all_reports),
                   commands=sum(len(r['commands']) for r in all_reports),
                   failed_commands=[c for r in all_reports for c in r['commands'] if c['exit_code']],
                   exact_eas_vs_direct_count=sum(v['eas_identical_to_direct_trace'] for r in all_reports for v in r['variants']))
    (OUT / 'measurements/summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Editable Agentic Scene：位图 → EAS → SVG 还原验证', '',
        '日期：2026-09-14。范围：视觉还原实验；未实现插件、语义分组或编辑。', '',
        '**结论：原始分辨率下，减色位图可以经 EAS 以纯矢量几何零像素误差还原。四张外部样本的 32 色和 64 色版本均验证一致。但本次精简平滑曲线方案未通过暂定保真门槛，不能宣称已得到适合语义编辑的 SVG。**', '',
        '## 怎么查看', '',
        '- `png/*-pixel-faithful.png`：实际 EAS SVG 经 Inkscape 渲染的原尺寸 PNG。',
        '- `png/*-smooth.png`：同一输入的平滑描摹对照。',
        '- `previews/*-comparison.png`：原图、64 色输入、像素保真版、平滑版四格对照。',
        '- `previews/*-detail.png`：固定局部坐标，最近邻放大，检查文字、五官、发丝。',
        '- `svg/`：对应纯矢量 SVG；`sources/`：原始下载、白底归一化输入、来源；`measurements/`：全部 30 个变体结果。', '',
        '## 64 色像素保真版', '',
        'MAE 为 RGB 平均绝对误差（0–255，越低越好）。原图误差只来自减色；还原误差是相对减色输入。MB 按 1,000,000 字节。', '',
        '|样本|原始尺寸|对原图 MAE|还原 MAE|SVG MB|矢量矩形子路径数|已记录处理秒数|',
        '|---|---|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"|{r['title']}|{r['size'][0]}×{r['size'][1]}|{r['exact']['eas_vs_original']['rgb_mae_0_255']:.4f}|0.0000|{r['exact_stats']['bytes']/1e6:.3f}|{r['exact_stats']['subpaths']:,}|{r['exact_measured_seconds']:.2f}|")
    lines += ['', '处理秒数 = 矢量化 + CLI 导入/导出/严格验证 + 两次 PNG 渲染，不包含下载、减色、指标计算、人工查看或 Agent 推理。本次有并行实验，不能据此作正式性能基准。Token 和 Agent 步数没有可靠仪表记录，未估算。', '',
        '## 平滑描摹版与局部误差', '',
        '使用 VTracer 0.6.15，spline、stacked、speckle=4、color_precision=8、layer_difference=16、path_precision=3；另保留 polygon、少量更保守参数和无平滑轮廓实验，未挑掉失败结果。', '',
        '|样本|对减色输入 MAE|误差≤8 像素占比|边缘 F1（容差 1 px）|主体对原图 MAE|细节对原图 MAE|',
        '|---|---:|---:|---:|---:|---:|']
    for r in rows:
        m=r['smooth']['eas_vs_quantized']
        lines.append(f"|{r['title']}|{m['rgb_mae_0_255']:.3f}|{m['pixels_max_channel_error_le_8_percent']:.2f}%|{m['edge_f1_1px']:.4f}|{r['foreground_smooth_vs_original']['rgb_mae_0_255']:.3f}|{r['detail_smooth_vs_original']['rgb_mae_0_255']:.3f}|")
    lines += ['',
        '主体掩膜统一取“原图相对边框中位背景色的最大通道差 >12”，用于减少空白背景稀释误差，不是语义分割。具体裁剪坐标、掩膜像素数和像素版主体误差均见 summary.json。边缘取相邻像素最大通道差 >24；F1 采用 1 像素容差，并非 OCR 或人脸识别分数。', '',
        '暂定门槛在看外部样本结果前设为：对减色输入 MAE≤2、误差≤8 像素≥97%、边缘 F1≥0.97、path≤20,000、SVG≤5MB；平滑版未全部满足。它是实验筛选条件，未经用户确认，不等于审美或生产验收标准。', '',
        '## 像素保真版究竟证明了什么', '',
        '1. 只读取位图，先 MEDIANCUT 减色，无抖动、不缩小分辨率。插画原 PNG 的透明区域合成到白底；保留下载原件。',
        '2. 将同色横向像素段纵向合并为矩形，以闭合矢量子路径表达；相同颜色汇总到 path 中，设置 crispEdges。没有 image、foreignObject、base64 位图或外部图像引用。',
        '3. 将整张矢量图作为一组，经真实 EAS CLI import-svg、render、validate --strict，再用 Inkscape 1.4.2 按原尺寸渲染。',
        '4. 这等价于保存像素网格的矢量几何，不是优雅的贝塞尔曲线重建。64 个颜色 path 背后仍有数万个矩形，不能把 path 数少当作结构简单；放大后仍呈像素阶梯。',
        '5. 当前 EAS 接收为一个 opaque_svg 节点；内部均为矢量，但还没有家具、墙体或脸部语义。分组绕开了现有 importer 对大量顶层对象逐个复制 SVG 的成本。',
        '6. Agent 编写、选择、调用并检查算法；没有让视觉模型逐笔重画，没有读取样本对应的原始 SVG，没有调用生成模型补画或修图。', '',
        '因此，“经 EAS 保存/渲染高保真几何”已获得正向证据；“精简平滑且便于语义微调的 SVG”仍待验证。建议保留精确保真底稿，下一轮仅在选定局部建立语义组和可编辑表示，并量化局部编辑对未编辑区域的影响；不据此跳到完整插件开发。', '',
        '## 完整性与局限', '',
        f"本轮共 {summary['variants']} 个变体，{summary['commands']} 次已记录子进程命令，非零退出 {len(summary['failed_commands'])} 次。{summary['exact_eas_vs_direct_count']} 个变体经 EAS 的渲染与各自直接 SVG 渲染完全一致。已知历史原生崩溃风险没有因此得到修复或排除。没有重跑整个 EAS 测试套件。", '',
        '仅验证本机 Inkscape、原始尺寸、白底输入。尚未验证其它渲染器、任意缩放、复杂照片、透明编辑、OCR 文字编辑或跨图泛化。4 个外部样本很小，两个插画同作者同角色，不代表所有二次元画风。额外 HMI 输入是先前工程的 PNG，只用于管线标定；它没有替代外部样本。', '',
        '最初下载的 Blue Archive Arona 图片实际为黑底，未擅自抠图改成白底；不纳入这次四张验收样本，也不放入交付包。两张白底插画使用下列 Kasuga 原始 PNG，即使来源页面另有 SVG，本实验也未读取它。', '',
        '## 来源与归属', '']
    for r in selected_sources:
        lines += [f"- [{r['id']}]({r['page']})：{r['credit']}。原始下载 SHA-256：`{r['sha256']}`。"]
    lines += ['', 'Kasuga 插画选用 CC BY-SA 3.0，相关白底化、减色、矢量化及对照衍生图沿用该许可，修改如上所述。楼盘资料仅用于此次用户请求的技术比较，无额外公开发布许可声明；未上传到公开 GitHub。', '',
        '## 复现', '',
        '在 EAS 仓库及其已安装的 Python 环境中运行，依赖版本见 measurements/*.json：', '', '```powershell',
        r'.\.venv\Scripts\python.exe scripts/probe_raster_svg.py --input INPUT.png --output NEW_OUTPUT_DIRECTORY --colors 32 64 --modes exact-runs',
        r'.\.venv\Scripts\python.exe scripts/probe_raster_svg.py --input INPUT.png --output ANOTHER_NEW_DIRECTORY --colors 64 --modes polygon spline', '```', '',
        '交付包含新增实验脚本及结果；不包含 venv、缓存、历史工程副本或整个项目仓库。核心 EAS 源码未修改。', '',
        '## 发送状态', '', '按用户最新指令改为在本聊天展示及提供文件。QQ 空白、微信停在登录入口的发送尝试均未发送任何消息或文件；已取消 QQ/微信投递。']
    (OUT / 'REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (OUT / 'delivery-status.txt').write_text('PNG/SVG/comparisons ready for this chat. QQ/WeChat delivery cancelled by user; no messages or files sent to either app.\n', encoding='utf-8')
    (OUT / '手机速读.txt').write_text(
        '四张样本：两张彩色楼盘户型图、两张白底少女插画。\n'
        'pixel-faithful.png：由纯矢量 SVG 经 EAS 导出后再渲染；原尺寸下与 64 色输入逐像素一致。\n'
        'smooth.png：平滑描摹对照，有细线、五官和颜色偏差，未通过暂定高保真门槛。\n'
        'comparison.png：四格全图；detail.png：局部最近邻放大。\n'
        '像素保真版是大量矢量小区域，并未识别家具/眼睛，也不意味着已经解决语义编辑。\n'
        '来源：Kasuga/Wikimedia Commons，插画及其衍生图 CC BY-SA 3.0；楼盘图为安居客、房天下技术比较样本。详见 REPORT.md。\n', encoding='utf-8')
    manifest = [{'path': str(p.relative_to(OUT)).replace('\\', '/'), 'bytes': p.stat().st_size,
                 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in sorted(OUT.rglob('*')) if p.is_file() and p.name != 'manifest.json']
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    archive = shutil.make_archive(str(OUT), 'zip', OUT)
    print(json.dumps({'output': str(OUT), 'archive': archive, 'variants': summary['variants'],
                      'commands': summary['commands'], 'files': len(manifest)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
