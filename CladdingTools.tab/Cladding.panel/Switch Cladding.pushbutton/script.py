# -*- coding: utf-8 -*-
"""Swap the cladding profile (and optionally material) on existing cladding,
keeping the layout: panel sizes, joints, openings, corner laps and tags.

Select one panel to change its whole cladding (or several claddings), or a
few panels to change just those.
"""
__title__ = "Switch\nCladding"
__doc__ = ("Swap the selected cladding to another profile/material, keeping panel sizes, "
           "joints, corners and tags.")

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import DirectShape, FilteredElementCollector, Material

from cladding import profiles, groupdata, revit_build

doc = revit.doc
out = script.get_output()

# --- selection -------------------------------------------------------------------
groups, picked = {}, {}
for el in revit.get_selection():
    if isinstance(el, DirectShape) and el.ApplicationId == revit_build.APP_ID:
        g, pd = revit_build.group_for_panel(doc, el)
        if g and pd:
            groups.setdefault(g["group"], g)
            if pd.get("key"):
                picked.setdefault(g["group"], set()).add(pd["key"])
if not groups:
    forms.alert("Select one or more cladding panels first.\n\n"
                "Tip: select one panel and click Select Cladding to select the whole cladding.",
                exitscript=True)

total = sum(len(revit_build.group_panels(doc, gid)) for gid in groups)
selected = sum(len(v) for v in picked.values())

# --- what to change ------------------------------------------------------------
ALL = u"Whole cladding ({} panels)".format(total)
SOME = u"Only the {} selected panel(s)".format(selected)
scope = ALL
if 0 < selected < total:
    scope = forms.CommandSwitchWindow.show([ALL, SOME], message="Switch which panels?")
    if not scope:
        script.exit()

lib = profiles.load_library()
names = [p["name"] for p in lib]
current = sorted(set(g["recipe"]["profile"].get("name", "") for g in groups.values()))
pick = forms.SelectFromList.show(
    names, title=u"New cladding profile (now: {})".format(u", ".join(current)),
    multiselect=False)
if not pick:
    script.exit()
new_profile = lib[names.index(pick)]

KEEP = "(keep current materials)"
NONE = "(no material)"
mats = sorted(m.Name for m in FilteredElementCollector(doc).OfClass(Material))
mpick = forms.SelectFromList.show([KEEP, NONE] + mats, title="Material", multiselect=False)
if not mpick:
    script.exit()
material = None if mpick == KEEP else (u"" if mpick == NONE else mpick)

snap = False
if scope == ALL:
    old_mods = set(round(profiles.module_width(profiles.profile_points(g["recipe"]["profile"])), 1)
                   for g in groups.values())
    new_mod = round(profiles.module_width(profiles.profile_points(new_profile)), 1)
    if old_mods != set([new_mod]):
        KEEP_SIZES = u"Keep panel sizes exactly (ribs may be cut at panel edges)"
        SNAP = u"Fit widths to whole {:g} mm ribs (strips shift slightly)".format(new_mod)
        how = forms.CommandSwitchWindow.show([KEEP_SIZES, SNAP], message="Panel widths")
        if not how:
            script.exit()
        snap = how == SNAP

# --- switch and rebuild ---------------------------------------------------------
lines, neighbors = [], 0
with revit.Transaction("Switch cladding"):
    for gid, g in groups.items():
        keys = None if scope == ALL else sorted(picked.get(gid, []))
        if keys == []:
            continue
        new_g, changes = groupdata.switch_profile(g, new_profile, keys, material, snap)
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

out.print_md(u"### Switch cladding → {}".format(pick))
for line in lines:
    out.print_md(u"- " + line)
if neighbors:
    out.print_md(u"- {} neighbouring cladding(s) updated so the corners match".format(neighbors))
out.print_md(u"- Flashings keep their position; run Add Flashings again if the new "
             u"profile is deeper or shallower.")
