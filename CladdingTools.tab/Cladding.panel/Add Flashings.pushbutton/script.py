# -*- coding: utf-8 -*-
"""Add flashings around doors and windows.

Select doors/windows (or pick them after clicking), then choose:
  - Add flashings: head, both jambs, and a sill on windows that aren't at
    floor level. Doors never get a sill.
  - Add flashings, no sills
  - Remove flashings from the selected doors/windows

Each side is its own element: select one and press Delete to remove just
that side. Edit CT_Profile / CT_Material / CT_Standoff on a piece to change
it. Adding again replaces a door/window's flashings, e.g. after the cladding
or the opening changed.
"""
__title__ = "Add\nFlashings"
__doc__ = ("Add flashings around the selected doors and windows (head, jambs, sill). "
           "Also removes them.")

from pyrevit import revit, forms, script
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.DB import FilteredElementCollector, Material
from Autodesk.Revit.Exceptions import OperationCanceledException

from cladding import flashing, revit_flashing, revit_params

doc = revit.doc
uidoc = revit.uidoc
out = script.get_output()


class OpeningFilter(ISelectionFilter):
    def AllowElement(self, el):
        return revit_flashing.is_opening(el)

    def AllowReference(self, ref, pt):
        return False


openings = [el for el in revit.get_selection() if revit_flashing.is_opening(el)]
if not openings:
    try:
        refs = uidoc.Selection.PickObjects(ObjectType.Element, OpeningFilter(),
                                           "Pick doors and windows, then click Finish")
    except OperationCanceledException:
        script.exit()
    openings = [doc.GetElement(r) for r in refs]
if not openings:
    script.exit()

ADD, NO_SILL, REMOVE = ("Add flashings (head, jambs, sill)",
                        "Add flashings, no sills",
                        "Remove flashings")
action = forms.CommandSwitchWindow.show(
    [ADD, NO_SILL, REMOVE],
    message=u"{} door(s)/window(s) selected".format(len(openings)))
if not action:
    script.exit()

if action == REMOVE:
    with revit.Transaction("Remove flashings"):
        n = sum(revit_flashing.remove(doc, o) for o in openings)
    out.print_md(u"Removed {} flashing pieces.".format(n))
    script.exit()

library = flashing.load_library()
if not library:
    forms.alert("No flashing types found in:\n{}".format(flashing.library_dir()), exitscript=True)
names = [f["name"] for f in library]
pick = forms.SelectFromList.show(names, title="Flashing type", multiselect=False)
if not pick:
    script.exit()
ftype = library[names.index(pick)]

CLAD = "(same as the cladding)"
NONE = "(no material)"
mats = sorted(m.Name for m in FilteredElementCollector(doc).OfClass(Material))
mpick = forms.SelectFromList.show([CLAD, NONE] + mats, title="Flashing material",
                                  multiselect=False)
if not mpick:
    script.exit()
material = None if mpick == CLAD else (u"" if mpick == NONE else mpick)

done, failed, cache = [], [], {}
with revit.Transaction("Add flashings"):
    report = revit_params.ensure_parameters(doc)
    for o in openings:
        try:
            sides, used, standoff = revit_flashing.add(doc, o, ftype, material,
                                                       action == ADD, cache)
            done.append((o, sides, used, standoff))
        except Exception as ex:
            failed.append((o, ex))

out.print_md(u"### {} flashings".format(ftype["name"]))
for o, sides, used, standoff in done:
    out.print_md(u"- {} {}: {} ({}, {:g} mm from the wall)".format(
        revit_flashing.opening_kind(o).capitalize(), o.Id, u", ".join(sides),
        used or u"no material", standoff))
for o, ex in failed:
    out.print_md(u"- **{} {} skipped:** {}".format(
        revit_flashing.opening_kind(o).capitalize(), o.Id, ex))
for line in revit_params.describe(report):
    out.print_md(u"- " + line)
out.print_md(u"- Change a piece: edit CT_Profile (flashing type), CT_Material or CT_Standoff "
             u"(wall to underside of the flashing) in Properties (material under Materials and Finishes), or pick another "
             u"Flashing type in the Type Selector.")
