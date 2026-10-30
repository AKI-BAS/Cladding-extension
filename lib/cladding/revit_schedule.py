# -*- coding: utf-8 -*-
"""Create Revit schedules for CladdingTools cladding and flashings.

Ordinary Generic Model schedules built from the CT_ parameters, so they update
by themselves, can be placed on sheets and edited like any other schedule.
"""
import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (
    ViewSchedule, ElementId, BuiltInCategory, ScheduleFieldType, ScheduleFilter,
    ScheduleFilterType, ScheduleSortGroupField, FilteredElementCollector,
)

CLADDING = {
    "name": "CladdingTools - Cladding",
    "filter": ("CT_Group", ScheduleFilterType.NotEqual, "Flashing"),
    # (parameter, heading, total?)
    "fields": [
        ("CT_Group", "Cladding", False),
        ("CT_Profile", "Profile", False),
        ("CT_Material", "Material", False),
        ("CT_Thickness", "Thickness", False),
        ("CT_PanelWidth", "Width", False),
        ("CT_PanelLength", "Length", False),
        ("#count", "Count", False),
        ("CT_CoverArea", "Area (flat, wall covered)", True),
        ("CT_DevelopedArea", "Area (sheet, unfolded)", True),
        ("CT_Weight", "Weight (kg)", True),
    ],
    "itemized_extra": [("CT_PanelId", "Panel", False)],
    "group_by": "CT_Group",
}

FLASHINGS = {
    "name": "CladdingTools - Flashings",
    "filter": ("CT_Group", ScheduleFilterType.Equal, "Flashing"),
    "fields": [
        ("CT_Profile", "Flashing type", False),
        ("CT_Material", "Material", False),
        ("CT_Thickness", "Thickness", False),
        ("CT_PanelLength", "Length", False),
        ("#count", "Count", False),
        ("CT_DevelopedArea", "Area (sheet, unfolded)", True),
        ("CT_Weight", "Weight (kg)", True),
    ],
    "itemized_extra": [("CT_PanelId", "Door/window, side", False)],
    "group_by": None,
}


def find(doc, name):
    for v in FilteredElementCollector(doc).OfClass(ViewSchedule):
        if v.Name == name:
            return v
    return None


def _unique_name(doc, name):
    if find(doc, name) is None:
        return name
    i = 2
    while find(doc, u"{} ({})".format(name, i)) is not None:
        i += 1
    return u"{} ({})".format(name, i)


def _totals(field):
    try:
        from Autodesk.Revit.DB import ScheduleFieldDisplayType
        field.DisplayType = ScheduleFieldDisplayType.Totals
    except Exception:
        try:
            field.HasTotals = True        # Revit 2021 and older
        except Exception:
            pass


def create(doc, spec, itemized=False):
    """Create the schedule (call inside a transaction). Returns (view, missing params)."""
    name = spec["name"] + (u" (every piece)" if itemized else u"")
    vs = ViewSchedule.CreateSchedule(doc, ElementId(BuiltInCategory.OST_GenericModel))
    vs.Name = _unique_name(doc, name)
    d = vs.Definition

    available, count_field = {}, None
    for sf in d.GetSchedulableFields():
        if sf.FieldType == ScheduleFieldType.Count:
            count_field = sf
        elif sf.FieldType == ScheduleFieldType.Instance:
            available[sf.GetName(doc)] = sf

    fields = list(spec["fields"])
    if itemized:
        fields = spec["itemized_extra"] + [f for f in fields if f[0] != "#count"]
    added, missing = {}, []
    for pname, heading, total in fields:
        sf = count_field if pname == "#count" else available.get(pname)
        if sf is None:
            missing.append(pname)
            continue
        f = d.AddField(sf)
        f.ColumnHeading = heading
        if total:
            _totals(f)
        added[pname] = f

    # filter (the filter field must be in the schedule; hide it if not shown)
    fname, ftype, fvalue = spec["filter"]
    ffield = added.get(fname)
    if ffield is None and fname in available:
        ffield = d.AddField(available[fname])
        ffield.IsHidden = True
    if ffield is not None:
        d.AddFilter(ScheduleFilter(ffield.FieldId, ftype, fvalue))

    # sorting / grouping: identical sizes end up on one row with a count
    first = True
    for pname, _, total in fields:
        if pname == "#count" or total or pname not in added:
            continue
        sg = ScheduleSortGroupField(added[pname].FieldId)
        if first and spec.get("group_by") == pname:
            sg.ShowHeader = True
            sg.ShowFooter = True
            sg.ShowBlankLine = True
        first = False
        d.AddSortGroupField(sg)

    d.IsItemized = bool(itemized)
    d.ShowGrandTotal = True
    try:
        d.ShowGrandTotalCount = True
        d.ShowGrandTotalTitle = True
        d.GrandTotalTitle = "Total"
    except Exception:
        pass
    return vs, missing
