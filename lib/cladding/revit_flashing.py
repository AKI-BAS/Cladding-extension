# -*- coding: utf-8 -*-
"""Build and remove flashings around doors and windows in Revit.

Each side (head, jambs, sill) is its own Generic Model, so a single side can
be deleted with Delete. Flashings are tagged with the door/window they
belong to, so adding again replaces them and Remove finds them.
"""
import clr
clr.AddReference("RevitAPI")
from System.Collections.Generic import List
from Autodesk.Revit.DB import (
    XYZ, Wall, HostObjectUtils, ShellLayerType, PlanarFace, CurveLoop, Line,
    GeometryCreationUtilities, SolidOptions, DirectShape, DirectShapeType,
    ElementId, BuiltInCategory, GeometryObject, FilteredElementCollector,
    Reference,
)

from cladding import flashing, profiles, groupdata, revit_build, revit_params

MM = revit_build.MM
FLASH_APP = "CladdingTools.Flashing"
DEFAULT_SURFACE = 50.0     # mm, used when the wall has no CladdingTools cladding


def is_opening(el):
    try:
        cid = el.Category.Id
    except Exception:
        return False
    return cid in (ElementId(BuiltInCategory.OST_Doors), ElementId(BuiltInCategory.OST_Windows))


def opening_kind(el):
    return "door" if el.Category.Id == ElementId(BuiltInCategory.OST_Doors) else "window"


def exterior_face(wall):
    for ref in HostObjectUtils.GetSideFaces(wall, ShellLayerType.Exterior):
        face = wall.GetGeometryObjectFromReference(ref)
        if isinstance(face, PlanarFace):
            return ref, face
    raise ValueError("the wall's outside face isn't flat")


def opening_rect(wall, face, frame, inst):
    """The opening as (u0, u1, v0, v1) mm in the face frame.

    Uses the face edges that the door/window cut into the wall, so it is the
    real hole, not the family's frame or bounding box.
    """
    us, vs = [], []
    for loop in face.EdgeLoops:
        for edge in loop:
            cut_by_inst = False
            for i in (0, 1):
                try:
                    f = edge.GetFace(i)
                    ids = wall.GetGeneratingElementIds(f) if f is not None else []
                except Exception:
                    ids = []
                if any(x == inst.Id for x in ids):
                    cut_by_inst = True
                    break
            if cut_by_inst:
                for p in edge.Tessellate():
                    d = p - frame.origin
                    us.append(d.DotProduct(frame.right) / MM)
                    vs.append(d.DotProduct(frame.up) / MM)
    if not us:   # fallback: the family's bounding box projected on the face
        bb = inst.get_BoundingBox(None)
        if bb is None:
            raise ValueError("can't find the opening in the wall")
        for x in (bb.Min.X, bb.Max.X):
            for y in (bb.Min.Y, bb.Max.Y):
                for z in (bb.Min.Z, bb.Max.Z):
                    d = XYZ(x, y, z) - frame.origin
                    us.append(d.DotProduct(frame.right) / MM)
                    vs.append(d.DotProduct(frame.up) / MM)
    return (min(us), max(us), min(vs), max(vs))


def cladding_on_wall(doc, wall):
    """(surface mm, material name) of CladdingTools cladding on this wall."""
    for _, group in revit_build._group_storages(doc):
        for s in group.get("faces", []):
            try:
                ref = Reference.ParseFromStableRepresentation(doc, s)
            except Exception:
                continue
            if ref.ElementId == wall.Id:
                r = group["recipe"]
                return flashing.cladding_surface(r), r.get("material") or u""
    return DEFAULT_SURFACE, u""


def flashing_type(doc, type_name, material, cache):
    name = u"".join(u"-" if c in groupdata._BAD_NAME_CHARS else c
                    for c in u"Flashing - {} - {}".format(type_name, material or u"No material"))
    if name in cache:
        return cache[name]
    found = None
    for t in FilteredElementCollector(doc).OfClass(DirectShapeType):
        if revit_build.element_name(t) == name:
            found = t
            break
    if found is None:
        found = DirectShapeType.Create(doc, name, ElementId(BuiltInCategory.OST_GenericModel))
    cache[name] = found
    return found


def existing(doc, inst=None):
    return [ds for ds in FilteredElementCollector(doc).OfClass(DirectShape)
            if ds.ApplicationId == FLASH_APP and
            (inst is None or ds.ApplicationDataId == inst.UniqueId)]


def remove(doc, inst):
    ids = List[ElementId]()
    for ds in existing(doc, inst):
        ids.Add(ds.Id)
    if ids.Count:
        doc.Delete(ids)
    return ids.Count


def add(doc, inst, ftype, material, include_sill, type_cache):
    """Add (or replace) flashings on one door/window. Call inside a transaction.

    material: a material name, or None to use the cladding's material.
    Returns (sides added, material used).
    """
    wall = inst.Host
    if not isinstance(wall, Wall):
        raise ValueError("it isn't hosted in a wall")
    _, face = exterior_face(wall)
    frame = revit_build.FaceFrame(face)
    rect = opening_rect(wall, face, frame, inst)
    surface, clad_mat = cladding_on_wall(doc, wall)
    if material is None:
        material = clad_mat
    mat_id = revit_build.find_material(doc, material)
    kind = opening_kind(inst)

    remove(doc, inst)
    dtype = flashing_type(doc, ftype["name"], material, type_cache)
    sections = flashing.loops(ftype, surface)
    sides = flashing.sides_for(kind, rect, frame.extents, include_sill)
    label = u"{} {}".format("Door" if kind == "door" else "Window", inst.Id)

    for side in sides:
        place = flashing.placements(rect, side, ftype)
        shapes = List[GeometryObject]()
        for part, loop2d in sections.items():
            su, sv, ru, rv, ou, ov, length = place[part]
            if length <= 1.0:
                continue
            pts = revit_build._drop_short(loop2d, revit_build.MIN_EDGE, closed=True)
            pts3 = [frame.point(su + ou * x, sv + ov * x, z) for x, z in pts]
            loop = CurveLoop()
            for a, b in zip(pts3, pts3[1:] + pts3[:1]):
                loop.Append(Line.CreateBound(a, b))
            loops = List[CurveLoop]()
            loops.Add(loop)
            direction = frame.right.Multiply(ru).Add(frame.up.Multiply(rv))
            shapes.Add(GeometryCreationUtilities.CreateExtrusionGeometry(
                loops, direction, length * MM,
                SolidOptions(mat_id, ElementId.InvalidElementId)))
        if shapes.Count == 0:
            continue
        ds = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_GenericModel))
        ds.ApplicationId = FLASH_APP
        ds.ApplicationDataId = inst.UniqueId
        ds.SetTypeId(dtype.Id)
        ds.SetShape(shapes)
        q = flashing.quantities(ftype, surface, rect, side)
        revit_params.set_values(ds, {
            "CT_Group": "Flashing",
            "CT_PanelId": u"{} {}".format(label, side),
            "CT_Profile": ftype["name"],
            "CT_Material": material or u"",
            "CT_PanelLength": q["length_mm"],
            "CT_DevelopedArea": q["developed_area_m2"],
            "CT_Weight": q["weight_kg"],
        })
        revit_build._store(ds, profiles.dumps({
            "kind": "flashing", "opening": inst.UniqueId, "side": side,
            "type": ftype["name"], "material": material or u""}))
    return sides, material
