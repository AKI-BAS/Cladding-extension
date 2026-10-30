# -*- coding: utf-8 -*-
"""Join two claddings at a corner, like Trim/Extend for walls.

Pick a panel on each side of the corner (near the corner). Choose which side
laps over the other. Both claddings are rebuilt so they meet:
  - outside corner: the lapping side runs past the corner and covers the
    other's end; the other side tucks in under the lapping sheet, so no gap
    shows even with a large standoff
  - inside corner: the lapping side runs into the corner, the other stops
    against it
The sizes come from the picked panels (their standoff and profile), and the
joint is remembered: it is redone whenever either cladding is rebuilt, e.g.
after changing the standoff.
"""
__title__ = "Trim\nCorner"
__doc__ = ("Join two claddings at a corner: pick a panel on each side, choose which laps. "
           "Remembered, so it follows standoff changes.")

from pyrevit import revit, forms, script
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.DB import DirectShape
from Autodesk.Revit.Exceptions import OperationCanceledException

from cladding import revit_build

doc = revit.doc
uidoc = revit.uidoc
out = script.get_output()


class PanelFilter(ISelectionFilter):
    def AllowElement(self, el):
        return isinstance(el, DirectShape) and el.ApplicationId == revit_build.APP_ID \
            and not el.ApplicationDataId.endswith(revit_build.COPY_SUFFIX)

    def AllowReference(self, ref, pt):
        return False


def pick(prompt):
    try:
        return doc.GetElement(uidoc.Selection.PickObject(
            ObjectType.Element, PanelFilter(), prompt))
    except OperationCanceledException:
        script.exit()


el_a = pick("Trim Corner: pick a cladding panel on the first side, near the corner")
el_b = pick("Trim Corner: pick a cladding panel on the other side of the corner")

try:
    a, face_a, g_a = revit_build.joint_end(doc, el_a)
    b, face_b, g_b = revit_build.joint_end(doc, el_b)
except ValueError as ex:
    forms.alert(u"{}".format(ex), exitscript=True)
if a["group"] == b["group"] and a["face"] == b["face"]:
    forms.alert("Both panels are on the same face. Pick one on each side of the corner.",
                exitscript=True)

frame_a, frame_b = revit_build.FaceFrame(face_a), revit_build.FaceFrame(face_b)
def nearest_end(frame, other):
    """The end of a face that is at the corner (smallest distance to the other face)."""
    hits = [(abs(r[0]), side) for side, r in
            ((sd, revit_build._corner(frame, sd, other)) for sd in (-1, 1)) if r is not None]
    return [sorted(hits)[0][1]] if hits else []


side_a = nearest_end(frame_a, face_b)
side_b = nearest_end(frame_b, face_a)
if not side_a or not side_b:
    forms.alert("These two claddings don't meet at a corner (their faces must be at an "
                "angle and end within {:g} mm of each other).".format(revit_build.CORNER_GAP),
                exitscript=True)
a["side"], b["side"] = side_a[0], side_b[0]
outside = revit_build._corner(frame_a, a["side"], face_b)[1] > 0

LAP_A = u"First picked side laps over the other"
LAP_B = u"Second picked side laps over the other"
REMOVE = u"Remove this trim (automatic corner again)"
choice = forms.CommandSwitchWindow.show(
    [LAP_A, LAP_B, REMOVE],
    message=u"{} corner".format("Outside" if outside else "Inside"))
if not choice:
    script.exit()
lap = {LAP_A: "a", LAP_B: "b", REMOVE: None}[choice]

with revit.Transaction("Trim corner"):
    revit_build.set_joint(doc, a, b, lap)
    done = set()
    for gid in (a["group"], b["group"]):
        if gid in done:
            continue
        done.add(gid)
        g = revit_build.load_group(doc, gid)
        revit_build.build(doc, revit_build.resolve_faces(doc, g["faces"]), g,
                          ensure_params=False)

out.print_md(u"### Trim corner")
out.print_md(u"- {}".format(choice))
out.print_md(u"- Sizes follow the picked panels' standoff and profile; the joint is redone "
             u"whenever either cladding is rebuilt.")
