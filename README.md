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
  live preview and quantities > Apply. Panels are Generic Models (DirectShapes).
- **Regenerate**: rebuilds the selected cladding, e.g. after the wall moved.
  Shift+Click to pick a new profile from a list. Usually not needed for
  parameter edits, see Live update.
- **Save Profile**: draw ONE module of a profile with detail lines in a drafting
  view (left to right, connected), select them, click. It is saved to
  `library/profiles/` and shows up in Apply Cladding.

## Live update
Change a CT_ value on any panel in Properties and click Apply: the whole
cladding rebuilds straight away, in the same undo step.

- **CT_PanelWidth**: type a width and the panels become that wide, snapped to
  whole profile modules (1200 mm of 76 mm corrugation becomes 1216 mm).
- **CT_PanelLength**: type a length and runs are split at that length or less.
- The other inputs (profile, joint, standoff, orientation, material) work the same.
- Calculated values (areas, weight, group) snap back if typed over.
- A value that can't be used (unknown profile, size too small) is put back and
  Revit shows a warning.

Live update is switched on by `startup.py` when pyRevit loads. Cladding made
with an older version needs one Regenerate before width/length edits work.

## Parameters
The first time you apply cladding in a project, the tool creates these shared
parameters on Generic Models (fixed GUIDs, so schedules and view filters carry
over between projects). Your own shared parameter file setting is left alone.

**Inputs** (edit in Properties; rebuilds live)

| Parameter | Type | Notes |
|---|---|---|
| CT_Profile | Text | Any profile name from the library |
| CT_CoverWidth | Length | Snaps to whole profile modules |
| CT_MaxLength | Length | Longer runs are split |
| CT_Joint | Length | Gap between panels |
| CT_Standoff | Length | Host face to back of sheet |
| CT_Orientation | Text | Vertical / Horizontal (Lóðrétt / Lárétt also work) |
| CT_Material | Text | Material name in the project, blank for none |

**Calculated** (for schedules; CT_PanelWidth and CT_PanelLength can also be typed over to resize)

| Parameter | Type |
|---|---|
| CT_Group | Text |
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
- Panels are DirectShapes: no grips or Edit Type. Change them through the CT_
  parameters and Regenerate.
- Written for Revit 2024 and later on pyRevit's IronPython engine.
