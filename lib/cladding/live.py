# -*- coding: utf-8 -*-
"""Apply CT_ edits made in Properties, live or from the Regenerate button.

Live: a Revit Dynamic Model Updater (registered by startup.py) watches
CladdingTools panels and flashings. When a CT_ value differs from what the
cladding/flashing says it should be, the edit is applied and the elements
are updated in place (tags stay) inside the same transaction, so one Ctrl+Z
undoes the edit and the update together.

The Regenerate button calls the same code, so edits also work if the live
updater isn't running. status() tells which is the case.
"""
import os
import tempfile
import traceback

import clr
clr.AddReference("RevitAPI")
from System import Guid
from Autodesk.Revit.DB import (
    IUpdater, UpdaterId, UpdaterRegistry, ChangePriority, ElementClassFilter,
    Element, DirectShape, FailureDefinition, FailureDefinitionId,
    FailureSeverity, FailureMessage,
)

from cladding import profiles, inputs, groupdata, revit_build, revit_params, revit_flashing

UPDATER_GUID = Guid("0d4b7d36-5c8e-4f0b-a7a3-8f1e2c6b9d42")
BAD_VALUE_ID = FailureDefinitionId(Guid("5e0a2c6f-1b7d-4c3e-9a8f-6d2b4e1c7a90"))
NO_MATERIAL_ID = FailureDefinitionId(Guid("9c3e6a1d-7f2b-4e8a-b5d1-3a6f9e2c4b17"))
NO_HOST_ID = FailureDefinitionId(Guid("2a7f4c9e-3d1b-4a6e-8c5f-7b9d1e3a6c28"))
STATUS_FILE = os.path.join(tempfile.gettempdir(), "CladdingTools_live_status.txt")


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------
def write_status(text):
    try:
        with open(STATUS_FILE, "w") as f:
            f.write(text)
    except Exception:
        pass


def last_execute_error():
    path = STATUS_FILE + ".error"
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return u""


def status(addin_id):
    """(running: bool, explanation) for the live updater in this session."""
    try:
        uid = UpdaterId(addin_id, UPDATER_GUID)
        registered = UpdaterRegistry.IsUpdaterRegistered(uid)
        enabled = registered and UpdaterRegistry.IsUpdaterEnabled(uid)
    except Exception as ex:
        return False, u"couldn't check ({})".format(ex)
    startup = u""
    try:
        with open(STATUS_FILE) as f:
            startup = f.read().strip()
    except Exception:
        pass
    if enabled:
        err = last_execute_error()
        return True, u"on" + (u" (last error: {})".format(err) if err else u"")
    if registered:
        return False, (u"registered but disabled by Revit, usually after an error. "
                       u"Restart Revit. " + last_execute_error())
    return False, u"not registered. Startup said: " + (startup or u"nothing (startup.py didn't run)")


# --------------------------------------------------------------------------
# Revit warnings
# --------------------------------------------------------------------------
def _define_failures():
    """Only allowed while Revit starts; harmless to try again on Reload."""
    for fid, text in (
        (BAD_VALUE_ID, "CladdingTools: a CT_ value couldn't be used (unknown profile or "
                       "flashing type, orientation not Vertical/Horizontal, or a size too "
                       "small). It was put back to the previous value."),
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


# --------------------------------------------------------------------------
# Cladding edits
# --------------------------------------------------------------------------
def group_edits(doc, group, els, library):
    """Edits typed on panels `els` of one cladding.

    Returns (new_group or None when nothing changed, changes, error).
    On an error the edited values are put back.
    """
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
        return None, [], None
    try:
        count = len(revit_build.group_panels(doc, group["group"]))
        new_group, changes = groupdata.apply_edits(group, edits, count, library)
        return new_group, changes, None
    except ValueError as ex:
        for el in els:
            exp = expected.get(el.Id)
            if exp:
                revit_params.set_values(el, exp)
                t = revit_build.panel_type(doc, exp["CT_Profile"], exp["CT_Material"])
                if el.GetTypeId() != t.Id:
                    el.SetTypeId(t.Id)
        return None, [], u"{}".format(ex)


def split(doc, element_ids):
    """{group id: [panels]}, [loose panels] and [flashing pieces] among the ids."""
    by_group, loose, flashings = {}, [], []
    for eid in element_ids:
        el = doc.GetElement(eid)
        if not isinstance(el, DirectShape):
            continue
        if el.ApplicationId == revit_build.APP_ID:
            if el.ApplicationDataId.endswith(revit_build.COPY_SUFFIX):
                loose.append(el)
            else:
                by_group.setdefault(el.ApplicationDataId, []).append(el)
        elif el.ApplicationId == revit_flashing.FLASH_APP:
            flashings.append(el)
    return by_group, loose, flashings


def loose_edits(doc, els, library):
    """Apply CT_ edits on copied (loose) panels. Returns (lines, errors)."""
    lines, errors = [], []
    for el in els:
        try:
            data = revit_build._read(el)
        except Exception:
            data = None
        if not data or data.get("kind") != "loose":
            continue
        exp = revit_build.loose_expected(data)
        copied = data.get("uid") != el.UniqueId          # a copy of a copy
        cur = revit_params.get_values(el)
        edits = dict((n, cur[n]) for n in inputs.PANEL_NAMES
                     if inputs.differs(n, cur.get(n), exp.get(n)))
        if not edits:
            t = revit_build.read_type(doc, el)
            if t and groupdata.type_name(t.get("profile"), t.get("material")) != \
                    groupdata.type_name(exp["CT_Profile"], exp["CT_Material"]):
                edits = groupdata.type_edits(t, exp)
        if not edits and not copied:
            continue
        if not data.get("frame"):
            revit_params.set_values(el, exp)
            errors.append(u"{}: this copy was made with an older version and can't be "
                          u"edited; delete it and copy again".format(el.Id))
            continue
        try:
            r = data["recipe"]
            if "CT_Profile" in edits:
                r["profile"] = groupdata._find_profile(edits["CT_Profile"],
                                                       {"profiles": {}}, library)
            if "CT_Material" in edits:
                r["material"] = inputs.material(edits["CT_Material"])
            if "CT_Standoff" in edits:
                r["standoff"] = inputs.length("CT_Standoff", edits["CT_Standoff"])
            if "CT_PanelWidth" in edits:
                data["panel"]["width"] = inputs.length("CT_PanelWidth", edits["CT_PanelWidth"])
            if "CT_PanelLength" in edits:
                data["panel"]["length"] = inputs.length("CT_PanelLength", edits["CT_PanelLength"])
        except ValueError as ex:
            revit_params.set_values(el, exp)
            errors.append(u"copied panel {}: {}".format(el.Id, ex))
            continue
        revit_build.rebuild_loose(doc, el, data)
        if edits:
            lines.append(u"copied panel {}: {}".format(el.Id, u", ".join(sorted(edits))))
    return lines, errors


def run(doc, element_ids, warn=True):
    """Apply every CT_ edit on the given elements. Returns text lines."""
    by_group, loose, flashings = split(doc, element_ids)
    lines, library = [], None
    for gid, els in by_group.items():
        group = revit_build.load_group(doc, gid)
        if group is None:
            continue                 # made with an older version: Regenerate first
        # copies (Copy / Ctrl+C) become their own panels before anything else
        if revit_build.detach_copies(doc, group):
            moved = [el for el in els
                     if el.ApplicationDataId.endswith(revit_build.COPY_SUFFIX)]
            loose.extend(moved)
            els = [el for el in els if el not in moved]
            lines.append(u"{} copied panel(s) made independent".format(len(moved)))
            if not els:
                continue
        if library is None:
            library = profiles.load_library()
        new_group, changes, error = group_edits(doc, group, els, library)
        if error:
            lines.append(u"**Not applied:** " + error)
            if warn:
                _warn(doc, BAD_VALUE_ID, els[0].Id)
            continue
        if new_group is None:
            continue
        try:
            faces = revit_build.resolve_faces(doc, group["faces"])
        except Exception:
            lines.append(u"**Not applied:** the wall/roof face is gone")
            if warn:
                _warn(doc, NO_HOST_ID, els[0].Id)
            continue
        summary = revit_build.build(doc, faces, new_group, ensure_params=False)
        if any(u"standoff" in c or u"profile" in c for c in changes):
            revit_build.rebuild_neighbors(doc, new_group)   # corner laps follow
        lines.extend(changes)
        if summary["missing_materials"]:
            lines.append(u"No material called {} in this project".format(
                u", ".join(summary["missing_materials"])))
            if warn:
                panels = revit_build.group_panels(doc, gid)
                if panels:
                    _warn(doc, NO_MATERIAL_ID, panels[0].Id)
    if loose:
        if library is None:
            library = profiles.load_library()
        changes, errors = loose_edits(doc, loose, library)
        lines.extend(changes)
        for e in errors:
            lines.append(u"**Not applied:** " + e)
        if errors and warn:
            _warn(doc, BAD_VALUE_ID, loose[0].Id)
    if flashings:
        changes, errors = revit_flashing.apply_edits(doc, flashings)
        lines.extend(changes)
        for e in errors:
            lines.append(u"**Not applied:** " + e)
        if errors and warn:
            _warn(doc, BAD_VALUE_ID, flashings[0].Id)
    return lines


# --------------------------------------------------------------------------
# The updater
# --------------------------------------------------------------------------
class CladdingUpdater(IUpdater):
    def __init__(self, addin_id):
        self._id = UpdaterId(addin_id, UPDATER_GUID)

    def GetUpdaterId(self):
        return self._id

    def GetUpdaterName(self):
        return "CladdingTools live update"

    def GetAdditionalInformation(self):
        return "Updates CladdingTools panels and flashings when their CT_ parameters change."

    def GetChangePriority(self):
        return ChangePriority.FloorsRoofsStructuralWalls

    def Execute(self, data):
        try:
            ids = list(data.GetModifiedElementIds()) + list(data.GetAddedElementIds())
            run(data.GetDocument(), ids)
        except Exception:
            # Never let an exception escape: Revit would disable the updater.
            try:
                with open(STATUS_FILE + ".error", "w") as f:
                    f.write(traceback.format_exc()[-800:])
            except Exception:
                pass


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
    UpdaterRegistry.AddTrigger(uid, ElementClassFilter(DirectShape),
                               Element.GetChangeTypeElementAddition())
    return updater
