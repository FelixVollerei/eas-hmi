# Agent integration

The same Python API drives the CLI and optional stdio MCP server. Core has no dependency on
Codex, DeepSeek Harness, dsh-std or 墨小汐. No host-specific runtime was modified.

## CLI contract

All operation commands print one JSON object to stdout. `--json` explicitly selects the existing
default; there is no hidden human-readable mode. Help/version print ordinary text. Exit 0 means
success, 2 means rejected input/integrity/I/O operation, 1 means unexpected internal failure.

```json
{"ok": false, "version": "0.1.0", "error": {"code": "PATCH_SIZE", "message": "..."}}
```

Useful errors: `OBJECT_NOT_FOUND`, `INVALID_MASK`, `INVALID_SUPPORT`, `OBJECT_CONFLICT`,
`TARGET_OUTSIDE`, `REPAIR_REQUIRED`, `PATCH_SIZE`, `STALE_REQUEST`, `OUTSIDE_CHANGE`,
`COMPOSITE_MISMATCH`, `CORRUPT_ASSET`, `CORRUPT_MANIFEST`, `PROJECT_BUSY`.
On rejection inspect the error and supply corrected information. Never enlarge the mask merely
to suppress validation. A native process crash may yield no JSON; treat any nonzero exit as failure.

## Model-neutral patch exchange

1. Inspect current image and choose an explicit mask for the requested object. Promote it.
2. Run `prepare` with instruction, context bbox and optional blending margin. Use purpose `repair`
   for a source-background repair request. No visual revision changes at preparation.
3. Give the host's image editor **only `context_file`**, the local `mask_file` and instruction.
   The host is responsible for honoring this input contract. Core cannot police unrelated host calls.
4. Save the actual returned image and metadata. Unknown model ID/seed must be `null` or explicitly unknown.
5. Submit request ID, patch path and metadata using `edit --provider external-patch`, or
   `move --repair generative`. The stored request fixes object, source revision, purpose and support.
6. Wrong dimensions are rejected unless `--normalize` explicitly permits patch-only LANCZOS resizing.
   Raw and normalized output are retained. Context pollution is measured, then discarded outside support.
7. Inspect warnings and preview the seam. Undo on unsatisfactory quality.

`mock` paints a deterministic blue replacement and perturbs the crop context. It does **not**
understand natural language. `external-patch` supports real current/future models by file exchange;
it does **not** make a network model call. This is the initial provider adapter, not a built-in
subscription or inference service. Python contracts are in `eas_visual.providers`.

## MCP

Install `python -m pip install -e '.[mcp]'`. Start:

```text
eas-visual-mcp
```

Generic stdio host configuration (adapt field placement to your MCP client):

```json
{
  "command": "/absolute/path/to/venv/bin/python",
  "args": ["-m", "eas_visual.mcp_server"],
  "env": {
    "EAS_VISUAL_ROOTS": "/absolute/path/to/workspace",
    "EAS_VISUAL_INKSCAPE": "/usr/bin/inkscape"
  }
}
```

On Windows use the venv's `Scripts/python.exe` and installed `inkscape.com`. Roots are separated
by the OS path separator (`;` Windows, `:` Unix), defaulting to the server working directory.
Input, output, project and external patch files must lie under those roots. Project hash paths
have independent traversal checks. This root boundary is a convenience restriction, not an OS sandbox.

Six tools: `inspect_visual`, `create_visual_project`, `promote_object`, `edit_visual`,
`render_visual`, `undo_visual`. Inspect returns paths for clients with local file access;
the server does not embed images or host URLs. `edit_visual` accepts operation `move`, `prepare`,
`edit` or `replay` and Python API keyword options. `undo_visual` also supports redo/reset/checkout.
Results contain `ok`; check it even when the MCP protocol call itself succeeded.

The official Python MCP SDK v1 is pinned `<2`; see https://py.sdk.modelcontextprotocol.io/v1/.
Release tests initialize a real stdio client session, list the six tools and exercise path rejection.
Individual named Agent hosts are not claimed tested simply because standard MCP works.

## Minimal Python use

```python
from eas_visual import VisualProject

VisualProject.create('input.png', 'scene.eas')
p = VisualProject('scene.eas')
p.promote('bed', bbox=[48, 72, 128, 192])
p.move('bed', 164, 0, donor_bbox=[350, 160, 390, 200])
assert p.inspect()['validation']['untouched_changed_pixels'] == 0
p.navigate('undo')
p.navigate('redo')
p.replay()
```
