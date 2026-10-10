# Standalone EAFS export verification

The fixed source is `76b9818308b1f606e86e796a2481ba229fade730`.
The independent distribution is `eafs 0.2.0`; the existing `sfe/0.1` protocol is unchanged.
This export retains 29 ordinary file profiles, the EAS-HMI engineering profile, and
the current experimental Minecraft source. All production Python module bytes match
the donor commit. Package metadata, CI, documentation and one test fixture are adapted.

Actual local verification on Windows x64 with Python 3.12.7:

- The wheel was built offline with existing setuptools 80.9.0, without downloading dependencies.
- The wheel was installed with `--no-index --no-deps --target` into a dedicated build directory.
- `python -I` assertions verified both `sfe_files` and `eas_hmi` came from that installed wheel.
- The installed wheel passed **508 tests**, with **0 failures, 0 errors and 0 skips**, in 16.98 seconds.
- The existing fixed thirty-profile demo used that wheel: **210 CLI calls; 199/199 checks passed**.
- The CI's focused `ruff check` passed. Every production Python module in the wheel
  was compared byte for byte with this export. No `eas_visual` package or CLI is included.

The initial source-tree run passed 507 tests and failed one Minecraft MCP test because
its temporary world was outside the default authorized root. The fixture now explicitly
sets its own `EAFS_MCP_ROOTS` to its temporary directory. Product path checks and all
test assertions are unchanged. The final wheel test run passed all 508 tests.

Build commands (use Python environments with the declared dependencies already installed):

```sh
python -m pip wheel --no-index --no-deps --no-build-isolation --no-cache-dir --wheel-dir build/current-wheels .
python -m pip install --no-index --no-deps --target build/current-wheel-install build/current-wheels/eafs-0.2.0-py3-none-any.whl
```

For the wheel tests and demo, `PYTHONPATH` pointed to `build/current-wheel-install`.
The pytest invocation overrode its source-tree `pythonpath` with that same installed
directory. No real model, network service, user database, private recording or game
instance was used. The SQLite sample is the fixed synthetic example from the donor commit.

```sh
python -m pytest -q tests -o pythonpath=build/current-wheel-install
python scripts/demo_sfe.py --output build/current-wheel-demo
python -m ruff check --no-cache src/sfe_files src/eas_hmi/agent_protocol.py tests scripts/demo_sfe.py
```

Tested wheel: `build/current-wheels/eafs-0.2.0-py3-none-any.whl`, 183140 bytes.
SHA-256: `40844c7108b52461d697b8f0c1a87a4c25dc7ac95320c57b3399008114be0f66`.

The publication target is the existing public `FelixVollerei/eas-hmi` repository,
with an ordinary commit based on master `7a663d6054fccbdbf66f8b4c480f9a848a3b8a27`.
After the tests above, only repository URLs and README wording changed before the
offline metadata rebuild. The rebuilt wheel contains all 73 production
package files byte for byte identical to the tested wheel, with identical CLI entry
points. Its metadata now links to the existing repository. These checks were done
without rerunning the 508 tests or the demo; no production code changed. The later
THIRD_PARTY notice addition preserves the original attribution and explains that old
Visual art remains only in earlier Git history.

Release wheel: `build/existing-repo-wheels/eafs-0.2.0-py3-none-any.whl`, 183171 bytes.
SHA-256: `ff06be4d7451eb0a51743042bc0fc2b0386f12737042591aa7266d6ac2dd048f`.

Local detailed outputs are under ignored `build/`: `current-wheel-tests.xml`,
`current-wheel-demo/REPORT.md` and `current-wheel-demo/results.json`.
No GitHub repository was created or pushed in this preparation. Remote CI, publishing
and independent structural review have not been claimed. This file records this export,
not the historical donor wheel's larger test or coverage totals.

Remaining product limits include profile-specific edit subsets, Office layout and formula
recalculation, native CAD/KiCad validation, host interoperability and the explicitly
experimental Minecraft workflow. Read each runtime contract and the introduction.
