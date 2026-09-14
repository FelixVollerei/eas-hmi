# EAS Visual roadmap

## 0.1 Experimental — this release

Immutable pixel-faithful baseline; explicit bbox/polygon/mask promotion; integer move;
donor / explicit none / constrained external repair; mock and external model patch exchange;
strict EAS SVG render validation; undo/redo/reset/checkout/replay; JSON CLI and six MCP tools.

## Next small release

1. Improve mask proposal and review on real user-selected objects. Preserve explicit support approval.
2. Improve donor selection and local repair quality, with visible seams/quality warnings.
3. Measure larger histories and remove repeated integrity I/O without weakening corruption detection.
4. Add one independently tested direct provider adapter if file exchange proves inconvenient.
5. Expand renderer/OS validation beyond the pinned experimental environment.

Only then consider multi-object operations and continuous transforms. Every addition must retain
baseline integrity, exact untouched-region checks and revision replay. Model quality is evaluated
separately from containment. No promise of automatic Raster → semantically complete SVG.
