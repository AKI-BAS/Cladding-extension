# CladdingTools (pyRevit extension)

Profiled cladding for Revit: corrugated, trapezoidal, standing seam, flat cassette
and your own drawn profiles, laid out as real panels on wall, roof, floor and
ceiling faces, with quantities.

## Install
**From Git (recommended):** clone this repo into pyRevit's extensions folder,
named with the `.extension` suffix:

```
git clone https://github.com/AKI-BAS/Cladding-extension.git "%APPDATA%\pyRevit\Extensions\CladdingTools.extension"
```
Update later with `git pull` in that folder, then Reload in pyRevit.

**Manually:** download the ZIP and put its contents in
`%APPDATA%\pyRevit\Extensions\CladdingTools.extension\` so that
`CladdingTools.tab` sits directly inside it.

Reload pyRevit. A **CladdingTools** tab appears.

`preview.html` is a standalone browser mock-up of the dialog; Revit doesn't use it.

## Buttons
- **Apply Cladding**: pick flat faces > choose profile and panel settings >
  live preview and quantities > Apply. Panels are DirectShapes (Generic Models).
- **Regenerate**: select any panel and rebuild the group after the host moved.
  Shift+Click to swap the profile first.
- **Save Profile**: draw ONE module of a profile with detail lines in a drafting
  view (left to right, connected), select them, click. It is saved to
  `library/profiles/` and shows up in Apply Cladding.

## Schedules
Each panel writes the Comments parameter. For proper schedules, add these
shared parameters to the Generic Models category (instance) once per project
or template. The tool fills them when present:

| Parameter | Type |
|---|---|
| CT_Profile | Text |
| CT_PanelLength | Length |
| CT_PanelWidth | Length |
| CT_CoverArea | Area |
| CT_DevelopedArea | Area (unfolded sheet) |
| CT_Weight | Number (kg) |

## Profile files
Either a `pattern` of segments or raw `points` (mm, x across, z depth):

```json
{"name": "Zigzag rhythm 3-1", "thickness": 0.7,
 "pattern": [{"type": "zig", "width": 40, "depth": 25},
             {"type": "flat", "width": 60},
             {"type": "zig", "width": 80, "depth": 40}]}
```
Segment types: `flat`, `zig`, `sine`, `trapezoid` (`top`, `bottom`), `seam` (`seam`).
Optional `density` in kg/m3 (default steel 7850).

## Known limits (v1)
- Flat faces only. Curved faces are skipped.
- Panels are laid out on the face's bounding rectangle; panels whose centre sits
  in an opening or outside the face are skipped, but edge panels are not trimmed
  to sloped edges or around openings.
- No end laps, flashings or corner trims.
- Not yet tested across Revit versions. Written for pyRevit's IronPython engine.
