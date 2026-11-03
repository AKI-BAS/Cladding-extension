# -*- coding: utf-8 -*-
"""Clad only part of a wall, inside a boundary you draw.

1. In an elevation (or 3D) draw a closed loop of detail or model lines around
   the area to clad. Any shape: rectangles, sloped edges, L-shapes.
2. Select the lines (or click this button and pick them), then pick the wall
   face.
3. The cladding window opens. "Fill the boundary with panels" lays panels out
   inside the boundary; untick it for one panel filling the whole boundary.

The boundary is stored with the cladding: Regenerate keeps to it, openings
inside it are still cut out, and everything else (edits, corners, flashings,
schedules, Switch Cladding) works as for whole-face cladding.
"""
__title__ = "Clad\nRegion"
__doc__ = ("Clad only the area inside a closed loop of lines you draw on a wall: filled "
           "with panels, or as one panel.")

from pyrevit import revit, forms, script
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.DB import CurveElement, PlanarFace
from Autodesk.Revit.Exceptions import OperationCanceledException

from cladding import profiles, groupdata, revit_build, revit_params
from cladding.window import CladdingWindow

doc = revit.doc
uidoc = revit.uidoc
MM = revit_build.MM


class LineFilter(ISelectionFilter):
    def AllowElement(self, el):
        return isinstance(el, CurveElement)

    def AllowReference(self, ref, pt):
        return False


# --- the boundary lines -------------------------------------------------------
lines = [el for el in revit.get_selection() if isinstance(el, CurveElement)]
if not lines:
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, LineFilter(),
            "Clad Region: pick the lines of the boundary, then click Finish")
    except OperationCanceledException:
        script.exit()
    lines = [doc.GetElement(r) for r in refs]
if not lines:
    forms.alert("Pick the lines of a closed boundary.", exitscript=True)

# --- the face ------------------------------------------------------------------
try:
    ref = uidoc.Selection.PickObject(ObjectType.Face, "Clad Region: pick the wall face to clad")
except OperationCanceledException:
    script.exit()
face = doc.GetElement(ref.ElementId).GetGeometryObjectFromReference(ref)
if not isinstance(face, PlanarFace):
    forms.alert("Clad Region works on flat faces. Use Apply Cladding for curved walls.",
                exitscript=True)
frame = revit_build.FaceFrame(face)

# --- chain the lines into one closed outline in the face's (u, v) -------------
segments = []
for el in lines:
    pts = list(el.GeometryCurve.Tessellate())
    segments.append([((p - frame.origin).DotProduct(frame.right) / MM,
                      (p - frame.origin).DotProduct(frame.up) / MM) for p in pts])


try:
    poly = groupdata.chain_outline(segments)
except ValueError as ex:
    forms.alert(u"{}".format(ex), exitscript=True)
off_face = max(abs((p - frame.origin).DotProduct(frame.normal)) / MM
               for el in lines for p in el.GeometryCurve.Tessellate())
frame.set_region(poly)

# --- settings and build ----------------------------------------------------------
library = profiles.load_library()
win = CladdingWindow(doc, [frame], library, region=True)
win.ShowDialog()
if not win.result:
    script.exit()

group = groupdata.new_group(revit_build.new_group_id(), win.result)
group["regions"] = {"0": [list(p) for p in poly]}
with revit.Transaction("Clad region"):
    summary = revit_build.build(doc, [(ref, face)], group)
    summary["neighbors"] = revit_build.rebuild_neighbors(doc, group)

out = script.get_output()
t = summary["totals"]
box = groupdata.polygon_box(poly)
w, h = box[1] - box[0], box[3] - box[2]
out.print_md(u"### Cladding `{}` in a boundary".format(group["group"]))
out.print_md(u"- Boundary {:.0f} × {:.0f} mm, {:.2f} m², {} corner(s)".format(
    w, h, abs(groupdata.polygon_area(poly)) / 1e6, len(poly)))
out.print_md(u"- {}: {} panel(s), {} cut to the boundary or openings".format(
    u"One panel" if win.result.get("single") else u"Filled with panels",
    summary["created"], summary["trimmed"]))
out.print_md(u"- Cover area {:.1f} m², sheet area {:.1f} m², weight {:.0f} kg".format(
    t["cover_area_m2"], t["developed_area_m2"], t["weight_kg"]))
if off_face > 50:
    out.print_md(u"- Note: the lines were up to {:.0f} mm away from the wall face; they were "
                 u"projected straight onto it.".format(off_face))
for line in revit_params.describe(summary.get("params", {})):
    out.print_md(u"- " + line)
out.print_md(u"- The boundary lines can be deleted or kept; the cladding remembers the shape.")
