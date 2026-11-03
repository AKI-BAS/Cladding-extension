# -*- coding: utf-8 -*-
"""Give an existing cladding a new boundary.

Draw a new closed loop of lines on the wall, select a panel of the cladding
and the new lines (or click and pick them), and the cladding re-fits to the
new boundary. Panel edits are kept where they still apply:
  - boundary start corner unchanged: the layout and all edits stay; panels
    now outside the boundary go
  - start corner moved: each edit goes to the panel at the same spot on the
    wall; edits on spots that are no longer clad are dropped
Works on whole-wall cladding too (it becomes a region cladding).

Shift+Click: remove the boundary, so the cladding covers the whole face again.
"""
__title__ = "Edit\nBoundary"
__doc__ = ("Re-fit a cladding to new boundary lines, keeping panel edits where they still "
           "apply. Shift+Click to remove the boundary (clad the whole face).")

from pyrevit import revit, forms, script, EXEC_PARAMS
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.DB import CurveElement, DirectShape, PlanarFace
from Autodesk.Revit.Exceptions import OperationCanceledException

from cladding import groupdata, revit_build

doc = revit.doc
uidoc = revit.uidoc
out = script.get_output()
MM = revit_build.MM


class Filter(ISelectionFilter):
    def __init__(self, kind):
        self.kind = kind

    def AllowElement(self, el):
        if self.kind == "lines":
            return isinstance(el, CurveElement)
        return isinstance(el, DirectShape) and el.ApplicationId == revit_build.APP_ID \
            and not el.ApplicationDataId.endswith(revit_build.COPY_SUFFIX)

    def AllowReference(self, ref, pt):
        return False


sel = revit.get_selection()
panel = next((el for el in sel if Filter("panel").AllowElement(el)), None)
lines = [el for el in sel if isinstance(el, CurveElement)]
try:
    if panel is None:
        panel = doc.GetElement(uidoc.Selection.PickObject(
            ObjectType.Element, Filter("panel"), "Edit Boundary: pick a panel of the cladding"))
except OperationCanceledException:
    script.exit()

group, pd = revit_build.group_for_panel(doc, panel)
if not group or not pd or "key" not in pd:
    forms.alert("Pick a panel of a cladding made with Apply Cladding or Clad Region "
                "(Regenerate older cladding once first).", exitscript=True)
fi = int(pd["key"].split(":")[0])
ref, face = revit_build.resolve_faces(doc, group["faces"])[fi]
if not isinstance(face, PlanarFace):
    forms.alert("Boundaries work on flat faces only.", exitscript=True)
frame = revit_build.FaceFrame(face)

if EXEC_PARAMS.config_mode:                              # remove the boundary
    if not groupdata.region(group, fi):
        forms.alert("This cladding has no boundary on that face.", exitscript=True)
    new_group, kept, dropped = groupdata.refit_region(
        group, fi, [(frame.extents[0], frame.extents[2]), (frame.extents[1], frame.extents[2]),
                    (frame.extents[1], frame.extents[3]), (frame.extents[0], frame.extents[3])],
        frame.extents)
    new_group["regions"].pop(str(fi), None)
    what = u"Boundary removed: the cladding covers the whole face again"
else:
    if not lines:
        try:
            refs = uidoc.Selection.PickObjects(
                ObjectType.Element, Filter("lines"),
                "Edit Boundary: pick the lines of the new boundary, then click Finish")
        except OperationCanceledException:
            script.exit()
        lines = [doc.GetElement(r) for r in refs]
    segments = [[((p - frame.origin).DotProduct(frame.right) / MM,
                  (p - frame.origin).DotProduct(frame.up) / MM)
                 for p in el.GeometryCurve.Tessellate()] for el in lines]
    try:
        poly = groupdata.chain_outline(segments)
    except ValueError as ex:
        forms.alert(u"{}".format(ex), exitscript=True)
    new_group, kept, dropped = groupdata.refit_region(group, fi, poly, frame.extents)
    box = groupdata.polygon_box(poly)
    what = u"New boundary {:.0f} × {:.0f} mm, {:.2f} m²".format(
        box[1] - box[0], box[3] - box[2], abs(groupdata.polygon_area(poly)) / 1e6)

with revit.Transaction("Edit cladding boundary"):
    summary = revit_build.build(doc, revit_build.resolve_faces(doc, new_group["faces"]),
                                new_group)
    neighbors = revit_build.rebuild_neighbors(doc, new_group)

out.print_md(u"### Edit boundary: cladding `{}`".format(new_group["group"]))
out.print_md(u"- " + what)
out.print_md(u"- Panel edits: {} kept, {} dropped (their spot is no longer clad)".format(
    kept, dropped))
out.print_md(u"- {} panels rebuilt, {} unchanged, {} new, {} removed".format(
    summary["updated"], summary.get("unchanged", 0), summary["created"], summary["removed"]))
if neighbors:
    out.print_md(u"- {} neighbouring cladding(s) updated so the corners match".format(neighbors))
