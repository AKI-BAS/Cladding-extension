# -*- coding: utf-8 -*-
"""Apply CT_ edits and rebuild the selected cladding(s) and flashing pieces.

- Picks up every CT_ value you changed in Properties (works even when live
  updates are off), then rebuilds so the cladding follows its wall/roof.
  Panels are updated in place, so tags stay.
- Also run it once on cladding made with an older version of the tool.
- Shows whether live updates are on in this Revit session.

Shift+Click: reset all panel edits, or change the profile of the whole cladding.
"""
__title__ = "Regenerate"
__doc__ = ("Apply CT_ edits and rebuild the selected cladding/flashings, keeping tags. "
           "Shift+Click to reset panel edits or change the whole cladding's profile.")

from pyrevit import revit, forms, script, EXEC_PARAMS, HOST_APP
from Autodesk.Revit.DB import DirectShape

from cladding import profiles, groupdata, revit_build, revit_params, revit_flashing, live

doc = revit.doc
out = script.get_output()

# --- what is selected ---------------------------------------------------------
groups, flashings = {}, []
for el in revit.get_selection():
    if not isinstance(el, DirectShape):
        continue
    if el.ApplicationId == revit_build.APP_ID and \
            el.ApplicationDataId.endswith(revit_build.COPY_SUFFIX):
        flashings.append(el)          # copied panels: edited on their own
    elif el.ApplicationId == revit_build.APP_ID and el.ApplicationDataId not in groups:
        g, _ = revit_build.group_for_panel(doc, el)
        if g:
            groups[g["group"]] = g
    elif el.ApplicationId == revit_flashing.FLASH_APP:
        flashings.append(el)
if not groups and not flashings:
    forms.alert("Select a cladding panel or a flashing made with CladdingTools.", exitscript=True)

# --- Shift+Click options (whole cladding) ---------------------------------------
note = None
if EXEC_PARAMS.config_mode and groups:
    RESET = "Reset all panel edits (sizes, profiles, materials)"
    PROFILE = "Change the profile of the whole cladding..."
    choice = forms.CommandSwitchWindow.show([RESET, PROFILE], message="Regenerate")
    if not choice:
        script.exit()
    if choice == RESET:
        groups = dict((k, groupdata.reset_edits(g)) for k, g in groups.items())
        note = u"All panel edits reset."
    else:
        lib = profiles.load_library()
        names = [p["name"] for p in lib]
        pick = forms.SelectFromList.show(names, title="Profile for the whole cladding",
                                         multiselect=False)
        if not pick:
            script.exit()
        groups = dict((k, groupdata.set_default_profile(g, lib[names.index(pick)]))
                      for k, g in groups.items())
        note = u"Profile → {} for all panels.".format(pick)

# --- apply edits, then rebuild ------------------------------------------------------
results = []
with revit.Transaction("Regenerate cladding"):
    report = revit_params.ensure_parameters(doc)
    if note is None:
        ids = [p.Id for gid in groups for p in revit_build.group_panels(doc, gid)]
        ids += [f.Id for f in flashings]
        edit_lines = live.run(doc, ids, warn=False)
    else:
        edit_lines = []
    for gid, g in groups.items():
        if note is None:
            g = revit_build.load_group(doc, gid) or g      # pick up applied edits
        try:
            faces = revit_build.resolve_faces(doc, g["faces"])
        except Exception as ex:
            results.append((gid, None, ex))
            continue
        results.append((gid, revit_build.build(doc, faces, g), None))
    neighbors = 0
    for gid in groups:
        g = revit_build.load_group(doc, gid)
        if g:
            neighbors += revit_build.rebuild_neighbors(doc, g)

# --- report ------------------------------------------------------------------------
out.print_md(u"### Regenerate")
if note:
    out.print_md(u"- " + note)
if edit_lines:
    out.print_md(u"**Edits applied**")
    for line in edit_lines:
        out.print_md(u"- " + line)
elif note is None:
    out.print_md(u"- No CT_ edits found; rebuilt to follow the wall/roof.")
for gid, summary, err in results:
    if err:
        out.print_md(u"- **Cladding `{}`:** can't find its wall/roof face ({}). "
                     u"Delete it and apply it again.".format(gid, err))
        continue
    t = summary["totals"]
    if summary.get("copies"):
        out.print_md(u"- {} copied panel(s) made independent".format(summary["copies"]))
    out.print_md(u"- Cladding `{}`: {} panels updated in place, {} new, {} removed, "
                 u"{} cut around openings. Sheet {:.1f} m², {:.0f} kg".format(
                     gid, summary["updated"], summary["created"], summary["removed"],
                     summary["trimmed"], t["developed_area_m2"], t["weight_kg"]))
    if summary.get("corners"):
        out.print_md(u"  - {} corner joint(s) with other cladding".format(summary["corners"]))
    if summary["missing_materials"]:
        out.print_md(u"  - No material called {} in this project".format(
            u", ".join(summary["missing_materials"])))
if neighbors:
    out.print_md(u"- {} neighbouring cladding(s) updated so the corners match".format(neighbors))
for line in revit_params.describe(report):
    out.print_md(u"- " + line)

running, why = live.status(HOST_APP.addin_id)
out.print_md(u"- Live updates: **{}**{}".format(
    "on" if running else "off",
    u"" if running and why == u"on" else u" ({})".format(why)))
if not running:
    out.print_md(u"  Edits still work: change CT_ values, then click Regenerate.")
