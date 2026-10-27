# S2b independent synthetic fixture preparation

These are **invented planning inputs**, not university data, verified gold, OCR outputs or recognition results. The generator reads no real screenshot, research dataset, labels or predictions. It uses only Pillow and the Python standard library.

`manifest.json` records each PNG's actual hash, dimensions and generator-declared source relationships. Coordinates describe the generated images, not recognition rules. Session tokens are literal printed tokens; no new application interpretation has been implemented.

| File | Intended later diagnostic |
|---|---|
| 01-two-row-colored.png | Pale grid, two clock rows, weekday/date labels, six invented blocks with varied spans |
| 02-scaled-translated.png | Changed resolution and origin; same source relationships |
| 03-colors-and-small-fonts.png | Different identities/names/dates/day assignments/times/venues, widths, colors and font size |
| 04-missing-grid-rules.png | Colored-region evidence without pale table borders |
| 05-unreadable-and-no-meridiem.png | Unreadable range, unknown venue and absent AM/PM |
| 06-ambiguous-block-boundary.png | Edges between header boundaries plus Time TBA; no invented interval |
| 07-transposed-unsupported.png | Day-column/time-row orientation outside the proposed first supported family |
| 08-unrelated-colored-notice.png | Colored notices and a code-like identifier do not establish a timetable |
| 09-adjacent-same-color.png | Adjacent same-color regions separated by a visible rule |
| 10-touching-ambiguous-regions.png | Touching same-color regions without an internal visible rule; no forced ownership |

The manifest's `time_field_state` describes a conservative prospective oracle. Missing meridiem may be resolved only if a future adapter demonstrates a unique source-backed axis authority and exact block ownership. These provisional planning expectations must be reviewed before becoming assertions; they do not prohibit a genuinely evidenced resolution. Likewise, the generator's hidden rectangle coordinates do not prove that an algorithm can observe a boundary in fixture 10.

Regenerate only these synthetic artifacts with:

` .\.venv\Scripts\python.exe -B tests/synthetic_samples/s2b_preparation/generate.py `

No S2b adapter, regression tests or browser workflow has been implemented in this preparation task. Source-based future assertions should check field values, raw evidence links and ambiguity—not just counts. No real timetable contents are copied into this directory.
