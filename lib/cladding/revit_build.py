# -*- coding: utf-8 -*-
"""Everything that touches the Revit API: face frames, panel solids,
DirectShapes, the cladding type, parameters and stored data.

Revit works in feet internally; this module converts from mm at the edges.

Storage:
  - one DataStorage element per cladding holds the group data
    (defaults, layout, per-panel edits), see groupdata.py
  - each panel stores {"kind": "panel", "group", "key", "width", "length"}
  - panels use a DirectShapeType per profile + material
    ("Cladding - Corrugated 18/76 - Stál"); switching type changes the panel
  - rebuilding updates panels in place, so element ids and tags survive
"""
import uuid

import clr
clr.AddReference("RevitAPI")
from System import Guid, String
from System.Collections.Generic import List
from Autodesk.Revit.DB import (
    XYZ, Line, CurveLoop, GeometryCreationUtilities, SolidOptions, Transform,
    BooleanOperationsUtils, BooleanOperationsType,
    DirectShape, DirectShapeType, Element, ElementId, BuiltInCategory, BuiltInParameter,
    GeometryObject, FilteredElementCollector, Material, PlanarFace, Reference,
)
from Autodesk.Revit.DB.ExtensibleStorage import (
    Schema, SchemaBuilder, Entity, AccessLevel, DataStorage,
)

from cladding import profiles, groupdata, revit_params

MM = 1.0 / 304.8
MIN_MODEL_T = 1.5   # mm, thinnest sheet Revit can model reliably
MIN_EDGE = 1.2      # mm, Revit's short-curve tolerance is ~0.8 mm
TRIM_DEPTH = 2000.0  # mm in front of the face covered by the trimming prism
APP_ID = "CladdingTools"
SCHEMA_GUID = Guid("6b0f3c1e-8a52-4d6f-9c1b-2e7a5d4f9a31")


# --------------------------------------------------------------------------
# Face frame
# --------------------------------------------------------------------------
class FaceFrame(object):
    """Right/up/normal axes and 2D extents (mm) of a planar face."""

    def __init__(self, face):
        if not isinstance(face, PlanarFace):
            raise ValueError("only flat faces are supported")
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
        self.face_extents = self.extents
        self._prisms = {}
        self._boxes = []            # (u0, u1, v0, v1) added past outside corners

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
        res = self.face.Project(self.point(u_mm, v_mm))
        return res is not None and self.face.IsInside(res.UVPoint)

    def end_span(self, side):
        """(u, v_low, v_high) of the face's left (-1) or right (+1) end edge."""
        u_end = self.face_extents[1] if side > 0 else self.face_extents[0]
        vs = []
        for loop in self.face.EdgeLoops:
            for edge in loop:
                for p in edge.Tessellate():
                    d = p - self.origin
                    if abs(d.DotProduct(self.right) / MM - u_end) < 1.0:
                        vs.append(d.DotProduct(self.up) / MM)
        if not vs:
            return u_end, None, None
        return u_end, min(vs), max(vs)

    def extend(self, side, mm):
        """Move the left (-1) or right (+1) end by mm (negative = shorten).
        Extra area past the face is added to the trimming outline."""
        if abs(mm) < 0.1:
            return
        u0, u1, v0, v1 = self.extents
        if side > 0:
            u1 += mm
        else:
            u0 -= mm
        self.extents = (u0, u1, v0, v1)
        if mm > 0:
            u_end, lo, hi = self.end_span(side)
            if lo is not None and hi - lo > 10.0:
                if side > 0:
                    self._boxes.append((u_end - 1.0, u_end + mm, lo, hi))
                else:
                    self._boxes.append((u_end - mm, u_end + 1.0, lo, hi))
        self._prisms = {}

    def prism(self, material_id):
        """The face outline (with door/window holes, plus any corner
        extensions) pushed out in front of the face. Panels are intersected
        with it, so they get cut around openings and along sloped edges.
        Made in the panel's material so the cut edges keep it."""
        key = material_id.ToString()
        if key not in self._prisms:
            opts = SolidOptions(material_id, ElementId.InvalidElementId)
            depth = (TRIM_DEPTH + 1.0) * MM
            back = Transform.CreateTranslation(self.normal.Multiply(-1.0 * MM))
            loops = List[CurveLoop]()
            for loop in self.face.GetEdgesAsCurveLoops():
                loops.Add(CurveLoop.CreateViaTransform(loop, back))
            solid = GeometryCreationUtilities.CreateExtrusionGeometry(
                loops, self.normal, depth, opts)
            for u0, u1, v0, v1 in self._boxes:
                pts = [self.point(u0, v0, -1.0), self.point(u1, v0, -1.0),
                       self.point(u1, v1, -1.0), self.point(u0, v1, -1.0)]
                box = CurveLoop()
                for a, b in zip(pts, pts[1:] + pts[:1]):
                    box.Append(Line.CreateBound(a, b))
                bl = List[CurveLoop]()
                bl.Add(box)
                try:
                    solid = BooleanOperationsUtils.ExecuteBooleanOperation(
                        solid, GeometryCreationUtilities.CreateExtrusionGeometry(
                            bl, self.normal, depth, opts),
                        BooleanOperationsType.Union)
                except Exception as ex:
                    print(u"Corner extension skipped: {}".format(ex))
            self._prisms[key] = solid
        return self._prisms[key]


# --------------------------------------------------------------------------
# Corners between claddings
# --------------------------------------------------------------------------
def _clad_faces(doc, current_group):
    """[(stable ref, PlanarFace, surface mm)] for every cladded face in the
    model; the group being built uses its current (maybe edited) recipe."""
    from cladding import flashing
    out = []
    groups = [g for _, g in _group_storages(doc) if g.get("group") != current_group["group"]]
    groups.append(current_group)
    for g in groups:
        try:
            surface = flashing.cladding_surface(g["recipe"])
        except Exception:
            continue
        for s in g.get("faces", []):
            try:
                ref = Reference.ParseFromStableRepresentation(doc, s)
                el = doc.GetElement(ref.ElementId)
                f = el.GetGeometryObjectFromReference(ref) if el else None
            except Exception:
                f = None
            if isinstance(f, PlanarFace):
                out.append((s, f, surface))
    return out


def _meets(frame, f):
    """True when face f is at an angle to this face and touches one of its ends."""
    if abs(f.FaceNormal.Normalize().DotProduct(frame.normal)) > 0.1:
        return False
    for side in (-1, 1):
        u_end, lo, hi = frame.end_span(side)
        if lo is None:
            continue
        res = f.Project(frame.point(u_end, (lo + hi) / 2.0))
        if res is not None and res.Distance <= 5.0 * MM:
            return True
    return False


def neighbor_groups(doc, group):
    """Other claddings that meet this one at a corner (they need rebuilding
    when this one changes, so the corner joint stays right)."""
    mine = []
    for s in group.get("faces", []):
        try:
            ref = Reference.ParseFromStableRepresentation(doc, s)
            f = doc.GetElement(ref.ElementId).GetGeometryObjectFromReference(ref)
            if isinstance(f, PlanarFace):
                mine.append(f)
        except Exception:
            pass
    out = []
    for _, g in _group_storages(doc):
        if g.get("group") == group["group"]:
            continue
        hit = False
        for s in g.get("faces", []):
            try:
                ref = Reference.ParseFromStableRepresentation(doc, s)
                f = doc.GetElement(ref.ElementId).GetGeometryObjectFromReference(ref)
            except Exception:
                continue
            if not isinstance(f, PlanarFace):
                continue
            fr = FaceFrame(f)
            if any(_meets(fr, m) or _meets(FaceFrame(m), f) for m in mine):
                hit = True
                break
        if hit:
            out.append(g)
    return out


def rebuild_neighbors(doc, group):
    """Rebuild claddings at corners of `group`. Returns how many."""
    n = 0
    for g in neighbor_groups(doc, group):
        try:
            build(doc, resolve_faces(doc, g["faces"]), g, ensure_params=False)
            n += 1
        except Exception as ex:
            print(u"Neighbour cladding {} not updated: {}".format(g.get("group"), ex))
    return n


def corner_extensions(frame, my_ref, my_surface, clad_faces):
    """{side: mm} to extend (+) or shorten (-) the left (-1) / right (+1) end.

    At a corner where another cladded face meets this one, exactly one of
    the two claddings covers the corner: the one whose face reference sorts
    first ("laps"). Outside corner: the lapping cladding runs past the
    corner by the other cladding's thickness, covering its end. Inside
    corner: the other cladding is shortened by the lapping one's thickness.
    """
    out = {}
    for side in (-1, 1):
        u_end, lo, hi = frame.end_span(side)
        if lo is None:
            continue
        p = frame.point(u_end, (lo + hi) / 2.0)
        direction = frame.right.Multiply(side)
        for s, f, surface in clad_faces:
            if s == my_ref:
                continue
            n = f.FaceNormal.Normalize()
            if abs(n.DotProduct(frame.normal)) > 0.1:
                continue                    # not a corner (parallel faces)
            res = f.Project(p)
            if res is None or res.Distance > 5.0 * MM:
                continue                    # doesn't meet this end
            laps = my_ref < s
            turn = n.DotProduct(direction)
            if turn > 0.9 and laps:         # outside corner
                out[side] = surface
            elif turn < -0.9 and not laps:  # inside corner
                out[side] = -surface - 1.0
            break
    return out


# --------------------------------------------------------------------------
# Panel solid
# --------------------------------------------------------------------------
def panel_solid(frame, panel, recipe, material_id):
    prof = recipe["profile"]
    pts = profiles.tile(profiles.profile_points(prof), panel["width"])
    # Revit can't make edges shorter than ~0.8 mm, so thin sheets are
    # modelled at MIN_MODEL_T. Quantities still use the real thickness.
    t = max(float(prof.get("thickness", 0.7)), MIN_MODEL_T)
    pts = _drop_short(pts, MIN_EDGE)
    loop2d = _drop_short(profiles.section_loop(pts, t), MIN_EDGE, closed=True)
    lift = float(recipe["standoff"]) + t   # back of the sheet sits on the standoff

    if recipe["orientation"] == "horizontal":
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


def trimmed(frame, solid, panel, orientation, material_id):
    """(solid cut to the face, kept fraction 0..1). (None, 0) when nothing is left."""
    try:
        cut = BooleanOperationsUtils.ExecuteBooleanOperation(
            solid, frame.prism(material_id), BooleanOperationsType.Intersect)
        if cut is None or cut.Volume < 1e-9:
            return None, 0.0
        return cut, min(1.0, cut.Volume / solid.Volume)
    except Exception:
        # Boolean failed (rare, odd geometry): fall back to keep/skip by centre
        if orientation == "horizontal":
            cu, cv = panel["u0"] + panel["length"] / 2.0, panel["v0"] + panel["width"] / 2.0
        else:
            cu, cv = panel["u0"] + panel["width"] / 2.0, panel["v0"] + panel["length"] / 2.0
        return (solid, 1.0) if frame.contains(cu, cv) else (None, 0.0)


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
# Materials and the cladding type
# --------------------------------------------------------------------------
def find_material(doc, name):
    """Exact name, else ignoring case and accents ('al' finds 'Ál')."""
    if not name:
        return ElementId.InvalidElementId
    mats = dict((m.Name, m.Id) for m in FilteredElementCollector(doc).OfClass(Material))
    hit = profiles.match_name(name, mats.keys())
    return mats[hit] if hit is not None else ElementId.InvalidElementId


def element_name(el):
    try:
        return Element.Name.__get__(el)
    except Exception:
        p = el.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        return p.AsString() if p else u""


def panel_type(doc, profile_name, material, cache=None):
    """The shared panel type for a profile + material, created if needed."""
    name = groupdata.type_name(profile_name, material)
    if cache is not None and name in cache:
        return cache[name]
    found = None
    for t in FilteredElementCollector(doc).OfClass(DirectShapeType):
        if element_name(t) == name:
            found = t
            break
    if found is None:
        found = DirectShapeType.Create(doc, name, ElementId(BuiltInCategory.OST_GenericModel))
        _store(found, profiles.dumps({"kind": "type", "profile": profile_name,
                                      "material": material or u""}))
    if cache is not None:
        cache[name] = found
    return found


def read_type(doc, el):
    """{"profile", "material"} of a panel's current type, if it is ours."""
    t = doc.GetElement(el.GetTypeId())
    if t is None or not isinstance(t, DirectShapeType):
        return None
    try:
        data = _read(t)
    except Exception:
        return None
    return data if data and data.get("kind") == "type" else None


# --------------------------------------------------------------------------
# Build / delete
# --------------------------------------------------------------------------
def build(doc, faces, group, ensure_params=True):
    """Build or update every panel of a cladding. Call inside a transaction.

    Panels that already exist (matched by their position key) are updated in
    place, so their element ids, and any tags on them, survive. New positions
    get new panels; panels whose position no longer exists are deleted.

    faces: list of (Reference, PlanarFace). `group` is updated in place with
    the placed layout and saved to the model.
    Returns a summary dict: created, updated, removed, skipped, trimmed,
    totals, missing_materials.
    """
    param_report = revit_params.ensure_parameters(doc) if ensure_params else {}
    gid = group["group"]
    group["faces"] = [ref.ConvertToStableRepresentation(doc) for ref, _ in faces]

    existing, stray = {}, []
    for ds in group_panels(doc, gid):
        try:
            pd = _read(ds)
        except Exception:
            pd = None
        if pd and pd.get("key") and pd["key"] not in existing:
            existing[pd["key"]] = ds
        else:
            stray.append(ds)              # older version, or a duplicate

    mat_cache, type_cache, missing = {}, {}, set()
    summary = {"created": 0, "updated": 0, "removed": 0, "skipped": 0, "trimmed": 0,
               "missing_materials": [],
               "totals": {"cover_area_m2": 0.0, "developed_area_m2": 0.0, "weight_kg": 0.0,
                          "total_length_m": 0.0, "count": 0}}
    layouts = []

    clad = None
    corners = 0
    for fi, (ref, face) in enumerate(faces):
        frame = FaceFrame(face)
        if group["recipe"].get("corners", True):
            if clad is None:
                clad = _clad_faces(doc, group)
            for side, mm in corner_extensions(frame, group["faces"][fi],
                                              None, clad).items():
                frame.extend(side, mm)
                corners += 1
        lay = group["layouts"][fi] if fi < len(group["layouts"]) else None
        panels, placed_layout = groupdata.place(frame.extents, lay, group["recipe"])
        layouts.append(placed_layout)
        for panel in panels:
            k = groupdata.key(fi, panel["col"], panel["row"])
            recipe = groupdata.panel_recipe(group, k)
            mname = recipe["material"] or u""
            if mname not in mat_cache:
                mat_cache[mname] = find_material(doc, mname)
                if mname and mat_cache[mname] == ElementId.InvalidElementId:
                    missing.add(mname)
            mat = mat_cache[mname]
            try:
                solid = panel_solid(frame, panel, recipe, mat)
            except Exception as ex:
                print(u"Panel {} skipped: {}".format(k, ex))
                summary["skipped"] += 1
                continue
            solid, kept = trimmed(frame, solid, panel, recipe["orientation"], mat)
            if solid is None:
                summary["skipped"] += 1          # fully inside an opening
                continue
            if kept < 0.999:
                summary["trimmed"] += 1

            ds = existing.pop(k, None)
            if ds is None:
                ds = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_GenericModel))
                ds.ApplicationId = APP_ID
                ds.ApplicationDataId = gid
                summary["created"] += 1
            else:
                summary["updated"] += 1
            values = groupdata.expected_values(group, k, panel["width"], panel["length"])
            ptype = panel_type(doc, values["CT_Profile"], mname, type_cache)
            if ds.GetTypeId() != ptype.Id:
                ds.SetTypeId(ptype.Id)
            shapes = List[GeometryObject]()
            shapes.Add(solid)
            ds.SetShape(shapes)

            for q in ("CT_CoverArea", "CT_DevelopedArea", "CT_Weight"):
                values[q] *= kept                # cut-outs reduce the quantities
            revit_params.set_values(ds, values)
            _write_comment(ds, gid, values["CT_PanelId"], values["CT_Profile"], panel)
            _store(ds, profiles.dumps({
                "kind": "panel", "version": groupdata.VERSION, "group": gid, "key": k,
                "width": round(panel["width"], 2), "length": round(panel["length"], 2),
                "kept": round(kept, 4)}))

            t = summary["totals"]
            t["count"] += 1
            t["total_length_m"] += panel["length"] / 1000.0
            t["cover_area_m2"] += values["CT_CoverArea"]
            t["developed_area_m2"] += values["CT_DevelopedArea"]
            t["weight_kg"] += values["CT_Weight"]

    leftovers = List[ElementId]()
    for ds in list(existing.values()) + stray:
        leftovers.Add(ds.Id)
    if leftovers.Count:
        doc.Delete(leftovers)
    summary["removed"] = leftovers.Count

    group["layouts"] = layouts
    save_group(doc, group)
    summary["missing_materials"] = sorted(missing)
    summary["params"] = param_report
    summary["corners"] = corners
    return summary


def group_panels(doc, group_id):
    return [ds for ds in FilteredElementCollector(doc).OfClass(DirectShape)
            if ds.ApplicationId == APP_ID and ds.ApplicationDataId == group_id]


def delete_group(doc, group_id):
    ids = List[ElementId]()
    for ds in group_panels(doc, group_id):
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


def new_group_id():
    return uuid.uuid4().hex[:12]


def _write_comment(el, group_id, panel_id, profile_name, panel):
    c = el.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    if c is not None and not c.IsReadOnly:
        c.Set(u"Cladding {} | {} | {} | {:.0f} x {:.0f} mm".format(
            group_id, panel_id, profile_name, panel["width"], panel["length"]))


# --------------------------------------------------------------------------
# Stored data
# --------------------------------------------------------------------------
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


def _read(el):
    s = Schema.Lookup(SCHEMA_GUID)
    if not s:
        return None
    e = el.GetEntity(s)
    if not e.IsValid():
        return None
    return profiles.loads(e.Get[String](s.GetField("Data")))


def read_panel(el):
    """A panel's own data. Old (v1) panels return their full recipe payload."""
    return _read(el)


def _group_storages(doc):
    for ds in FilteredElementCollector(doc).OfClass(DataStorage):
        try:
            data = _read(ds)
        except Exception:
            continue
        if data and data.get("kind") == "group":
            yield ds, data


def load_group(doc, group_id):
    for _, data in _group_storages(doc):
        if data.get("group") == group_id:
            return data
    return None


def save_group(doc, group):
    for ds, data in _group_storages(doc):
        if data.get("group") == group["group"]:
            _store(ds, profiles.dumps(group))
            return
    ds = DataStorage.Create(doc)
    _store(ds, profiles.dumps(group))


def group_for_panel(doc, el):
    """(group data, panel data) for a panel, or (None, None).

    Old (v1) panels get group data converted from their recipe; it is saved
    the next time the cladding is built.
    """
    panel = read_panel(el)
    if not panel:
        return None, None
    group = load_group(doc, panel["group"])
    if group is None and "recipe" in panel:
        group = groupdata.from_legacy(panel)
    return group, panel
