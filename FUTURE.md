# Outside v0.1

- Optional automatic localization proposals with confidence, preview and explicit acceptance.
- Better local inpainting and seam metrics; automatic donor proposals.
- Swap, scale, rotate; explicit policies for overlap, clipping and object ownership.
- History indexing, deduplication/compaction, orphan collection and storage quotas.
- Streaming/tiled processing for large images and more renderers.
- One optional direct image-model transport adapter; never a required model SDK.
- Thin host recipes for Codex, DeepSeek Harness and other MCP clients; no Core dependency on them.
- Optional plugin packaging after actual host feedback. No edits to dsh-std or 墨小汐 in this release.
- GUI, cloud, accounts, commercial services and ontology work are separate projects, not release gates.

Do not add whole-image regeneration as a fallback. Do not silently enlarge support to make a failed edit pass.
