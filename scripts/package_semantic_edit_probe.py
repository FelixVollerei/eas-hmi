"""Assemble the bounded experiment and evidence without environments/caches."""
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'deliverables/semantic-edit-poc-20260914'
CASES = ['case-a-bed-move-v1', 'case-b-mouth-mock-v1', 'case-b-mouth-generated-v1']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    (OUT / 'cases').mkdir()
    evidence = OUT / 'evidence'
    evidence.mkdir()
    rows = []
    for name in CASES:
        source = ROOT / 'build/semantic-edit' / name
        metrics = json.loads((source / 'metrics.json').read_text(encoding='utf-8'))
        assert metrics['invariant_pass']
        tree = ET.parse(source / 'edited.svg')
        assert not any(e.tag.rsplit('}', 1)[-1] in ('image', 'foreignObject') for e in tree.iter())
        negative = json.loads((source / 'negative-control.json').read_text(encoding='utf-8'))
        assert negative['metrics']['changed_pixels'] == 1 and negative['metrics']['max_absolute_difference'] == 1
        shutil.copytree(source, OUT / 'cases' / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        rows.append({'case': name, 'untouched': metrics['untouched'],
                     'edit_mask_area_percent': metrics['edit_mask_area_percent'],
                     'pure_vector_svg': True, 'negative_control_detected': True})
    snapshot = OUT / 'engine-snapshot'
    shutil.copytree(ROOT / 'src', snapshot / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(ROOT / 'pyproject.toml', snapshot / 'pyproject.toml')
    (snapshot / 'scripts').mkdir()
    for name in ('probe_semantic_edit.py', 'package_semantic_edit_probe.py'):
        shutil.copy2(ROOT / 'scripts' / name, snapshot / 'scripts' / name)
    shutil.copytree(ROOT / 'experiments/semantic-edit', snapshot / 'experiments/semantic-edit',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache', '.ruff_cache'))
    shutil.copy2(ROOT / 'experiments/semantic-edit/SEMANTIC-EDIT-POC-REPORT.md', OUT / 'SEMANTIC-EDIT-POC-REPORT.md')
    shutil.copy2(ROOT / 'deliverables/editable-agentic-scene-probe-20260914/REPORT.md', OUT / 'PRIOR-RASTER-REPORT.md')
    shutil.copy2(ROOT / 'build/semantic-edit/replay-final/replay-report.json', evidence / 'replay-report.json')
    for name in CASES:
        replay = ROOT / 'build/semantic-edit/replay-final'
        shutil.copy2(replay / (name + '.stdout'), evidence / (name + '-replay.stdout'))
        shutil.copy2(replay / (name + '.stderr'), evidence / (name + '-replay.stderr'))
        shutil.copy2(replay / name / 'metrics.json', evidence / (name + '-replay-metrics.json'))
        shutil.copy2(replay / name / 'commands.json', evidence / (name + '-replay-commands.json'))
        shutil.copy2(replay / name / 'invocation.json', evidence / (name + '-replay-invocation.json'))
    versions = {d.metadata['Name']: d.version for d in importlib.metadata.distributions()}
    (evidence / 'environment-versions.json').write_text(json.dumps(versions, indent=2), encoding='utf-8')
    pins = ['-e ./engine-snapshot'] + [f'{n}=={versions[n]}' for n in sorted(versions)
                                      if n.lower() not in ('eas-hmi', 'pip', 'setuptools')]
    (OUT / 'requirements-probe.txt').write_text('\n'.join(pins) + '\n', encoding='utf-8')
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, check=True,
                              capture_output=True, text=True).stdout.strip()
    core_diff = subprocess.run(['git', 'diff', '--', 'src', 'pyproject.toml'], cwd=ROOT,
                               check=True, capture_output=True, text=True).stdout
    assert not core_diff
    audit = {'cases': rows, 'tracked_core_revision': revision, 'core_diff': core_diff,
             'tests': {'command': 'python -X faulthandler -m pytest experiments/semantic-edit/test_contract.py -q',
                       'observed_stdout': '9 passed in 0.40s', 'exit_code': 0,
                       'scope': 'targeted experiment tests only, not the full EAS suite'},
             'lint': {'command': 'python -m ruff check scripts/probe_semantic_edit.py experiments/semantic-edit/test_contract.py experiments/semantic-edit/replay.py',
                      'observed_stdout': 'All checks passed!', 'exit_code': 0},
             'notes': 'Test/lint results transcribed from actual tool execution; run commands again to independently verify.'}
    (evidence / 'audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'README.md').write_text('''# Semantic Edit POC 交付

先看 `SEMANTIC-EDIT-POC-REPORT.md` 和 `cases/*/detail.png`。
每个案例同时有全图 `comparison.png`、EAS `edited.svg` 和实际渲染的 `edited.png`。
`case-b-mouth-mock-v1` 是明确的确定性 mock，不能用它证明生成模型能力。
`case-b-mouth-generated-v1` 保留一次真实模型返回、输入裁剪和完整 provenance。

## 在新目录复现（不调用模型）

需要 Python 3.12 和 Inkscape 1.4.2。本机验证路径为
`C:\\Program Files\\Inkscape\\bin\\inkscape.com`；其它路径可给 probe 传 `--inkscape`。
从解压后的此目录运行：

```powershell
python -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -r requirements-probe.txt
.\\.venv\\Scripts\\python.exe engine-snapshot/experiments/semantic-edit/replay.py --cases cases --script engine-snapshot/scripts/probe_semantic_edit.py --output replay-new
.\\.venv\\Scripts\\python.exe -m pytest engine-snapshot/experiments/semantic-edit/test_contract.py -q
```

如果当前 EAS 环境已经安装依赖，可直接用现有解释器执行重放命令。
输出目录必须不存在，避免覆盖证据。重放使用保存的生成像素，不会重新调用模型；
重新采样的模型结果不保证可复现。原输入绝对路径作为历史 provenance 保留，
重放实际使用本包内的相对路径。

`engine-snapshot` 包含执行实验所需的 EAS Core、pyproject 与新增实验代码；
不包含整个仓库的历史文档/测试、git 历史、venv、缓存或构建运行副本。
`manifest.json` 为所有交付文件的 SHA-256。样本许可和来源见报告。
''', encoding='utf-8')
    files = [{'path': p.relative_to(OUT).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p)}
             for p in sorted(OUT.rglob('*')) if p.is_file()]
    (OUT / 'manifest.json').write_text(json.dumps(files, indent=2), encoding='utf-8')
    archive = shutil.make_archive(str(OUT), 'zip', OUT)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
    assert all(sha(OUT / row['path']) == row['sha256'] for row in files)
    print(json.dumps({'output': str(OUT), 'archive': archive, 'files': len(files),
                      'archive_bytes': Path(archive).stat().st_size, 'manifest_and_zip_verified': True}))


if __name__ == '__main__':
    main()
