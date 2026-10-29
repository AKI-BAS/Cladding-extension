# -*- coding: utf-8 -*-
"""Live update: change a CT_ input in Properties and the cladding rebuilds.

A Revit Dynamic Model Updater watches CladdingTools panels. When a CT_
input (profile, cover width, max length, joint, standoff, orientation,
material) differs from the stored recipe, or CT_PanelWidth / CT_PanelLength
is typed over, the whole group is rebuilt inside
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

from cladding import profiles, inputs, revit_build, revit_params

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
                         "The cladding was rebuilt without a material."),
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
            self._run(data)
        except Exception as ex:
            # Never let an exception escape: Revit would disable the updater.
            print(u"CladdingTools live update failed: {}".format(ex))

    def _run(self, data):
        doc = data.GetDocument()
        done = set()
        library = None
        for eid in data.GetModifiedElementIds():
            el = doc.GetElement(eid)
            if not isinstance(el, DirectShape) or el.ApplicationId != revit_build.APP_ID:
                continue
            group = el.ApplicationDataId
            if group in done:
                continue
            stored = revit_build.read_stored(el)
            if not stored:
                continue
            values, outputs_edited = inputs.requested_values(
                revit_params.get_inputs(el), revit_params.get_outputs(el), stored)
            if library is None:
                library = profiles.load_library()
            try:
                recipe, changes = inputs.apply_inputs(stored["recipe"], values, library)
            except ValueError:
                revit_params.set_values(el, inputs.recipe_inputs(stored["recipe"]))
                _warn(doc, BAD_VALUE_ID, eid)
                done.add(group)
                continue
            if not changes:
                if outputs_edited:  # area/weight/group typed over: put them back
                    revit_params.set_values(el, inputs.stored_outputs(stored))
                continue            # moved, or our own writes: nothing to do
            done.add(group)

            try:
                faces = revit_build.resolve_faces(doc, stored["faces"])
            except Exception:
                revit_params.set_values(el, inputs.recipe_inputs(stored["recipe"]))
                _warn(doc, NO_HOST_ID, eid)
                continue

            if recipe.get("material") and \
                    revit_build.find_material(doc, recipe["material"]) == ElementId.InvalidElementId:
                recipe["material"] = ""
                warn_material = True
            else:
                warn_material = False

            revit_build.delete_group(doc, group)
            revit_build.build(doc, faces, recipe, group)
            if warn_material:
                panels = revit_build.group_panels(doc, group)
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
