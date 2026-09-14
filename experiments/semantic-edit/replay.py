"""Replay saved patches (never calls an image model), and verify output pixels.

Run with the EAS-installed Python:
  python experiments/semantic-edit/replay.py --cases build/semantic-edit --output NEW_DIR
In the delivery ZIP, use --cases cases --script code/probe_semantic_edit.py.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--script', type=Path, default=Path(__file__).resolve().parents[2] / 'scripts/probe_semantic_edit.py')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    names = ['case-a-bed-move-v1', 'case-b-mouth-mock-v1', 'case-b-mouth-generated-v1']
    for name in names:
        source = args.cases / name
        output = args.output / name
        command = [sys.executable, '-X', 'faulthandler', str(args.script),
                   '--input', str(source / 'input.png'), '--baseline-png', str(source / 'baseline.png'),
                   '--baseline-svg', str(source / 'baseline.svg'), '--config', str(source / 'config.json'),
                   '--output', str(output)]
        if 'generated' in name:
            command += ['--provider-patch', str(source / 'provider-raw.png'),
                        '--provider-record', str(source / 'provider-provenance.json'), '--allow-patch-resize']
        result = subprocess.run(command, check=False, capture_output=True)
        (args.output / (name + '.stdout')).write_bytes(result.stdout)
        (args.output / (name + '.stderr')).write_bytes(result.stderr)
        identical = result.returncode == 0 and np.array_equal(
            np.asarray(Image.open(source / 'edited.png')), np.asarray(Image.open(output / 'edited.png')))
        rows.append({'case': name, 'exit_code': result.returncode, 'identical_rgba_pixels': identical})
        (args.output / 'replay-report.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
        print(json.dumps(rows[-1]), flush=True)
        if not identical:
            raise RuntimeError('Replay failed; results retained, no automatic retry')


if __name__ == '__main__':
    main()
