# -*- coding: utf-8 -*-
"""Everything that touches the Revit API: face frames, panel solids,
DirectShapes, parameters and the stored recipe.

Revit works in feet internally; this module converts from mm at the edges.
"""
import json
import uuid

import clr
clr.AddReference("RevitAPI")
from System import Guid, String
from System.Collections.Generic import List
from Autodesk.Revit.DB import (
    XYZ, UV, Line, CurveLoop, GeometryCreationUtilities, SolidOptions,
    DirectShape, ElementId, BuiltInCategory, BuiltInParameter,
    GeometryObject, FilteredElementCollector, Material, PlanarFace, Reference,
)
from Autodesk.Revit.DB.ExtensibleStorage import (
    Schema, SchemaBuilder, Entity, AccessLevel,
)

from cladding import profiles, layout

MM = 1.0 / 304.8
MIN_MODEL_T = 1.5   # mm, thinnest sheet Revit can model reliably
MIN_EDGE = 1.2      # mm, Revit's short-curve tolerance is ~0.8 mm
APP_ID = "CladdingTools"
SCHEMA_GUID = Guid("6b0f3c1e-8a52-4d6f-9c1b-2e7a5d4f9a31")

# Shared parameters filled in when they exist on Generic Models.
# Add them once with Manage > Shared Parameters to get them in schedules.
PARAMS = {
    "CT_Profile": "profile",
    "CT_PanelLength": "length_mm",
    "CT_PanelWidth": "width_mm",
    "CT_CoverArea": "cover_area_m2",
    "CT_DevelopedArea": "developed_area_m2",
    "CT_Weight": "weight_kg",
}


# --------------------------------------------------------------------------
# Face frame
# --------------------------------------------------------------------------
class FaceFrame(object):
    """Right/up/normal axes and 2D extents (mm) of a planar face."""

    def __init__(self, face):
        if not isinstance(face, PlanarFace):
            raise ValueError("v1 supports flat faces only")
        self.face = face
        self.normal = face.FaceNormal.Normalize()
        self.origin = face.Origin
        z = XYZ.BasisZ
        up = z - self.normal.Multiply(z.DotProduct(self.normal))
        if up.GetLength() < 1e-6:          # roof / floor / ceiling face
            up = face.YVector
        self.up = up.Normalize()
        self.right = self.up.CrossProduct(self.normal).Normalize()
        self.extents = self._extents()

    def _extents(self):
        us, vs = [], []
        for loop in self.face.EdgeLoops:
            for edge in loop:
                for p in edge.Tessellate():
                    d = p - self.origin
                    us.append(d.DotProduct(self.right) / MM)
                    vs.append(d.DotProduct(self.up) / MM)
        return (min(us), max(us), min(vs), max(vs))

    def point(self, u_mm, v_mm, n_mm=0.0):
        return (self.origin
                + self.right.Multiply(u_mm * MM)
                + self.up.Multiply(v_mm * MM)
                + self.normal.Multiply(n_mm * MM))

    def contains(self, u_mm, v_mm):
        """False when the point sits in an opening or outside the face."""
        res = self.face.Project(self.point(u_mm, v_mm))
        return res is not None and self.face.IsInside(res.UVPoint)


# --------------------------------------------------------------------------
# Panel solid
# --------------------------------------------------------------------------
def panel_solid(frame, panel, recipe, material_id):
    r = layout.merged(recipe)
    prof = r["profile"]
    pts = profiles.tile(profiles.profile_points(prof), panel["width"])
    # Revit can't make edges shorter than ~0.8 mm, so thin sheets are
    # modelled at MIN_MODEL_T. Quantities still use the real thickness.
    t = max(float(prof.get("thickness", 0.7)), MIN_MODEL_T)
    pts = _drop_short(pts, MIN_EDGE)
    loop2d = _drop_short(profiles.section_loop(pts, t), MIN_EDGE, closed=True)
    lift = float(r["standoff"]) + t   # back of the sheet sits on the standoff

    if r["orientation"] == "horizontal":
        # profile x runs up the face, extrude along right
        def p3(x, z):
            return frame.point(panel["u0"], panel["v0"] + x, z + lift)
        direction = frame.right
    else:
        def p3(x, z):
            return frame.point(panel["u0"] + x, panel["v0"], z + lift)
        direction = frame.up

    loop = CurveLoop()
    pts3 = [p3(x, z) for x, z in loop2d]
    for a, b in zip(pts3, pts3[1:] + pts3[:1]):
        loop.Append(Line.CreateBound(a, b))

    opts = SolidOptions(material_id, ElementId.InvalidElementId)
    loops = List[CurveLoop]()
    loops.Add(loop)
    return GeometryCreationUtilities.CreateExtrusionGeometry(
        loops, direction, panel["length"] * MM, opts)


def _drop_short(pts, min_len, closed=False):
    """Remove points that would create edges shorter than min_len (mm)."""
    out = [pts[0]]
    for p in pts[1:]:
        if ((p[0] - out[-1][0]) ** 2 + (p[1] - out[-1][1]) ** 2) ** 0.5 >= min_len:
            out.append(p)
    if not closed and out[-1] != pts[-1]:
        out[-1] = pts[-1]          # keep the true panel edge
    if closed:
        while len(out) > 3 and ((out[0][0] - out[-1][0]) ** 2 +
                                (out[0][1] - out[-1][1]) ** 2) ** 0.5 < min_len:
            out.pop()
    return out


# --------------------------------------------------------------------------
# Build / delete
# --------------------------------------------------------------------------
def find_material(doc, name):
    if not name:
        return ElementId.InvalidElementId
    for m in FilteredElementCollector(doc).OfClass(Material):
        if m.Name == name:
            return m.Id
    return ElementId.InvalidElementId


def build(doc, faces, recipe, group_id=None):
    """faces: list of (Reference, PlanarFace). Call inside a transaction.

    Returns (group_id, created element count, skipped count, totals).
    """
    r = layout.merged(recipe)
    group_id = group_id or uuid.uuid4().hex[:12]
    mat = find_material(doc, r["material"])
    created, skipped, all_panels = 0, 0, []
    refs = [ref.ConvertToStableRepresentation(doc) for ref, _ in faces]
    payload = json.dumps({"group": group_id, "faces": refs, "recipe": r})

    for ref, face in faces:
        frame = FaceFrame(face)
        for panel in layout.layout(frame.extents, r):
            cu, cv = layout.panel_center(panel, r["orientation"])
            if not frame.contains(cu, cv):
                skipped += 1
                continue
            try:
                solid = panel_solid(frame, panel, r, mat)
            except Exception as ex:
                print("Panel skipped: {}".format(ex))
                skipped += 1
                continue
            ds = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_GenericModel))
            ds.ApplicationId = APP_ID
            ds.ApplicationDataId = group_id
            shapes = List[GeometryObject]()
            shapes.Add(solid)
            ds.SetShape(shapes)
            q = profiles.panel_quantities(r["profile"], panel["width"], panel["length"])
            q.update({"profile": r["profile"].get("name", "Custom"),
                      "length_mm": round(panel["length"]),
                      "width_mm": round(panel["width"])})
            _write_params(ds, q, group_id)
            _store(ds, payload)
            all_panels.append(panel)
            created += 1
    return group_id, created, skipped, layout.totals(all_panels, r)


def delete_group(doc, group_id):
    ids = List[ElementId]()
    for ds in FilteredElementCollector(doc).OfClass(DirectShape):
        if ds.ApplicationId == APP_ID and ds.ApplicationDataId == group_id:
            ids.Add(ds.Id)
    if ids.Count:
        doc.Delete(ids)
    return ids.Count


def resolve_faces(doc, stable_refs):
    faces = []
    for s in stable_refs:
        ref = Reference.ParseFromStableRepresentation(doc, s)
        el = doc.GetElement(ref.ElementId)
        face = el.GetGeometryObjectFromReference(ref) if el else None
        if face is None:
            raise ValueError("host face no longer exists")
        faces.append((ref, face))
    return faces


# --------------------------------------------------------------------------
# Parameters and stored recipe
# --------------------------------------------------------------------------
def _write_params(el, values, group_id):
    for pname, key in PARAMS.items():
        p = el.LookupParameter(pname)
        if p is None or p.IsReadOnly:
            continue
        v = values[key]
        try:
            if isinstance(v, str):
                p.Set(v)
            elif pname in ("CT_PanelLength", "CT_PanelWidth"):
                p.Set(float(v) * MM)                   # length params: feet
            elif pname in ("CT_CoverArea", "CT_DevelopedArea"):
                p.Set(float(v) * 10.7639104)           # area params: sq ft
            else:
                p.Set(float(v))                        # weight: plain number
        except Exception:
            pass
    c = el.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    if c is not None:
        c.Set("Cladding {} | {} | {} x {} mm".format(
            group_id, values["profile"], values["width_mm"], values["length_mm"]))


def _schema():
    s = Schema.Lookup(SCHEMA_GUID)
    if s:
        return s
    b = SchemaBuilder(SCHEMA_GUID)
    b.SetSchemaName("CladdingToolsRecipe")
    b.SetReadAccessLevel(AccessLevel.Public)
    b.SetWriteAccessLevel(AccessLevel.Public)
    b.AddSimpleField("Data", String)
    return b.Finish()


def _store(el, payload):
    s = _schema()
    e = Entity(s)
    e.Set[String](s.GetField("Data"), payload)
    el.SetEntity(e)


def read_stored(el):
    s = Schema.Lookup(SCHEMA_GUID)
    if not s:
        return None
    e = el.GetEntity(s)
    if not e.IsValid():
        return None
    return json.loads(e.Get[String](s.GetField("Data")))
