# EAFS v0.2

EAFS (Editable Agent File Standard) is an experimental structured file editing project.
The runnable plan protocol remains `sfe/0.1`; neither name is an externally adopted standard.
This EAFS-focused product tree exports the current implementation from source commit
`76b9818308b1f606e86e796a2481ba229fade730` in this repository. Earlier EAS Visual commits remain in the existing Git history. It retains
the EAS-HMI engineering profile and its required implementation; EAS Visual is a separate project.

## Use

Python 3.12 or newer is required. Install the package and optional MCP adapter:

```sh
python -m pip install -e '.[dev,mcp]'
python -m sfe_files.cli contract
python -m sfe_files.cli status examples/sfe/settings.json
python -m sfe_files.cli preview examples/sfe/settings.json examples/sfe/settings.plan.json
python -m sfe_files.cli apply examples/sfe/settings.json examples/sfe/settings.plan.json --output build/settings-edited.json
eafs-mcp
```

`status` and `inspect` read structure and object addresses. A plan binds the original
source SHA-256; `preview` validates every step, and `apply` publishes a new output.
The original input and an existing output are preserved. MCP uses local stdin/stdout
and restricts paths to `EAFS_MCP_ROOTS` (the current directory by default).

The v0.2 candidate supports 29 ordinary file profiles plus EAS-HMI engineering projects.
The current source also includes an experimental Minecraft Java 26.3 world-directory
and semantic structure workflow. It is separate from the original 30-profile candidate
verification. Office layout, formula recalculation, CAD rendering and KiCad ERC remain
outside the file structure checks; each profile exposes its specific limitations.

Read the [introduction](docs/EAFS-INTRODUCTION.md), [MCP guide](docs/EAFS-MCP.md),
[sample plans](examples/sfe/README.md) and [Minecraft experiment](docs/EAFS-MINECRAFT-JAVA-26-3.md).
The package distribution is `eafs` version `0.2.0`; existing Python modules and plan
identifiers remain compatible. The `eas-hmi` command serves the engineering profile.

## Verify and provenance

```sh
python -m pytest -q tests
python scripts/demo_sfe.py --output build/eafs-demo
```

See [current export verification](VALIDATION.md) and [file provenance](SOURCE_MANIFEST.json).
Older donor reports describe their own artifacts and do not certify this standalone export.
Remote CI and publication must be checked after an actual run; a workflow file is not a passing run.

Source code and original synthetic examples retain the [MIT license](LICENSE),
including its original copyright notice. No EAS Visual artwork, generated worlds,
user data, private configuration, credentials, environments or old Git history is included.
