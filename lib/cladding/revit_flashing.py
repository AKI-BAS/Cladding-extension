# -*- coding: utf-8 -*-
"""Build, edit and remove flashings around doors and windows in Revit.

Each side (head, jambs, sill) is its own Generic Model, so a single side can
be deleted with Delete. Each piece stores {opening, side, type, material,
standoff}; editing CT_Profile (flashing type), CT_Material or CT_Standoff on
a piece, or picking another flashing type in the Type Selector, rebuilds
that piece in place (tags stay).
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

from cladding import flashing, profiles, groupdata, inputs, revit_build, revit_params

MM = revit_build.MM
FLASH_APP = "CladdingTools.Flashing"
EDITABLE = ("CT_Profile", "CT_Material", "CT_Standoff")


# --------------------------------------------------------------------------
# Openings and walls
# --------------------------------------------------------------------------
def is_opening(el):
    try:
        cid = el.Category.Id
    except Exception:
        return False
    return cid in (ElementId(BuiltInCategory.OST_Doors), ElementId(BuiltInCategory.OST_Windows))


def opening_kind(el):
    return "door" if el.Category.Id == ElementId(BuiltInCategory.OST_Doors) else "window"


def exterior_face(wall):
    """The wall's exterior face: flat, or curved for walls curved in plan."""
    for ref in HostObjectUtils.GetSideFaces(wall, ShellLayerType.Exterior):
        face = wall.GetGeometryObjectFromReference(ref)
        if face is not None and revit_build.is_supported(face):
            return ref, face
    raise ValueError("the wall's outside face is neither flat nor a curved wall face")


def _uv(frame, p):
    """(u, v) mm of a point in a flat or curved face frame."""
    if getattr(frame, "curved", False):
        return frame.uv(p)
    d = p - frame.origin
    return d.DotProduct(frame.right) / MM, d.DotProduct(frame.up) / MM


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
                    u, v = _uv(frame, p)
                    us.append(u)
                    vs.append(v)
    if not us:   # fallback: the family's bounding box projected on the face
        bb = inst.get_BoundingBox(None)
        if bb is None:
            raise ValueError("can't find the opening in the wall")
        for x in (bb.Min.X, bb.Max.X):
            for y in (bb.Min.Y, bb.Max.Y):
                for z in (bb.Min.Z, bb.Max.Z):
                    u, v = _uv(frame, XYZ(x, y, z))
                    us.append(u)
                    vs.append(v)
    return (min(us), max(us), min(vs), max(vs))


def cladding_on_wall(doc, wall, face):
    """Group recipe of CladdingTools cladding on this side of the wall, or None.
    Cladding on the other side is ignored."""
    curved = not isinstance(face, PlanarFace)
    normal = None if curved else face.FaceNormal.Normalize()
    for _, group in revit_build._group_storages(doc):
        for s in group.get("faces", []):
            try:
                ref = Reference.ParseFromStableRepresentation(doc, s)
            except Exception:
                continue
            if ref.ElementId != wall.Id:
                continue
            f = wall.GetGeometryObjectFromReference(ref)
            if curved:
                try:
                    same = (not isinstance(f, PlanarFace) and abs(
                        f.get_Radius(0).GetLength() - face.get_Radius(0).GetLength()) < 2.0 * MM)
                except Exception:
                    same = False
                if same:
                    return group["recipe"]
            elif isinstance(f, PlanarFace) and \
                    f.FaceNormal.Normalize().DotProduct(normal) > 0.99:
                return group["recipe"]
    return None


class OpeningInfo(object):
    """Wall face, frame and opening rectangle for one door/window."""

    def __init__(self, doc, inst):
        wall = inst.Host
        if not isinstance(wall, Wall):
            raise ValueError("it isn't hosted in a wall")
        _, self.face = exterior_face(wall)
        self.frame = revit_build.make_frame(self.face)
        self.rect = opening_rect(wall, self.face, self.frame, inst)
        self.kind = opening_kind(inst)
        self.recipe = cladding_on_wall(doc, wall, self.face)
        self.label = u"{} {}".format("Door" if self.kind == "door" else "Window", inst.Id)


# --------------------------------------------------------------------------
# Types
# --------------------------------------------------------------------------
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
        revit_build._store(found, profiles.dumps({"kind": "flashing_type",
                                                  "type": type_name,
                                                  "material": material or u""}))
    cache[name] = found
    return found


def read_type(doc, el):
    t = doc.GetElement(el.GetTypeId())
    if t is None or not isinstance(t, DirectShapeType):
        return None
    try:
        data = revit_build._read(t)
    except Exception:
        return None
    return data if data and data.get("kind") == "flashing_type" else None


# --------------------------------------------------------------------------
# Building one side
# --------------------------------------------------------------------------
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


def expected_values(info, side, ftype, material, standoff):
    q = flashing.quantities(ftype, standoff, info.rect, side)
    return {
        "CT_Group": "Flashing",
        "CT_PanelId": u"{} {}".format(info.label, side),
        "CT_Profile": ftype["name"],
        "CT_Material": material or u"",
        "CT_Standoff": float(standoff),
        "CT_PanelLength": q["length_mm"],
        "CT_DevelopedArea": q["developed_area_m2"],
        "CT_Weight": q["weight_kg"],
        "CT_Thickness": float(ftype.get("thickness", 0.7)),
    }


def _revolve(frame, loops, u_start, run_mm, opts):
    """Sweep a section placed at u_start along the curve by run_mm (arc length)."""
    from Autodesk.Revit.DB import Frame
    radial = frame._radial(u_start)
    origin = XYZ(frame.center.X, frame.center.Y, frame.point(u_start, 0.0).Z)
    bz = XYZ.BasisZ
    by = bz.CrossProduct(radial)
    angle = frame.s * (run_mm * MM) / frame.radius
    a0, a1 = (0.0, angle) if angle > 0 else (angle, 0.0)
    return GeometryCreationUtilities.CreateRevolvedGeometry(
        Frame(origin, radial, by, bz), loops, a0, a1, opts)


def build_side(doc, ds, inst, info, side, ftype, material, standoff, type_cache):
    """Create (ds=None) or update in place one flashing piece."""
    mat_id = revit_build.find_material(doc, material)
    place = flashing.placements(info.rect, side, ftype)
    shapes = List[GeometryObject]()
    for part, loop2d in flashing.loops(ftype, standoff).items():
        su, sv, ru, rv, ou, ov, length = place[part]
        if length <= 1.0:
            continue
        pts = revit_build._drop_short(loop2d, revit_build.MIN_EDGE, closed=True)
        pts3 = [info.frame.point(su + ou * x, sv + ov * x, z) for x, z in pts]
        loop = CurveLoop()
        for a, b in zip(pts3, pts3[1:] + pts3[:1]):
            loop.Append(Line.CreateBound(a, b))
        loops = List[CurveLoop]()
        loops.Add(loop)
        opts = SolidOptions(mat_id, ElementId.InvalidElementId)
        if getattr(info.frame, "curved", False) and ru:
            # head/sill on a curved wall: turn the section around the wall's axis
            shapes.Add(_revolve(info.frame, loops, su, ru * length, opts))
        else:
            direction = info.frame.right.Multiply(ru).Add(info.frame.up.Multiply(rv))
            shapes.Add(GeometryCreationUtilities.CreateExtrusionGeometry(
                loops, direction, length * MM, opts))
    if shapes.Count == 0:
        return None
    if ds is None:
        ds = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_GenericModel))
        ds.ApplicationId = FLASH_APP
        ds.ApplicationDataId = inst.UniqueId
    t = flashing_type(doc, ftype["name"], material, type_cache)
    if ds.GetTypeId() != t.Id:
        ds.SetTypeId(t.Id)
    ds.SetShape(shapes)
    revit_params.set_values(ds, expected_values(info, side, ftype, material, standoff))
    revit_build._store(ds, profiles.dumps({
        "kind": "flashing", "version": 2, "opening": inst.UniqueId, "side": side,
        "type": ftype["name"], "material": material or u"", "standoff": float(standoff)}))
    return ds


def add(doc, inst, ftype, material, include_sill, type_cache, standoff=None):
    """Add (or replace) flashings on one door/window. Call inside a transaction.

    material: a material name, or None to use the cladding's material.
    standoff: wall to underside of the flashing in mm, None = just outside
    the cladding. Returns (sides, material, standoff).
    """
    info = OpeningInfo(doc, inst)
    if material is None:
        material = (info.recipe or {}).get("material") or u""
    if standoff is None:
        standoff = flashing.default_standoff(info.recipe)
    remove(doc, inst)
    sides = flashing.sides_for(info.kind, info.rect, info.frame.extents, include_sill)
    made = []
    for side in sides:
        if build_side(doc, None, inst, info, side, ftype, material, standoff, type_cache):
            made.append(side)
    return made, material, standoff


# --------------------------------------------------------------------------
# Edits made in Properties
# --------------------------------------------------------------------------
def apply_edits(doc, pieces, library=None):
    """Rebuild flashing pieces whose CT_Profile / CT_Material / CT_Standoff (or
    type) were changed. Bad values are put back.

    Returns (changes, errors): lists of text lines.
    """
    library = library if library is not None else flashing.load_library()
    changes, errors, cache, infos = [], [], {}, {}
    for ds in pieces:
        try:
            data = revit_build._read(ds)
        except Exception:
            data = None
        if not data or data.get("kind") != "flashing":
            continue
        inst = doc.GetElement(data["opening"])
        if inst is None:
            errors.append(u"{}: its door/window no longer exists".format(ds.Id))
            continue
        try:
            info = infos.get(inst.Id) or OpeningInfo(doc, inst)
            infos[inst.Id] = info
            ftype = flashing.find_type(data["type"], library)
        except Exception as ex:
            errors.append(u"{}: {}".format(ds.Id, ex))
            continue
        standoff = data.get("standoff")
        if standoff is None:
            standoff = flashing.default_standoff(info.recipe)
        expected = expected_values(info, data["side"], ftype, data.get("material", u""), standoff)
        current = revit_params.get_values(ds)

        edits = dict((n, current[n]) for n in EDITABLE
                     if inputs.differs(n, current.get(n), expected.get(n)))
        if not edits:
            t = read_type(doc, ds)
            if t and (inputs.differs("CT_Profile", t.get("type"), expected["CT_Profile"]) or
                      inputs.differs("CT_Material", t.get("material", u""),
                                     expected["CT_Material"])):
                edits = {"CT_Profile": t.get("type"), "CT_Material": t.get("material", u"")}
        if not edits:
            continue
        try:
            new_type = flashing.find_type(edits.get("CT_Profile", ftype["name"]), library)
            new_mat = inputs.material(edits.get("CT_Material", data.get("material", u"")))
            new_off = inputs.length("CT_Standoff", edits.get("CT_Standoff", standoff))
        except ValueError as ex:
            revit_params.set_values(ds, expected)
            t = flashing_type(doc, ftype["name"], data.get("material", u""), cache)
            if ds.GetTypeId() != t.Id:
                ds.SetTypeId(t.Id)
            errors.append(u"{} {}: {}".format(info.label, data["side"], ex))
            continue
        build_side(doc, ds, inst, info, data["side"], new_type, new_mat, new_off, cache)
        changes.append(u"{} {}: {}, {}, standoff {:g} mm".format(
            info.label, data["side"], new_type["name"], new_mat or u"no material", new_off))
    return changes, errors
