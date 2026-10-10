> Historical donor candidate report. These older wheel/test counts are not verification of this standalone export. See [current export verification](../VALIDATION.md).

# EAFS v0.2 release candidate

Status: **local candidate for user review; not published**. The donor distribution was `eas-visual` version `0.2.0`. This standalone export uses `eafs` version `0.2.0`. Existing edit plans retain the `sfe/0.1` protocol identifier and behavior. EAFS is an experimental project name, not an externally adopted standard.

## Included scope

- Thirty discoverable edit profiles: EAS-HMI engineering projects and 29 ordinary file formats. Each supports only the explicit subset returned by its contract; the [introduction](EAFS-INTRODUCTION.md) and [sample index](../examples/sfe/README.md) describe addresses and limitations.
- One `sfe-files` CLI, one optional `eafs-mcp` stdio adapter, fixed-base sample plans and a reproducible 30-profile demo.
- New ordinary-file outputs use exclusive publication after candidate validation. An existing output is preserved even if another process creates it during `apply`.
- Shared plan validation and JSON-pointer helpers are separated from the format registry. Profiles depend on those helpers without importing the registry that loads them.
- Exact sample bytes, including CRLF where present, are preserved in Git so published plan hashes match a clean checkout.

The earlier local implementation was captured separately in baseline commit `fa19a81`; its origin is recorded in `task EAFS-002` (donor historical record; excluded from this export). This candidate adds the release corrections and verification. The Visual 0.1 feature scope remains documented in the historical `Visual release report` (separate donor component).

## Verification

Commands, results, environment, and remaining unverified checks are recorded in `EAFS-002 verification` (donor historical record; excluded from this export). CI definitions for EAFS and Visual are in `.github/workflows/`. A workflow file is a proposed gate; a remote run must be inspected separately before claiming remote CI success.

The implementation commit `57e4033` was checked out independently. All 29 fixed plans matched the new checkout's source bytes. A freshly installed wheel passed **756 tests** with **88.08%** aggregate coverage against an 85% floor, and its 30-profile demo completed **210 CLI calls and 199/199 checks**. `pip check` found no broken requirements. The wheel's SHA-256 is `38de34528e764b5ecfdcec746f8df102b036a3f301ee850f443bfc7d66aca019`; a local review copy is at `deliverables/eafs-v0.2/eas_visual-0.2.0-py3-none-any.whl` (ignored by Git). Complete per-call demo records and test output are in the project evidence directory linked above.

### Quality correction candidate

`QUALITY-001` (donor historical record; excluded from this export) applies the code-quality corrections to this candidate. Its code result is `5f77e3a` and its rebuilt wheel is at `deliverables/eafs-v0.2-quality/eas_visual-0.2.0-py3-none-any.whl` (ignored by Git), SHA-256 `3fab0235ebcf41dc0dedb515f9b6c13b80d062e6135f853fdd3043ab6bde7726`. This wheel supersedes the earlier local review copy for code-quality review. The `quality verification` (donor historical record; excluded from this export) and `structural review packet` (donor historical record; excluded from this export) record behavior, remaining static-analysis debt and independent-review status. The earlier candidate evidence remains historical evidence for `57e4033`.

## Boundaries and release decision

- The local stdio MCP test does not certify every host integration.
- Ordinary-file publication uses a temporary file and a same-directory hard link to claim a new output name exclusively. A filesystem that does not support this operation returns an I/O error and leaves the candidate unpublished.
- If publication succeeds but removal of its temporary hard link fails, `apply` reports `committed: true` with a `warnings` entry. The output remains the committed result.
- Office/ODF/EPUB semantic previews do not prove final layout, formula recalculation or reader rendering. Profile-specific limits remain in the [interface draft](STRUCTURED-FILE-EDITING.md).
- KiCad ERC and native schematic loading, CAD visual layout, and cross-host interoperability remain unverified. The [batch report](EAFS-BATCHES-3-5.md) states their current scope.
- Independent structural review is **PENDING USER-SUPPLIED REVIEW**. No tag, remote push, GitHub Release or package-index upload is included in this candidate.
