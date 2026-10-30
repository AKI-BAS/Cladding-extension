# -*- coding: utf-8 -*-
"""Change existing cladding in the same window used to create it.

Select a panel (or Select Cladding first), click Switch Cladding: the window
opens with the cladding's current settings. Change the profile, material,
widths, joint, standoff... and click Switch cladding. Panel sizes, cut-outs,
corner laps and tags are kept; only what you changed is applied.
"""
__title__ = "Switch\nCladding"
__doc__ = ("Open the cladding window with the selected cladding's settings, change them, "
           "and switch the cladding over, keeping panel sizes, corners and tags.")

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import DirectShape

from cladding import profiles, groupdata, revit_build
from cladding.window import CladdingWindow

doc = revit.doc
out = script.get_output()

groups = {}
for el in revit.get_selection():
    if isinstance(el, DirectShape) and el.ApplicationId == revit_build.APP_ID \
            and el.ApplicationDataId not in groups:
        g, _ = revit_build.group_for_panel(doc, el)
        if g:
            groups[g["group"]] = g
if not groups:
    forms.alert("Select a cladding panel first.\n\n"
                "Tip: select one panel and click Select Cladding to select the whole cladding.",
                exitscript=True)

first = list(groups.values())[0]
frames, layouts = [], []
for g in groups.values():
    try:
        for i, (_, f) in enumerate(revit_build.resolve_faces(doc, g["faces"])):
            frames.append(revit_build.FaceFrame(f))
            layouts.append(g["layouts"][i] if i < len(g["layouts"]) else None)
    except Exception:
        pass

note = u"Only what you change is applied. Your per-panel sizes are kept; a new profile, " \
       u"material or standoff replaces per-panel ones."
if len(groups) > 1:
    note += u" Showing the first of {} claddings; changes go to all of them.".format(len(groups))

win = CladdingWindow(doc, frames, profiles.load_library(), initial=first["recipe"],
                     layouts=layouts, mode="switch", note=note)
win.ShowDialog()
if not win.result:
    script.exit()

lines, neighbors = [], 0
with revit.Transaction("Switch cladding"):
    for gid, g in groups.items():
        new_g, changes = groupdata.switch_settings(g, win.result, snap=not win.keep_sizes)
        if not changes:
            lines.append(u"`{}`: nothing changed".format(gid))
            continue
        try:
            faces = revit_build.resolve_faces(doc, new_g["faces"])
        except Exception as ex:
            lines.append(u"**Cladding `{}` skipped:** can't find its wall/roof ({})".format(gid, ex))
            continue
        summary = revit_build.build(doc, faces, new_g)
        lines.extend(u"`{}`: {}".format(gid, c) for c in changes)
        lines.append(u"`{}`: {} panels updated in place, {} new, {} removed".format(
            gid, summary["updated"], summary["created"], summary["removed"]))
        if summary["missing_materials"]:
            lines.append(u"No material called {} in this project".format(
                u", ".join(summary["missing_materials"])))
    for gid in groups:
        g = revit_build.load_group(doc, gid)
        if g:
            neighbors += revit_build.rebuild_neighbors(doc, g)

out.print_md(u"### Switch cladding")
for line in lines:
    out.print_md(u"- " + line)
if neighbors:
    out.print_md(u"- {} neighbouring cladding(s) updated so the corners match".format(neighbors))
out.print_md(u"- Flashings keep their position; run Add Flashings again if the cladding "
             u"got deeper or shallower.")
