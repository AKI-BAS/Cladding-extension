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
  live preview > Apply. Panels are cut around doors, windows and sloped edges.
- **Switch Cladding**: select a panel of existing cladding and click. The
  same window as Apply Cladding opens, filled in with that cladding's
  settings. Change the profile, material, widths, joint, standoff or
  orientation and click **Switch cladding**. Only what you changed is
  applied; panel sizes, cut-outs, corner laps and tags are kept. With a new
  profile, "Keep panel sizes exactly" keeps every panel where it is (ribs may
  be cut at panel edges); untick it to fit widths to whole ribs.
- **Add Flashings**: select doors/windows (or pick them after clicking), choose
  a flashing type and material. Adds a head, both jambs, and a sill on windows
  that aren't at floor level; doors never get a sill. Also "no sills" and
  "Remove flashings". See Flashings below.
- **Select Cladding**: select one panel, click, and every panel of that
  cladding is selected.
- **Regenerate**: applies every CT_ edit on the selected cladding(s) and
  flashing pieces, then rebuilds so the cladding follows its wall/roof
  (panels updated in place, tags stay). Works even when live updates are off,
  and tells you whether they're on. Shift+Click: reset all panel edits, or
  change the profile of the whole cladding.
- **Cladding Schedule** / **Flashing Schedule**: create (or open) a Revit
  schedule of all cladding panels / flashings. The cladding schedule lists
  panel types (P01, P02...: see Panel marks) with profile,
  material, thickness, width, length, count, flat area (wall covered), sheet
  area (unfolded, more than flat for corrugated) and weight, with totals.
  Identical pieces share a row; Shift+Click for one row per piece. They are
  normal schedules: they update by themselves and can go on sheets.
- **Save Profile**: draw ONE module of a profile with detail lines in a drafting
  view (left to right, connected), select them, click. It is saved to
  `library/profiles/` and shows up in Apply Cladding.

## Editing panels
Change a value in Properties and click Apply. With live updates on (started
by `startup.py` when Revit starts), the cladding updates straight away in the
same undo step. If they're off, click Regenerate after editing; Regenerate's
output says which it is and why.

**One panel** (Properties > Dimensions, material under Materials and Finishes)

| Parameter | What it does |
|---|---|
| CT_PanelWidth | Width of that panel's strip, snapped to whole profile modules. Later strips shift along. On the last strip it can reach past the wall edge. |
| CT_PanelLength | Length of that panel. Later panels in the strip re-split the rest. A length you set is kept even past the edge of the wall, so a panel can run up onto the wall above. |
| CT_Profile | Profile of that panel (any library profile name) |
| CT_Material | Material of that panel. A real Material parameter under **Materials and Finishes**: pick from the project's materials. |

**Types.** Panels get a type per profile and material, e.g.
`Cladding - Corrugated 18/76 - Stál`. Picking another type in the Type
Selector changes the panel's profile and material, like swapping a family
type. Right-click > Select All Instances selects every panel with that profile
and material.

**Tags stay.** Edits update the existing panels in place, so element ids and
tags survive. Only panels that disappear (e.g. now fully inside an opening)
are removed.
| CT_Standoff | Distance from the wall to the back of that panel |

Select several panels and set a value to change all of them. If you set the
same profile, material, standoff or width on **every** panel, it becomes the
cladding's default instead of separate edits.

**Whole cladding** (Properties > Data, edit on any panel)

| Parameter | What it does |
|---|---|
| CT_CoverWidth | Standard panel width (strips you resized keep their width) |
| CT_MaxLength | Longest panel before a run is split |
| CT_Joint | Gap between panels |
| CT_Orientation | Vertical / Horizontal (Lóðrétt / Lárétt also work). Resets panel edits. |

**Calculated** (for schedules; snap back if typed over): CT_Group, CT_PanelId
(face, strip, piece), CT_Thickness (real sheet thickness from the profile),
CT_CoverArea, CT_DevelopedArea, CT_Weight. Areas and
weight are reduced in proportion to what was cut away for openings.

A value that can't be used (unknown profile, size too small) is put back and
Revit shows a warning. The parameters are created on Generic Models the first
time you apply cladding in a project, with fixed GUIDs so schedules and filters
carry over between projects. A CT_ parameter that exists with the wrong type
(e.g. made by hand as Text) is replaced with the right one, and per-panel ones
are moved to Dimensions; the output window lists what changed.

Cladding made with an older version: select a panel and click Regenerate once.

## Panel marks
Every cladding panel gets a mark in **CT_PanelType** (P01, P02...). Panels
that are physically the same (profile, material, thickness, perforation,
width and length) share a mark across the whole project, copies included; a
copy you resize gets a new mark. Marks are stored in the project, so P03 stays
P03 when new sizes are added and tags keep pointing at the right type.
**CT_Cut** says "Cut" for panels cut around an opening or edge; they share
the mark of their full size but get their own row in the schedule.

## Perforated panels
In the cladding window (Apply or Switch) pick **Perforation**: round holes,
staggered 60° or straight rows, with hole Ø and pitch; the open area is shown.
Per panel use **CT_Perforation** (e.g. `Ø5/8 staggered`, `5/8 straight` or
`none`); the same value on every panel becomes the cladding default.
- The sheet stays solid geometry. The tool makes a material variant
  `<material> | perforated Ø5/8 staggered` with a cutout image (real
  see-through holes in Ray Trace and render plugins like Enscape/Twinmotion/
  D5, not in Realistic) and a dot surface pattern (holes in elevations/3D).
- Cutout images are saved in `library/textures`, so keep the extension on a
  shared path (S:) for renders on other machines.
- CT_OpenArea shows the open %, CT_Weight is reduced by it; both are in the
  Cladding Schedule.
- If Revit refuses the cutout (non-Generic appearance), the output window says
  so and gives the image path to set by hand.

## Copying panels
Copy a panel (Copy, Ctrl+C/V, Array...) and the copy becomes its own,
independent panel: it stays where you put it, keeps the size, profile and
material it had, and editing CT_ values on it (width, length, profile,
material, standoff) changes only the copy. The original and the rest of the
cladding are not touched and the copy is not deleted on Regenerate.
Copies show "copy of F1 S3 P1" in CT_PanelId and "<cladding> (copies)" in
CT_Group, so they get their own group in the schedule.
- A copy no longer follows the cladding (Switch Cladding, wall changes).
- A copy that was cut around a window is rebuilt as a full rectangle the
  first time you edit it.
- Moving an ordinary (non-copied) panel with Move doesn't stick: the next
  rebuild puts it back in the layout. Copy it instead, or change its size.

## Curved walls
Pick the face of a wall that is curved in plan and it is clad like a flat
one: the face is unrolled, panels are laid out by arc length, and each panel
is bent to the curve with vertical ribs (flat parts of the profile follow the
arc too). Openings, per-panel edits, overhangs, perforation and schedules
work the same; widths and lengths are measured along the curve. Cladding on
the inside of a curve works too. Flashings follow the curve (head and sill are
swept along the arc, jambs stay vertical), and copied curved panels can be
edited on their own. A copy that is moved keeps its curve but isn't re-fitted
to a different radius.

## Corners
Where two claddings meet at a corner (two walls, or two faces picked in one
Apply), exactly one of them covers the corner:
- **Outside corner:** one cladding runs past the corner by the other
  cladding's thickness, so the end of the other cladding and the wall corner
  are covered.
- **Inside corner:** one cladding is shortened by the other's thickness, so
  they don't clash.

When the walls aren't joined at the outside corner, so the end of one wall
shows between the claddings (up to 600 mm), that strip is covered too.

Which one laps is fixed per corner, so both sides always agree. Apply,
Regenerate and standoff/profile edits also rebuild the cladding on the other
side of the corner. Cladding applied before this version: Regenerate it once.
Corners against walls without cladding are left as they are.

## Flashings
- Each side (head, left jamb, right jamb, sill) is its own Generic Model:
  select one and press Delete to remove just that side.
- The head runs the full width plus both laps and sits over the jambs; the sill
  runs under them, the way trims lap on site.
- Flashings always go on the wall's exterior side (Revit's exterior).
- **CT_Standoff** on a flashing = wall to the underside of the flashing. It
  starts just outside the CladdingTools cladding on that side (cladding
  standoff + sheet + profile depth + 1 mm; 50 mm without cladding) and can be
  changed per piece.
- **CT_Material** (Materials and Finishes, material picker) and **CT_Profile** (flashing type) can be changed per piece
  too, or pick another `Flashing - <type> - <material>` type in the Type
  Selector. The piece is rebuilt in place.
- Running Add Flashings again on a door/window replaces its flashings, e.g.
  after the cladding or the opening changed (flashings don't follow cladding
  changes by themselves).
- Types are named `Flashing - <type> - <material>`. Parameters: CT_Profile
  (type), CT_Material, CT_PanelLength (run length), CT_DevelopedArea, CT_Weight,
  CT_PanelId (e.g. "Window 345678 head"), CT_Group = "Flashing".

Flashing types live in `library/flashings/*.json`:

```json
{"name": "Box trim 100", "face_width": 100, "kick": 15, "return": true, "thickness": 0.7}
```
`face_width` = lap over the cladding, `kick` = turn-down at its outer edge,
`return` = leg from the cladding back to the wall inside the opening.

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

## Known limits
- Curved faces: walls curved in plan (vertical cylindrical faces) work with
  vertical ribs. Horizontal ribs, domes and other curved shapes don't yet.
  Curved walls get no corner joints yet.
- No end laps, flashings or corner trims.
- Panels are DirectShapes: no grips. Change them through the CT_ parameters.
- Written for Revit 2024 and later on pyRevit's IronPython engine.
