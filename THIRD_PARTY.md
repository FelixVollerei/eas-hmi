# Artwork attribution and licenses

The Python code and original synthetic room artwork use the root MIT license.
Third-party image assets and their derivatives do **not** inherit the MIT license.

## Wikipe-tan

- Artwork: **Wikipe-tan full length**, by **Kasuga**.
- Source: https://commons.wikimedia.org/wiki/File:Wikipe-tan_full_length.png
- License used here: **Creative Commons Attribution-ShareAlike 3.0 Unported**,
  https://creativecommons.org/licenses/by-sa/3.0/
  (legal text: https://creativecommons.org/licenses/by-sa/3.0/legalcode).
- Files: `examples/visual/assets/wikipe-tan-input.png`, `mouth-provider-input.png`,
  `mouth-provider-output.png`, and all corresponding generated character projects,
  SVGs, patches, masks and comparison images, including `docs/visual/generative-*.png`.
- Changes: white-background normalization, 64-color reduction, crop, vector reconstruction,
  locally generated smile, masked composition and annotated comparison layouts.
- These artwork adaptations are distributed under **CC BY-SA 3.0**. Attribution does not imply endorsement.
- The original author did not provide the model edit; `mouth-provider-metadata.json` records
  the actual prior crop-edit call. Unknown model identity and seed have not been invented.

The raw provider result includes changes to surrounding context and a size mismatch. Its inclusion
is intentional: the demo demonstrates rejecting those context changes during composition.

Earlier privately downloaded real-estate floorplan images are not included in the public demo assets.
Runtime libraries and Inkscape retain their own licenses and are installed separately.
