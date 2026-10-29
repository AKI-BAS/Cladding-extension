# -*- coding: utf-8 -*-
"""Rebuild the selected cladding, keeping all panel edits.

Use it after the wall/roof moved or changed shape, or once for cladding made
with an older version of the tool. Parameter edits update live without it.

Shift+Click for more: reset all panel edits, or change the profile of the
whole cladding.
"""
__title__ = "Regenerate"
__doc__ = ("Rebuild the selected cladding to follow its wall/roof, keeping panel edits. "
           "Shift+Click to reset panel edits or change the profile for the whole cladding.")

from pyrevit import revit, forms, script, EXEC_PARAMS
from Autodesk.Revit.DB import DirectShape

from cladding import profiles, groupdata, revit_build

doc = revit.doc
out = script.get_output()

group = None
for el in revit.get_selection():
    if isinstance(el, DirectShape):
        group, _ = revit_build.group_for_panel(doc, el)
        if group:
            break
if not group:
    forms.alert("Select a cladding panel made with Apply Cladding.", exitscript=True)

note = u"Rebuilt to follow the wall/roof; panel edits kept."
if EXEC_PARAMS.config_mode:                      # Shift+Click
    RESET = "Reset all panel edits (sizes, profiles, materials)"
    PROFILE = "Change the profile of the whole cladding..."
    choice = forms.CommandSwitchWindow.show([RESET, PROFILE], message="Regenerate")
    if not choice:
        script.exit()
    if choice == RESET:
        group = groupdata.reset_edits(group)
        note = u"All panel edits reset."
    else:
        lib = profiles.load_library()
        names = [p["name"] for p in lib]
        pick = forms.SelectFromList.show(names, title="Profile for the whole cladding",
                                         multiselect=False)
        if not pick:
            script.exit()
        group = groupdata.set_default_profile(group, lib[names.index(pick)])
        note = u"Profile → {} for all panels.".format(pick)

try:
    faces = revit_build.resolve_faces(doc, group["faces"])
except Exception as ex:
    forms.alert("Can't find the wall/roof face anymore ({}). "
                "Delete the cladding and apply it again.".format(ex), exitscript=True)

with revit.Transaction("Regenerate cladding"):
    summary = revit_build.build(doc, faces, group)

t = summary["totals"]
out.print_md(u"### Regenerated `{}`".format(group["group"]))
out.print_md(u"- " + note)
out.print_md(u"- {} panels updated in place, {} new, {} removed ({} cut around openings or edges)"
             .format(summary["updated"], summary["created"], summary["removed"],
                     summary["trimmed"]))
out.print_md(u"- Sheet area {:.1f} m², weight {:.0f} kg".format(
    t["developed_area_m2"], t["weight_kg"]))
if summary["missing_materials"]:
    out.print_md(u"- No material called {} in this project, built without it".format(
        u", ".join(summary["missing_materials"])))
