# -*- coding: utf-8 -*-
"""Create a schedule of all CladdingTools cladding panels.

Rows group identical pieces (type, material, thickness, size) with a count;
areas and weight are totalled. Shift+Click: one row per panel instead.
If the schedule already exists it is opened; it updates by itself.
"""
__title__ = "Cladding\nSchedule"
__doc__ = ("Create (or open) a schedule of all cladding panels with sizes, material, thickness, "
           "areas and weight. Shift+Click for one row per panel.")

from pyrevit import revit, script, EXEC_PARAMS

from cladding import revit_params, revit_schedule

doc = revit.doc
out = script.get_output()
spec = revit_schedule.CLADDING
itemized = bool(EXEC_PARAMS.config_mode)
name = spec["name"] + (u" (every piece)" if itemized else u"")

vs = revit_schedule.find(doc, name)
missing = []
if vs is None:
    with revit.Transaction("Create cladding panels schedule"):
        report = revit_params.ensure_parameters(doc)
        vs, missing = revit_schedule.create(doc, spec, itemized)
    out.print_md(u"### Created schedule **{}**".format(vs.Name))
    for line in revit_params.describe(report):
        out.print_md(u"- " + line)
    if missing:
        out.print_md(u"- Fields not found: {}".format(u", ".join(missing)))
    out.print_md(u"- Thickness and areas fill in after a Regenerate on cladding made "
                 u"with an older version.")
revit.uidoc.ActiveView = vs
