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
)

from cladding import inputs

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
    }[kind]


def _bindings(doc):
    """{name: InternalDefinition} for every parameter bound in the project."""
    out = {}
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        out[it.Key.Name] = it.Key
    return out


def _group_for(name):
    """Per-panel values go under Dimensions, everything else under Data."""
    from Autodesk.Revit.DB import GroupTypeId
    return GroupTypeId.Geometry if name in inputs.PANEL_NAMES else GroupTypeId.Data


def ensure_parameters(doc):
    """Create and bind any missing CT_ parameters, and put each one in the
    right Properties group. Call inside a transaction.

    Returns the names that were added.
    """
    have = _bindings(doc)
    _regroup(have)
    missing = [d for d in inputs.ALL if d[0] not in have]
    if not missing:
        return []

    app = doc.Application
    original = app.SharedParametersFilename
    path = os.path.join(tempfile.gettempdir(), "CladdingTools_SharedParameters.txt")
    if not os.path.exists(path):
        open(path, "w").close()      # Revit fills in the header

    added = []
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
            if definition is None:
                opts = ExternalDefinitionCreationOptions(name, _spec(kind))
                opts.GUID = Guid(guid)
                opts.Description = desc
                definition = group.Definitions.Create(opts)
            if _insert(doc, definition, binding, name):
                added.append(name)
    finally:
        try:
            app.SharedParametersFilename = original or ""
        except Exception:
            pass
    return added


def _regroup(bound):
    """Move CT_ parameters made by an earlier version to the right group."""
    for name in inputs.KINDS:
        definition = bound.get(name)
        if definition is None:
            continue
        try:
            target = _group_for(name)
            if definition.GetGroupTypeId() != target:
                definition.SetGroupTypeId(target)
        except Exception:
            pass   # older Revit without ForgeTypeId groups: leave as is


def _insert(doc, definition, binding, name):
    try:
        return doc.ParameterBindings.Insert(definition, binding, _group_for(name))
    except Exception:
        return doc.ParameterBindings.Insert(definition, binding)


def set_values(el, values):
    """values: {name: mm / m2 / number / text}. Silently skips missing params."""
    for name, v in values.items():
        p = el.LookupParameter(name)
        if p is None or p.IsReadOnly or v is None:
            continue
        kind = inputs.KINDS.get(name, "text")
        try:
            if p.StorageType == StorageType.String:
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
        elif p.StorageType == StorageType.String:
            out[name] = p.AsString()
        elif kind == "length":
            out[name] = p.AsDouble() / MM
        elif kind == "area":
            out[name] = p.AsDouble() / SQFT_PER_M2
        else:
            out[name] = p.AsDouble()
    return out
