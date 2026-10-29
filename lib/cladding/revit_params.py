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


def _bound_names(doc):
    names = set()
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        names.add(it.Key.Name)
    return names


def ensure_parameters(doc):
    """Create and bind any missing CT_ parameters. Call inside a transaction.

    Returns the names that were added.
    """
    have = _bound_names(doc)
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
            if _insert(doc, definition, binding):
                added.append(name)
    finally:
        try:
            app.SharedParametersFilename = original or ""
        except Exception:
            pass
    return added


def _insert(doc, definition, binding):
    try:
        from Autodesk.Revit.DB import GroupTypeId
        return doc.ParameterBindings.Insert(definition, binding, GroupTypeId.Data)
    except Exception:
        return doc.ParameterBindings.Insert(definition, binding)


def _kinds():
    return dict((d[0], d[1]) for d in inputs.ALL)


def set_values(el, values):
    """values: {name: mm / m2 / number / text}. Silently skips missing params."""
    kinds = _kinds()
    for name, v in values.items():
        p = el.LookupParameter(name)
        if p is None or p.IsReadOnly or v is None:
            continue
        kind = kinds.get(name, "text")
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


def get_inputs(el):
    """Current CT_ input values on an element (mm / text). None when unset."""
    kinds = _kinds()
    out = {}
    for name, kind, _, _ in inputs.INPUTS:
        p = el.LookupParameter(name)
        if p is None or not p.HasValue:
            out[name] = None
            continue
        if p.StorageType == StorageType.String:
            out[name] = p.AsString()
        elif kind == "length":
            out[name] = p.AsDouble() / MM
        else:
            out[name] = p.AsDouble()
    return out


def get_outputs(el):
    """Current calculated CT_ values (mm / m2 / number / text). None when unset."""
    out = {}
    for name, kind, _, _ in inputs.OUTPUTS:
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

