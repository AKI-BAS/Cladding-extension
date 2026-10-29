# -*- coding: utf-8 -*-
"""Rebuild a cladding group after the host wall/roof changed.

Select any panel of the group. Shift-click to change settings first.
"""
__title__ = "Regenerate"
__doc__ = ("Rebuild the selected cladding from its stored recipe. "
           "Shift+Click to pick a different profile from the library first.")

from pyrevit import revit, forms, script, EXEC_PARAMS
from Autodesk.Revit.DB import DirectShape

from cladding import profiles, revit_build

doc = revit.doc

sel = [el for el in revit.get_selection() if isinstance(el, DirectShape)]
data = None
for el in sel:
    data = revit_build.read_stored(el)
    if data:
        break
if not data:
    forms.alert("Select a cladding panel made with Apply Cladding.", exitscript=True)

recipe = data["recipe"]

if EXEC_PARAMS.config_mode:   # Shift+Click: swap the profile
    lib = profiles.load_library()
    names = [p["name"] for p in lib]
    pick = forms.SelectFromList.show(names, title="New profile", multiselect=False)
    if pick:
        recipe["profile"] = lib[names.index(pick)]

try:
    faces = revit_build.resolve_faces(doc, data["faces"])
except Exception as ex:
    forms.alert("Can't find the host face anymore ({}). "
                "Delete the cladding and apply it again.".format(ex), exitscript=True)

with revit.Transaction("Regenerate cladding"):
    removed = revit_build.delete_group(doc, data["group"])
    group, made, skipped, totals = revit_build.build(doc, faces, recipe, data["group"])

script.get_output().print_md(
    "Regenerated `{}`: removed {}, created {} panels ({} skipped). "
    "Sheet area {:.1f} m², weight {:.0f} kg.".format(
        group, removed, made, skipped, totals["developed_area_m2"], totals["weight_kg"]))
