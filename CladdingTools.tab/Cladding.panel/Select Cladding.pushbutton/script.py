# -*- coding: utf-8 -*-
"""Select every panel of the cladding(s) you have selected a panel of.

(Right-click > Select All Instances selects panels with the same profile and
material instead, across all claddings.)
"""
__title__ = "Select\nCladding"
__doc__ = "Select all panels of the cladding the selected panel belongs to."

from pyrevit import revit, forms
from Autodesk.Revit.DB import DirectShape, ElementId
from System.Collections.Generic import List

from cladding import revit_build

doc = revit.doc
groups = set(el.ApplicationDataId for el in revit.get_selection()
             if isinstance(el, DirectShape) and el.ApplicationId == revit_build.APP_ID)
if not groups:
    forms.alert("Select a cladding panel first.", exitscript=True)

ids = List[ElementId]()
for g in groups:
    for ds in revit_build.group_panels(doc, g):
        ids.Add(ds.Id)
revit.uidoc.Selection.SetElementIds(ids)
