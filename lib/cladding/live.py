# -*- coding: utf-8 -*-
"""Live update: change a CT_ input in Properties and the cladding rebuilds.

A Revit Dynamic Model Updater watches CladdingTools panels. When a CT_
value on a panel differs from what the cladding says it should be, the edit
is recorded (per panel, or for the whole cladding) and the cladding is
updated in place (tags stay) inside
the same transaction, so one Ctrl+Z undoes the edit and the rebuild together.

Registered from the extension's startup.py. Invalid values are put back to
the previous value and Revit shows a warning.
"""
import clr
clr.AddReference("RevitAPI")
from System import Guid
from Autodesk.Revit.DB import (
    IUpdater, UpdaterId, UpdaterRegistry, ChangePriority, ElementClassFilter,
    Element, DirectShape, FailureDefinition, FailureDefinitionId,
    FailureSeverity, FailureMessage, ElementId,
)

from cladding import profiles, inputs, groupdata, revit_build, revit_params

UPDATER_GUID = Guid("0d4b7d36-5c8e-4f0b-a7a3-8f1e2c6b9d42")
BAD_VALUE_ID = FailureDefinitionId(Guid("5e0a2c6f-1b7d-4c3e-9a8f-6d2b4e1c7a90"))
NO_MATERIAL_ID = FailureDefinitionId(Guid("9c3e6a1d-7f2b-4e8a-b5d1-3a6f9e2c4b17"))
NO_HOST_ID = FailureDefinitionId(Guid("2a7f4c9e-3d1b-4a6e-8c5f-7b9d1e3a6c28"))


def _define_failures():
    """Only allowed while Revit starts; harmless to try again on Reload."""
    for fid, text in (
        (BAD_VALUE_ID, "CladdingTools: a CT_ value couldn't be used (unknown profile, "
                       "orientation not Vertical/Horizontal, or a size too small). "
                       "It was put back to the previous value."),
        (NO_MATERIAL_ID, "CladdingTools: no material in this project matches CT_Material. "
                         "Those panels were built without a material."),
        (NO_HOST_ID, "CladdingTools: the wall/roof face this cladding was applied to "
                     "no longer exists, so it can't be rebuilt. Apply the cladding again."),
    ):
        try:
            FailureDefinition.CreateFailureDefinition(fid, FailureSeverity.Warning, text)
        except Exception:
            pass


def _warn(doc, fid, element_id):
    try:
        msg = FailureMessage(fid)
        msg.SetFailingElement(element_id)
        doc.PostFailure(msg)
    except Exception:
        pass   # definition missing after a Reload: skip the message, keep working


class CladdingUpdater(IUpdater):
    def __init__(self, addin_id):
        self._id = UpdaterId(addin_id, UPDATER_GUID)

    def GetUpdaterId(self):
        return self._id

    def GetUpdaterName(self):
        return "CladdingTools live update"

    def GetAdditionalInformation(self):
        return "Rebuilds CladdingTools panels when their CT_ parameters change."

    def GetChangePriority(self):
        return ChangePriority.FloorsRoofsStructuralWalls

    def Execute(self, data):
        try:
            run(data.GetDocument(), data.GetModifiedElementIds())
        except Exception as ex:
            # Never let an exception escape: Revit would disable the updater.
            print(u"CladdingTools live update failed: {}".format(ex))


def run(doc, element_ids):
    """Find CT_ edits on the given panels and rebuild the claddings they belong to."""
    by_group = {}
    for eid in element_ids:
        el = doc.GetElement(eid)
        if isinstance(el, DirectShape) and el.ApplicationId == revit_build.APP_ID:
            by_group.setdefault(el.ApplicationDataId, []).append(el)

    library = None
    for gid, els in by_group.items():
        group = revit_build.load_group(doc, gid)
        if group is None:
            continue                 # made with an older version: Regenerate first
        edits, expected = {}, {}
        for el in els:
            pd = revit_build.read_panel(el)
            if not pd or "key" not in pd:
                continue
            exp = groupdata.expected_for(group, pd)
            expected[el.Id] = exp
            e, outputs_edited = groupdata.find_edits(revit_params.get_values(el), exp)
            if not e:   # picked another type in the Type Selector?
                t = revit_build.read_type(doc, el)
                if t and groupdata.type_name(t.get("profile"), t.get("material")) != \
                        groupdata.type_name(exp["CT_Profile"], exp["CT_Material"]):
                    e = groupdata.type_edits(t, exp)
            if e:
                edits[pd["key"]] = e
            elif outputs_edited:     # area/weight/id typed over: put them back
                revit_params.set_values(el, dict((n, exp[n]) for n in inputs.OUTPUT_NAMES))
        if not edits:
            continue                 # moved, or our own writes: nothing to do

        def put_back():
            for el in els:
                if el.Id in expected:
                    exp = expected[el.Id]
                    revit_params.set_values(el, exp)
                    t = revit_build.panel_type(doc, exp["CT_Profile"], exp["CT_Material"])
                    if el.GetTypeId() != t.Id:
                        el.SetTypeId(t.Id)

        if library is None:
            library = profiles.load_library()
        try:
            count = len(revit_build.group_panels(doc, gid))
            new_group, _ = groupdata.apply_edits(group, edits, count, library)
        except ValueError:
            put_back()
            _warn(doc, BAD_VALUE_ID, els[0].Id)
            continue
        try:
            faces = revit_build.resolve_faces(doc, group["faces"])
        except Exception:
            put_back()
            _warn(doc, NO_HOST_ID, els[0].Id)
            continue

        summary = revit_build.build(doc, faces, new_group, ensure_params=False)
        if summary["missing_materials"]:
            panels = revit_build.group_panels(doc, gid)
            if panels:
                _warn(doc, NO_MATERIAL_ID, panels[0].Id)


def register(addin_id):
    """Register (or re-register after a pyRevit Reload) the updater."""
    _define_failures()
    updater = CladdingUpdater(addin_id)
    uid = updater.GetUpdaterId()
    if UpdaterRegistry.IsUpdaterRegistered(uid):
        UpdaterRegistry.UnregisterUpdater(uid)
    UpdaterRegistry.RegisterUpdater(updater, True)   # optional: no warning without pyRevit
    UpdaterRegistry.AddTrigger(uid, ElementClassFilter(DirectShape),
                               Element.GetChangeTypeAny())
    return updater
