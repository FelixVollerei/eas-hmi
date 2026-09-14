# Changelog

## EAS Visual 0.1.0 Experimental — 2026-09-14

- Added independent `eas_visual` Python package using the existing EAS SVG importer/renderer.
- Added immutable directory projects with verified content-addressed assets and atomic revision navigation.
- Added explicit promotion, integer move, strict local patch composite, donor/none/generative repair.
- Added mock provider and model-neutral external patch exchange with honest provider provenance.
- Added untouched-region validation, exact object-copy measurement, undo/redo/reset/checkout/replay.
- Added `eas-visual` JSON-first CLI and optional `eas-visual-mcp` with six high-level tools.
- Added three reproducible demos, a small saved project, release tests and experimental documentation.
- Python distribution is now `eas-visual` 0.1.0. Both `eas_visual` and `eas_hmi` ship together;
  the `eas-hmi` command remains available. Do not install both distribution names in one environment.
- EAS HMI Core source behavior is unchanged. Its older Windows native-crash investigation remains unresolved;
  this release does not claim to fix it. See archived [HMI README](docs/README-EAS-HMI.md).

Historical experiment reports remain evidence of their own stages, not release specifications.
