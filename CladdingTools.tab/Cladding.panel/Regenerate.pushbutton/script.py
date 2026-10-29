# -*- coding: utf-8 -*-
"""Rebuild a cladding group from its CT_ parameters.

1. Select any panel of the cladding.
2. Change CT_Profile, CT_CoverWidth, CT_MaxLength, CT_Joint, CT_Standoff,
   CT_Orientation or CT_Material in Properties (on any panel of the group).
3. Click Regenerate. The whole group is rebuilt with the new values and
   follows the host if the wall/roof moved.

Shift+Click picks a different profile from a list instead.
"""
__title__ = "Regenerate"
__doc__ = ("Rebuild the selected cladding from its CT_ parameters "
           "(edit them in Properties first). Shift+Click to pick a new profile from a list.")

from pyrevit import revit, forms, script, EXEC_PARAMS
from Autodesk.Revit.DB import DirectShape, ElementId

from cladding import profiles, inputs, revit_build, revit_params

doc = revit.doc
out = script.get_output()

# --- find the group from the selection ---------------------------------------
sel = [el for el in revit.get_selection() if isinstance(el, DirectShape)]
data, picked = None, None
for el in sel:
    data = revit_build.read_stored(el)
    if data:
        picked = el
        break
if not data:
    forms.alert("Select a cladding panel made with Apply Cladding.", exitscript=True)

stored = data["recipe"]
library = profiles.load_library()
panels = revit_build.group_panels(doc, data["group"])

# --- work out the new recipe ----------------------------------------------------
recipe, changes = stored, []
if EXEC_PARAMS.config_mode:                      # Shift+Click: pick from a list
    names = [p["name"] for p in library]
    pick = forms.SelectFromList.show(names, title="New profile", multiselect=False)
    if not pick:
        script.exit()
    recipe, changes = inputs.apply_inputs(stored, {"CT_Profile": pick}, library)
else:
    # The selected panel wins; otherwise the first panel someone edited.
    candidates = [picked] + [p for p in panels if p.Id != picked.Id]
    try:
        for el in candidates:
            el_data = revit_build.read_stored(el) or data
            values, _ = inputs.requested_values(
                revit_params.get_inputs(el), revit_params.get_outputs(el), el_data)
            r, c = inputs.apply_inputs(stored, values, library)
            if c:
                recipe, changes = r, c
                break
    except ValueError as ex:
        forms.alert(u"{}\n\nFix the value in Properties and run Regenerate again."
                    .format(ex), exitscript=True)

if recipe.get("material") and \
        revit_build.find_material(doc, recipe["material"]) == ElementId.InvalidElementId:
    if not forms.alert(u"No material called '{}' in this project. Continue without "
                       u"a material?".format(recipe["material"]), yes=True, no=True):
        script.exit()

try:
    faces = revit_build.resolve_faces(doc, data["faces"])
except Exception as ex:
    forms.alert("Can't find the host face anymore ({}). "
                "Delete the cladding and apply it again.".format(ex), exitscript=True)

# --- rebuild ---------------------------------------------------------------------
with revit.Transaction("Regenerate cladding"):
    removed = revit_build.delete_group(doc, data["group"])
    group, made, skipped, totals = revit_build.build(doc, faces, recipe, data["group"])

out.print_md(u"### Regenerated `{}`".format(group))
if changes:
    for c in changes:
        out.print_md(u"- {}".format(c))
else:
    out.print_md(u"- No parameter changes; rebuilt to follow the host.")
out.print_md(u"- Removed {}, created {} panels ({} skipped)".format(removed, made, skipped))
out.print_md(u"- Sheet area {:.1f} m², weight {:.0f} kg".format(
    totals["developed_area_m2"], totals["weight_kg"]))
