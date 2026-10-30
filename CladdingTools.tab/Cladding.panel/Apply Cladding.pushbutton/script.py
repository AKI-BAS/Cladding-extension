# -*- coding: utf-8 -*-
"""Pick faces, choose a profile and panel settings, build profiled cladding."""
__title__ = "Apply\nCladding"
__doc__ = "Pick wall, roof, floor or ceiling faces and clad them with a profiled sheet."

from pyrevit import revit, forms, script
from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

from cladding import profiles, revit_build, groupdata, revit_params
from cladding.window import CladdingWindow

doc = revit.doc
uidoc = revit.uidoc


def pick_faces():
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Face, "Pick flat faces to clad, then click Finish")
    except OperationCanceledException:
        return []
    faces, skipped = [], 0
    for ref in refs:
        face = doc.GetElement(ref.ElementId).GetGeometryObjectFromReference(ref)
        if revit_build.is_supported(face):
            faces.append((ref, face))
        else:
            skipped += 1
    if skipped:
        forms.alert("{} face(s) skipped. Flat faces and curved walls (vertical curved "
                    "faces) can be clad; other curved shapes can't yet.".format(skipped))
    return faces


# ---------------------------------------------------------------------------
library = profiles.load_library()
if not library:
    forms.alert("No profiles found in:\n{}".format(profiles.library_dir()), exitscript=True)

faces = pick_faces()
if not faces:
    script.exit()

win = CladdingWindow(doc, [revit_build.make_frame(f) for _, f in faces], library)
win.ShowDialog()

if win.result:
    group = groupdata.new_group(revit_build.new_group_id(), win.result)
    with revit.Transaction("Apply cladding"):
        summary = revit_build.build(doc, faces, group)
        summary["neighbors"] = revit_build.rebuild_neighbors(doc, group)
    t = summary["totals"]
    out = script.get_output()
    out.print_md(u"### Cladding `{}` created".format(group["group"]))
    out.print_md(u"- {} panels, {} cut around openings or edges, {} fully inside openings"
                 .format(summary["created"], summary["trimmed"], summary["skipped"]))
    out.print_md(u"- Cover area {:.1f} m², sheet area {:.1f} m², weight {:.0f} kg".format(
        t["cover_area_m2"], t["developed_area_m2"], t["weight_kg"]))
    if summary["missing_materials"]:
        out.print_md(u"- No material called {} in this project, built without it".format(
            u", ".join(summary["missing_materials"])))
    if summary.get("corners") or summary.get("neighbors"):
        out.print_md(u"- {} corner joint(s) with other cladding; {} neighbouring "
                     u"cladding(s) updated to match".format(summary.get("corners", 0),
                                                            summary.get("neighbors", 0)))
    for line in revit_params.describe(summary.get("params", {})):
        out.print_md(u"- " + line)
    out.print_md(u"- Change one panel: edit its values under **Dimensions** (material under **Materials and Finishes**) in Properties. "
                 u"Change the whole cladding: Select All Instances, or edit CT_CoverWidth, "
                 u"CT_MaxLength, CT_Joint or CT_Orientation on any panel.")
