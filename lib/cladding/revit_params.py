# -*- coding: utf-8 -*-
"""Create the CT_ shared parameters on Generic Models and read/write them.

On first use in a project the parameters are created from a temporary
shared parameter file with fixed GUIDs, so schedules and filters made in
one project work in the next. The user's own shared parameter file setting
is restored afterwards.
"""
import os
import tempfile

import clr
clr.AddReference("RevitAPI")
from System import Guid
from Autodesk.Revit.DB import (
    BuiltInCategory, Category, ExternalDefinitionCreationOptions, StorageType,
    FilteredElementCollector,
)

from cladding import inputs, profiles

MM = 1.0 / 304.8          # mm -> feet
SQFT_PER_M2 = 10.7639104
GROUP_NAME = "CladdingTools"


def _spec(kind):
    from Autodesk.Revit.DB import SpecTypeId
    return {
        "text": SpecTypeId.String.Text,
        "length": SpecTypeId.Length,
        "area": SpecTypeId.Area,
        "number": SpecTypeId.Number,
        "material": SpecTypeId.Reference.Material,
    }[kind]


def _bindings(doc):
    """{name: InternalDefinition} for every parameter bound in the project."""
    out = {}
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        out[it.Key.Name] = it.Key
    return out


def _group_for(name):
    """Material under Materials and Finishes, other per-panel values under
    Dimensions, everything else under Data."""
    from Autodesk.Revit.DB import GroupTypeId
    if inputs.KINDS.get(name) == "material":
        return GroupTypeId.Materials
    return GroupTypeId.Geometry if name in inputs.PANEL_NAMES else GroupTypeId.Data


def _spec_key(forge_id):
    """'autodesk.spec.aec:length-2.0.0' -> 'autodesk.spec.aec:length'."""
    try:
        return forge_id.TypeId.split("-")[0]
    except Exception:
        return None


def _wrong_type(definition, kind):
    try:
        have = _spec_key(definition.GetDataType())
    except Exception:
        return False                       # can't tell: leave it alone
    return have is not None and have != _spec_key(_spec(kind))


def _delete_parameter(doc, name):
    from Autodesk.Revit.DB import ParameterElement
    for pe in FilteredElementCollector(doc).OfClass(ParameterElement):
        try:
            if pe.GetDefinition().Name == name:
                doc.Delete(pe.Id)
        except Exception:
            pass


def ensure_parameters(doc):
    """Make sure every CT_ parameter exists on Generic Models with the right
    type (Length, Area, Text...) and in the right Properties group.
    Call inside a transaction.

    A CT_ parameter of the wrong type (for example one made by hand as Text)
    is replaced. Returns {"added": [...], "replaced": [...], "regrouped": [...],
    "problems": [...]}.
    """
    report = {"added": [], "replaced": [], "regrouped": [], "problems": []}
    have = _bindings(doc)
    for name, kind, _, _ in inputs.ALL:
        d = have.get(name)
        if d is not None and _wrong_type(d, kind):
            _delete_parameter(doc, name)
            report["replaced"].append(name)
    if report["replaced"]:
        have = _bindings(doc)
    report["regrouped"], problems = _regroup(doc, have)
    report["problems"].extend(problems)

    missing = [d for d in inputs.ALL if d[0] not in have]
    if not missing:
        return report

    app = doc.Application
    original = app.SharedParametersFilename
    path = os.path.join(tempfile.gettempdir(), "CladdingTools_SharedParameters_v2.txt")
    if not os.path.exists(path):
        open(path, "w").close()      # Revit fills in the header

    try:
        app.SharedParametersFilename = path
        sp_file = app.OpenSharedParameterFile()
        if sp_file is None:
            raise RuntimeError("Couldn't open a shared parameter file at " + path)
        group = sp_file.Groups.get_Item(GROUP_NAME) or sp_file.Groups.Create(GROUP_NAME)

        cats = app.Create.NewCategorySet()
        cats.Insert(Category.GetCategory(doc, BuiltInCategory.OST_GenericModel))
        binding = app.Create.NewInstanceBinding(cats)

        for name, kind, guid, desc in missing:
            definition = group.Definitions.get_Item(name)
            if definition is not None and _wrong_type(definition, kind):
                report["problems"].append(
                    u"{}: the temporary parameter file has it with another type; "
                    u"delete {} and run again".format(name, path))
                continue
            if definition is None:
                opts = ExternalDefinitionCreationOptions(name, _spec(kind))
                opts.GUID = Guid(guid)
                opts.Description = desc
                definition = group.Definitions.Create(opts)
            if _insert(doc, definition, binding, name):
                report["added"].append(name)
            else:
                report["problems"].append(u"{}: Revit refused to add it".format(name))
    finally:
        try:
            app.SharedParametersFilename = original or ""
        except Exception:
            pass
    return report


def _same_group(definition, target):
    try:
        return definition.GetGroupTypeId().TypeId == target.TypeId
    except Exception:
        return False


def _regroup(doc, bound):
    """Put each CT_ parameter in its Properties group (Dimensions or Data)."""
    moved, problems = [], []
    for name in inputs.KINDS:
        d = bound.get(name)
        if d is None:
            continue
        target = _group_for(name)
        if _same_group(d, target):
            continue
        try:
            d.SetGroupTypeId(target)
        except Exception:
            pass
        if not _same_group(d, target):     # second way: re-insert the binding
            try:
                doc.ParameterBindings.ReInsert(d, doc.ParameterBindings.get_Item(d), target)
            except Exception as ex:
                problems.append(u"{}: couldn't move it to its group ({})".format(name, ex))
                continue
        if _same_group(d, target):
            moved.append(name)
    return moved, problems


def _insert(doc, definition, binding, name):
    try:
        return doc.ParameterBindings.Insert(definition, binding, _group_for(name))
    except Exception:
        return doc.ParameterBindings.Insert(definition, binding)


def describe(report):
    """Lines for the output window, empty when nothing happened."""
    lines = []
    if report.get("added"):
        lines.append(u"Parameters added: " + u", ".join(report["added"]))
    if report.get("replaced"):
        lines.append(u"Parameters replaced because they had the wrong type: " +
                     u", ".join(report["replaced"]))
    if report.get("regrouped"):
        lines.append(u"Parameters moved to their group: " + u", ".join(report["regrouped"]))
    for p in report.get("problems", []):
        lines.append(u"**Parameter problem:** " + p)
    return lines


def _material_id(doc, name):
    from Autodesk.Revit.DB import ElementId, Material
    if not name:
        return ElementId.InvalidElementId
    mats = dict((m.Name, m.Id) for m in FilteredElementCollector(doc).OfClass(Material))
    hit = profiles.match_name(name, mats.keys())
    return mats[hit] if hit is not None else ElementId.InvalidElementId


def set_values(el, values):
    """values: {name: mm / m2 / number / text}. Silently skips missing params."""
    for name, v in values.items():
        p = el.LookupParameter(name)
        if p is None or p.IsReadOnly or v is None:
            continue
        kind = inputs.KINDS.get(name, "text")
        try:
            if p.StorageType == StorageType.ElementId:
                p.Set(_material_id(el.Document, v))
            elif p.StorageType == StorageType.String:
                p.Set(u"{}".format(v))
            elif kind == "length":
                p.Set(float(v) * MM)
            elif kind == "area":
                p.Set(float(v) * SQFT_PER_M2)
            else:
                p.Set(float(v))
        except Exception as ex:
            print(u"Couldn't set {}: {}".format(name, ex))


def get_values(el):
    """Every CT_ value on an element (mm / m2 / number / text). None when unset."""
    out = {}
    for name, kind, _, _ in inputs.ALL:
        p = el.LookupParameter(name)
        if p is None or not p.HasValue:
            out[name] = None
        elif p.StorageType == StorageType.ElementId:
            # <By Category> / unknown: no value, so it's never read as an edit
            m = el.Document.GetElement(p.AsElementId())
            out[name] = m.Name if m is not None else None
        elif p.StorageType == StorageType.String:
            out[name] = p.AsString()
        elif kind == "length":
            out[name] = p.AsDouble() / MM
        elif kind == "area":
            out[name] = p.AsDouble() / SQFT_PER_M2
        else:
            out[name] = p.AsDouble()
    return out
