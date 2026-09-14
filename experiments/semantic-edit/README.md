> Historical experiment. Its local build/deliverables assets are not bundled in Git. For the installable release and public reproducible demos, see [EAS Visual](../../README.md).

# Lazy Semantic Edit POC

This is a bounded experiment, not a plugin or a universal semantic SVG format.
It keeps the previous 64-color EAS baseline unchanged and creates local vector
objects only when requested. `scene.json` is an external minimal Scene IR;
existing EAS `opaque_svg` nodes carry exact local geometry and normal EAS
`move`/`set` commands implement the structural operations. Core EAS is unchanged.

## Run against the existing baseline delivery

From the repository root in the existing Python environment:

```powershell
.\.venv\Scripts\python.exe scripts/probe_semantic_edit.py --input deliverables/editable-agentic-scene-probe-20260914/sources/floorplan-yunxi-original.png --baseline-png deliverables/editable-agentic-scene-probe-20260914/png/floorplan-yunxi-pixel-faithful.png --baseline-svg deliverables/editable-agentic-scene-probe-20260914/svg/floorplan-yunxi-pixel-faithful.svg --config experiments/semantic-edit/configs/bed-move.json --output build/semantic-edit/NEW-MOVE

.\.venv\Scripts\python.exe scripts/probe_semantic_edit.py --input deliverables/editable-agentic-scene-probe-20260914/sources/anime-wikipe-original.png --baseline-png deliverables/editable-agentic-scene-probe-20260914/png/anime-wikipe-pixel-faithful.png --baseline-svg deliverables/editable-agentic-scene-probe-20260914/svg/anime-wikipe-pixel-faithful.svg --config experiments/semantic-edit/configs/mouth-smile.json --output build/semantic-edit/NEW-MOCK
```

External generation: first prepare only the context crop and a provenance JSON
containing its SHA-256, actual prompt, provider, and call count. Supply
`--provider-patch returned.png --provider-record record.json`. A wrong-sized
return is rejected unless `--allow-patch-resize` is explicitly passed; resizing
is then logged and limited to the patch. Without a provider, the script runs an
explicit deterministic mock and does not claim generative quality. The included
real provider record describes one built-in image generation tool call; the
tool did not expose an exact model identifier or seed.

The rectangle/polygon, displacement, repair donor, and blending margin live in
versioned JSON configs. Masks are fixed before edits; they are never inferred
from the measured output difference. This probe rejects >10% edit area, empty
masks, out-of-frame polygon coordinates, and target moves that would clip.
The 10% limit is an experiment safeguard, not an EAS product specification.

## Replay and test

```powershell
.\.venv\Scripts\python.exe experiments/semantic-edit/replay.py --cases build/semantic-edit --output build/semantic-edit/NEW-REPLAY
.\.venv\Scripts\python.exe -m pytest experiments/semantic-edit/test_contract.py -q
```

Replay uses saved model pixels and never calls a model. This verifies reproducible
compositing and EAS execution, not deterministic image generation.

Inspect `metrics.json` and `detail.png` together. A zero untouched MAE is not a
segmentation, background repair, or instruction-following quality score.
The actual hidden background is unknown and no repair ground-truth metric is
fabricated. `negative-control.json` demonstrates detection of a one-channel,
one-bit change outside the edit mask. `failure.txt` and `run-status.json` retain
execution failures without automatic retries.

PNG patch analysis and expected composites use Pillow/NumPy. Only cropped
masked pixels are converted to vector paths; the full edited image is never
retraced. The emitted EAS SVG is rasterized at the baseline dimensions using
Inkscape, and the resulting file is the source of acceptance metrics.

See `SEMANTIC-EDIT-POC-REPORT.md` for measured results and limitations.
