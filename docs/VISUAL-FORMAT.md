# EAS Visual project format v1

An `.eas` project is a directory. Assets have SHA-256 filenames, not mutable friendly aliases.

```text
scene.eas/
  manifest.json
  baseline/   original source bytes, original RGBA PNG, quantized PNG, EAS SVG
  objects/    extracted local RGBA pixels
  masks/      full-canvas binary masks; provider-local masks
  patches/    local vector overlays, context crops, requests, raw and normalized model outputs
  history/    immutable JSON revision records
  output/     verified PNG and SVG revision caches
```

`manifest.json` contains format/tool versions, dimensions, normalization, baseline hash,
baseline references, current revision, revision index, provider metadata, scene reference,
redo stack, prepared requests and navigation log. Its checksum covers canonical JSON excluding
the checksum itself. Every asset reference is `{ "path": "relative/path", "sha256": "..." }`.
Paths cannot escape the project or traverse symlinks. Hashes detect accidental corruption;
they are not cryptographic signatures against an adversary able to rewrite the entire project.

Each revision records ID, parent, UTC timestamp, operation, object, original mask, editable
support, source/output pixel hashes, provider/instruction, transform, patch references,
validation and warnings. Its scene state consists of objects plus ordered local SVG layers.
The residual is the immutable baseline under those overlays; there is no whole-scene retracing
after initialization. A promoted object has label, current bbox/mask, immutable extracted
source region, source hash, cumulative integer translation and localization metadata.

Coordinates are integer pixels. Bbox is `[x0,y0,x1,y1)` with exclusive right/bottom edges.
Mask PNGs must be full-size binary grayscale (0 or 255). Polygon edges are rasterized once.
Dimensions are limited to 4M pixels; each edit support is nonempty and at most 25% of the
canvas. A provider receives a context crop smaller than the canvas. Margins are explicit,
0–8 pixels and included in support before provider output is accepted. Other promoted
objects cannot overlap the selected support in v0.1. Residual content can still be occluded
by a move: semantic collision detection on unpromoted pixels is not provided.

Input is retained byte-for-byte. An RGBA PNG copy is also retained. The working baseline
uses explicit white alpha compositing and MEDIANCUT color reduction (64 by default, no resize).
"Pixel-faithful" means exact to this declared baseline, not lossless to the original full-color
image. Baseline geometry and every overlay are pure vector paths. Rendering at original
resolution through the checked EAS + Inkscape pipeline must equal the expected RGB composite.
Other zoom levels and renderers have no exactness guarantee.

File hashes use ordinary SHA-256. Pixel hashes use SHA-256 of
`f'{width}x{height}:RGB:'.encode() + image.convert('RGB').tobytes()`.
They have different purposes and are not interchangeable. Pixel metrics compare RGB channel
values; changed-pixel ratio counts pixels with any changed channel. Validation compares each
edit with its parent revision, excluding only the declared support. This preserves all earlier
edits outside the new support.

## Transactions and navigation

A file lock serializes each operation. Every referenced history/asset is hash-verified on open,
including inactive branches and redo. New assets are written exclusively and fsynced. Only after
the actual SVG raster has passed validation is the manifest atomically replaced. Failed work may
leave unreferenced files, never a committed half-edit. This is a local-filesystem process-failure
boundary, not a claim about sudden power loss or network filesystem durability.

Undo/redo/reset/checkout move the manifest pointer and append a navigation event. Old records
remain; a new edit after undo clears the redo stack but retains the old branch for checkout.
Promotion is also a revision; undo can undo promotion. Reset restores genesis pixels and removes
current promotions; older revisions remain check-outable. `render` exports the verified cached
PNG/SVG without overwriting any existing file. `replay` independently renders all saved states
through EAS, checks their hashes, makes no provider calls and leaves the manifest unchanged.

v0.1 intentionally verifies all referenced assets at each operation. I/O grows with retained
history. Each revision also retains output PNG/SVG caches; no compaction is implemented. This
is a small-scene experimental tool, not a resolution of the earlier EAS history-scale concern.
An incomplete init may leave a sibling `.name.init-*` directory for diagnosis.
