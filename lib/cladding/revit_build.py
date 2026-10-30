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
    XYZ, UV, Line, CurveLoop, GeometryCreationUtilities, SolidOptions, Transform,
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
    curved = False

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

    def _box_solid(self, box, opts, depth):
        u0, u1, v0, v1 = box
        pts = [self.point(u0, v0, -1.0), self.point(u1, v0, -1.0),
               self.point(u1, v1, -1.0), self.point(u0, v1, -1.0)]
        loop = CurveLoop()
        for a, b in zip(pts, pts[1:] + pts[:1]):
            loop.Append(Line.CreateBound(a, b))
        bl = List[CurveLoop]()
        bl.Add(loop)
        return GeometryCreationUtilities.CreateExtrusionGeometry(bl, self.normal, depth, opts)

    def prism_with(self, material_id, boxes):
        """prism() plus extra boxes (panel overhang past the face)."""
        base = self.prism(material_id)
        if not boxes:
            return base
        opts = SolidOptions(material_id, ElementId.InvalidElementId)
        depth = (TRIM_DEPTH + 1.0) * MM
        solid = base
        for box in boxes:
            try:
                solid = BooleanOperationsUtils.ExecuteBooleanOperation(
                    solid, self._box_solid(box, opts, depth), BooleanOperationsType.Union)
            except Exception as ex:
                print(u"Overhang skipped: {}".format(ex))
        return solid

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
# Curved faces (curved walls)
# --------------------------------------------------------------------------
class CurvedFaceFrame(object):
    """Unrolled frame for a vertical cylindrical face (a wall curved in plan).

    u = arc length along the face (mm), v = height (mm), n = distance out
    from the face. Panels are laid out on the unrolled face exactly like on a
    flat one; point() wraps them back onto the curve, so a vertical-rib
    profile is bent to the arc and extruded straight up.
    """
    curved = True

    def __init__(self, face):
        import math
        from Autodesk.Revit.DB import CylindricalFace
        if not isinstance(face, CylindricalFace):
            raise ValueError("only flat faces and curved walls are supported")
        axis = face.Axis.Normalize()
        if abs(abs(axis.Z) - 1.0) > 1e-3:
            raise ValueError("only curved faces with a vertical axis (curved walls) "
                             "are supported")
        self.face = face
        self.up = XYZ.BasisZ
        self.radius = face.get_Radius(0).GetLength()          # feet
        self.center = XYZ(face.Origin.X, face.Origin.Y, 0.0)
        self.ex = XYZ(face.get_Radius(0).X, face.get_Radius(0).Y, 0.0).Normalize()
        self.ey = self.up.CrossProduct(self.ex)
        self._math = math

        pts = [p for loop in face.EdgeLoops for edge in loop for p in edge.Tessellate()]
        bb = face.GetBoundingBox()
        uv_mid = UV((bb.Min.U + bb.Max.U) / 2.0, (bb.Min.V + bb.Max.V) / 2.0)
        mid = face.Evaluate(uv_mid)
        n_mid = face.ComputeNormal(uv_mid).Normalize()
        r_mid = mid - self.center
        r_mid = XYZ(r_mid.X, r_mid.Y, 0.0).Normalize()
        self.s = 1.0 if n_mid.DotProduct(r_mid) > 0 else -1.0   # +1: cladding outside the curve
        self.theta_ref = self._theta(mid)          # middle of the face: spans up to 360°
        self._z0 = min(p.Z for p in pts)
        us, vs = [], []
        for p in pts:
            u, v = self.uv(p)
            us.append(u)
            vs.append(v)
        self.extents = (min(us), max(us), min(vs), max(vs))
        self.face_extents = self.extents
        u_mid = (self.extents[0] + self.extents[1]) / 2.0
        self.origin = self.point(u_mid, 0.0)
        self.normal = self._radial(u_mid).Multiply(self.s)
        self.right = self.up.CrossProduct(self.normal).Normalize()
        self._prisms = {}

    # ---- mapping -------------------------------------------------------------
    def _theta(self, p):
        d = p - self.center
        return self._math.atan2(d.DotProduct(self.ey), d.DotProduct(self.ex))

    def uv(self, p):
        math = self._math
        d = self._theta(p) - self.theta_ref
        while d > math.pi:
            d -= 2 * math.pi
        while d <= -math.pi:
            d += 2 * math.pi
        return self.s * d * self.radius / MM, (p.Z - self._z0) / MM

    def _angle(self, u_mm):
        return self.theta_ref + self.s * (u_mm * MM) / self.radius

    def _radial(self, u_mm):
        a = self._angle(u_mm)
        return self.ex.Multiply(self._math.cos(a)).Add(self.ey.Multiply(self._math.sin(a)))

    def point(self, u_mm, v_mm, n_mm=0.0):
        r = self.radius + self.s * n_mm * MM
        return (self.center.Add(self._radial(u_mm).Multiply(r))
                .Add(XYZ(0, 0, self._z0 + v_mm * MM)))

    def contains(self, u_mm, v_mm):
        res = self.face.Project(self.point(u_mm, v_mm))
        return res is not None and self.face.IsInside(res.UVPoint)

    def extend(self, side, mm):
        pass                                  # no corner joints on curved faces

    def end_span(self, side):
        return (self.extents[1] if side > 0 else self.extents[0]), None, None

    # ---- trimming --------------------------------------------------------------
    def _depth(self):
        d = TRIM_DEPTH
        if self.s < 0:                        # cladding inside the curve
            d = min(d, 0.9 * self.radius / MM)
        return d

    def sector(self, box, opts):
        """Solid filling the region in front of the face over a (u, v) box."""
        from Autodesk.Revit.DB import Arc
        u0, u1, v0, v1 = box
        if u1 - u0 < 1.0 or v1 - v0 < 1.0:
            return None
        um = (u0 + u1) / 2.0
        n_in, n_out = -1.0, self._depth()
        a0, a1, am = self.point(u0, v0, n_in), self.point(u1, v0, n_in), self.point(um, v0, n_in)
        b0, b1, bm = self.point(u0, v0, n_out), self.point(u1, v0, n_out), self.point(um, v0, n_out)
        loop = CurveLoop()
        loop.Append(Arc.Create(a0, a1, am))
        loop.Append(Line.CreateBound(a1, b1))
        loop.Append(Arc.Create(b1, b0, bm))
        loop.Append(Line.CreateBound(b0, a0))
        loops = List[CurveLoop]()
        loops.Add(loop)
        return GeometryCreationUtilities.CreateExtrusionGeometry(
            loops, self.up, (v1 - v0) * MM, opts)

    def _holes(self):
        """(u0, u1, v0, v1) of the door/window holes in the face."""
        boxes = []
        for loop in self.face.EdgeLoops:
            us, vs = [], []
            for edge in loop:
                for p in edge.Tessellate():
                    u, v = self.uv(p)
                    us.append(u)
                    vs.append(v)
            boxes.append((min(us), max(us), min(vs), max(vs)))
        boxes.sort(key=lambda b: (b[1] - b[0]) * (b[3] - b[2]), reverse=True)
        return boxes[1:]                     # the biggest loop is the outline

    def prism(self, material_id):
        key = material_id.ToString()
        if key not in self._prisms:
            opts = SolidOptions(material_id, ElementId.InvalidElementId)
            solid = self.sector(self.extents, opts)
            for hole in self._holes():
                cut = self.sector(hole, opts)
                if cut is None:
                    continue
                try:
                    solid = BooleanOperationsUtils.ExecuteBooleanOperation(
                        solid, cut, BooleanOperationsType.Difference)
                except Exception as ex:
                    print(u"Opening not cut out: {}".format(ex))
            self._prisms[key] = solid
        return self._prisms[key]

    def prism_with(self, material_id, boxes):
        base = self.prism(material_id)
        if not boxes:
            return base
        opts = SolidOptions(material_id, ElementId.InvalidElementId)
        solid = base
        for box in boxes:
            extra = self.sector(box, opts)
            if extra is None:
                continue
            try:
                solid = BooleanOperationsUtils.ExecuteBooleanOperation(
                    solid, extra, BooleanOperationsType.Union)
            except Exception as ex:
                print(u"Overhang skipped: {}".format(ex))
        return solid


def make_frame(face):
    """FaceFrame for a flat face, CurvedFaceFrame for a curved wall face."""
    if isinstance(face, PlanarFace):
        return FaceFrame(face)
    return CurvedFaceFrame(face)


def is_supported(face):
    try:
        make_frame(face)
        return True
    except Exception:
        return False


def _densify(pts, max_len=40.0):
    """Extra points on long straight bits, so flat parts follow the curve."""
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        L = abs(b[0] - a[0])
        n = int(L // max_len)
        for i in range(1, n + 1):
            t = float(i) / (n + 1)
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        out.append(b)
    return out


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
            standoff = float(g["recipe"].get("standoff", 30))
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
                out.append((s, f, surface, standoff))
    return out


CORNER_GAP = 600.0   # mm: an uncladded wall end up to this wide is bridged


def _corner(frame, side, f):
    """How face f meets the left (-1) / right (+1) end of this face.

    Returns (gap, turn) or None. gap = distance (mm) from this end to f's
    plane, along this face; it is > 0 when the end of another wall sits in
    between (walls not joined at the outside), and that strip gets covered.
    turn > 0: outside corner, < 0: inside corner.
    """
    n = f.FaceNormal.Normalize()
    if abs(n.DotProduct(frame.normal)) > 0.1:
        return None                         # parallel faces: not a corner
    u_end, lo, hi = frame.end_span(side)
    if lo is None:
        return None
    p = frame.point(u_end, (lo + hi) / 2.0)
    direction = frame.right.Multiply(side)
    turn = n.DotProduct(direction)
    if abs(turn) < 0.9:
        return None
    gap = (f.Origin - p).DotProduct(n) / turn / MM
    if gap < -5.0 or gap > CORNER_GAP:
        return None
    q = p.Add(direction.Multiply(max(gap, 0.0) * MM))
    res = f.Project(q)
    if res is None or res.Distance > (CORNER_GAP + 5.0) * MM:
        return None                         # f is somewhere else on that plane
    return max(gap, 0.0), turn


def _meets(frame, f):
    """True when face f meets one of this face's ends at a corner."""
    return any(_corner(frame, side, f) is not None for side in (-1, 1))


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
    When the walls aren't joined at the outside, the end of a wall sits
    between the two claddings; the cladding facing the same way as that wall
    end is first extended across it ("gap"), so nothing grey shows.
    """
    out = {}
    for side in (-1, 1):
        best = None
        for s, f, surface, standoff in clad_faces:
            if s == my_ref:
                continue
            rel = _corner(frame, side, f)
            if rel is None:
                continue
            gap, turn = rel
            if best is None or gap < best[0]:
                best = (gap, turn, s, surface, standoff)
        if best is None:
            continue
        gap, turn, s, surface, standoff = best
        laps = my_ref < s
        ext = gap
        if turn > 0:                   # outside corner
            # lapping side covers the other's end; the other tucks in under it
            ext += surface if laps else standoff
        elif not laps:                 # inside corner
            ext -= surface + 1.0
        if abs(ext) > 0.1:
            out[side] = ext
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
    if getattr(frame, "curved", False):
        if recipe["orientation"] == "horizontal":
            raise ValueError("horizontal ribs on a curved wall aren't supported; "
                             "use vertical ribs there")
        pts = _densify(pts)
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
            solid, frame.prism_with(material_id, panel.get("over")),
            BooleanOperationsType.Intersect)
        if cut is None or cut.Volume < 1e-9:
            return None, 0.0
        return cut, min(1.0, cut.Volume / solid.Volume)
    except Exception:
        # Boolean failed (rare, odd geometry): fall back to keep/skip by centre
        if panel.get("over"):
            return solid, 1.0
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
def panel_material(doc, name, perf, cache, missing=None):
    """Material id for a panel: the material itself, or its perforated variant."""
    from cladding import perforation
    key = (name or u"", perforation.label(perf))
    if key not in cache:
        base = find_material(doc, name)
        if name and base == ElementId.InvalidElementId and missing is not None:
            missing.add(name)
        if perf:
            from cladding import revit_perf
            base_name = doc.GetElement(base).Name if base != ElementId.InvalidElementId else u""
            try:
                cache[key] = revit_perf.ensure_material(doc, base_name, perf)
            except Exception as ex:
                print(u"Perforated material not made: {}".format(ex))
                cache[key] = base
        else:
            cache[key] = base
    return cache[key]


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


def panel_type(doc, profile_name, material, cache=None, perf=None):
    """The shared panel type for a profile + material (+ perforation)."""
    name = groupdata.type_name(profile_name, material, perf)
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
                                      "material": material or u"", "perforation": perf}))
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
# --------------------------------------------------------------------------
# Panel marks (P01, P02...)
# --------------------------------------------------------------------------
MARK_PREFIX = "P"


def _mark_key(v):
    from cladding import perforation
    return u"|".join([
        profiles.fold(v.get("CT_Profile") or u""),
        profiles.fold(perforation.base_material(v.get("CT_Material") or u"")),
        u"{:.1f}".format(float(v.get("CT_Thickness") or 0)),
        profiles.fold(v.get("CT_Perforation") or u"none"),
        u"{:.0f}".format(float(v.get("CT_PanelWidth") or 0)),
        u"{:.0f}".format(float(v.get("CT_PanelLength") or 0)),
    ])


def _marks_storage(doc):
    for ds in FilteredElementCollector(doc).OfClass(DataStorage):
        try:
            data = _read(ds)
        except Exception:
            continue
        if data and data.get("kind") == "marks":
            return ds, data
    return None, {"kind": "marks", "next": 1, "map": {}}


def number_panels(doc, panels=None):
    """Give cladding panels a mark (all panels in the project when `panels`
    is None). Identical panels share a mark project-wide; marks are kept in
    the project so they don't change when new sizes appear.
    Returns the number of distinct marks among the panels handled."""
    if panels is None:
        panels = [ds for ds in FilteredElementCollector(doc).OfClass(DirectShape)
                  if ds.ApplicationId == APP_ID]
    if not panels:
        return 0
    store, data = _marks_storage(doc)
    keys = {}
    for ds in panels:
        keys[ds.Id] = _mark_key(revit_params.get_values(ds))
    new = sorted(set(k for k in keys.values() if k not in data["map"]))
    for k in new:
        data["map"][k] = u"{}{:02d}".format(MARK_PREFIX, data["next"])
        data["next"] += 1
    for ds in panels:
        mark = data["map"][keys[ds.Id]]
        p = ds.LookupParameter("CT_PanelType")
        if p is not None and not p.IsReadOnly and p.AsString() != mark:
            p.Set(mark)
    if new or store is None:
        if store is None:
            store = DataStorage.Create(doc)
        _store(store, profiles.dumps(data))
    return len(set(keys.values()))


# --------------------------------------------------------------------------
# Fingerprints: rebuild only panels whose shape inputs changed
# --------------------------------------------------------------------------
FINGERPRINT_VERSION = "1"


def _r(x, step=0.1):
    return round(float(x) / step) * step


def _trim_context(frame):
    """(hole boxes, outline points) of a face in its (u, v) frame, cached."""
    ctx = getattr(frame, "_trim_ctx", None)
    if ctx is not None:
        return ctx
    loops = []
    for loop in frame.face.EdgeLoops:
        pts = []
        for edge in loop:
            for p in edge.Tessellate():
                if getattr(frame, "curved", False):
                    pts.append(frame.uv(p))
                else:
                    d = p - frame.origin
                    pts.append((d.DotProduct(frame.right) / MM, d.DotProduct(frame.up) / MM))
        loops.append(pts)

    def box(pts):
        us, vs = [p[0] for p in pts], [p[1] for p in pts]
        return (min(us), max(us), min(vs), max(vs))

    loops.sort(key=lambda pts: (box(pts)[1] - box(pts)[0]) * (box(pts)[3] - box(pts)[2]),
               reverse=True)
    outline = loops[0] if loops else []
    holes = [box(pts) for pts in loops[1:]]
    ctx = (holes, outline)
    try:
        frame._trim_ctx = ctx
    except Exception:
        pass
    return ctx


def _overlaps(a, b, pad=1.0):
    return not (a[1] < b[0] - pad or b[1] < a[0] - pad or a[3] < b[2] - pad or b[3] < a[2] - pad)


def fingerprint(frame, panel, recipe, material_name):
    """Text that changes whenever anything that shapes this panel changes:
    where the wall is, the panel's size and position, profile, standoff,
    material, perforation, overhangs, corner extensions and the openings or
    wall edges it touches. Equal fingerprint = geometry can be kept."""
    import hashlib
    from cladding import perforation
    if recipe["orientation"] == "horizontal":
        rect = (panel["u0"], panel["u0"] + panel["length"],
                panel["v0"], panel["v0"] + panel["width"])
    else:
        rect = (panel["u0"], panel["u0"] + panel["width"],
                panel["v0"], panel["v0"] + panel["length"])
    holes, outline = _trim_context(frame)
    prof = recipe["profile"]
    parts = [
        FINGERPRINT_VERSION,
        "curved" if getattr(frame, "curved", False) else "flat",
        [_r(c, 0.01) for c in ((frame.center.X, frame.center.Y, frame.radius,
                                frame.theta_ref, frame._z0, frame.s)
                               if getattr(frame, "curved", False) else
                               (frame.origin.X, frame.origin.Y, frame.origin.Z,
                                frame.normal.X, frame.normal.Y, frame.normal.Z,
                                frame.up.X, frame.up.Y, frame.up.Z))],
        [_r(x) for x in rect],
        recipe["orientation"],
        prof.get("name"), _r(prof.get("thickness", 0.7), 0.01),
        [[_r(x), _r(z)] for x, z in profiles.profile_points(prof)],
        _r(recipe["standoff"]), material_name or u"",
        perforation.label(recipe.get("perforation")),
        [[_r(x) for x in b] for b in panel.get("over", [])],
        [[_r(x) for x in b] for b in getattr(frame, "_boxes", []) if _overlaps(b, rect)],
        [[_r(x, 0.5) for x in h] for h in holes if _overlaps(h, rect)],
        [[_r(u, 0.5), _r(v, 0.5)] for u, v in outline
         if rect[0] - 1 <= u <= rect[1] + 1 and rect[2] - 1 <= v <= rect[3] + 1],
        # the wall's size only matters to panels that reach its edge
        [_r(x) for x in frame.extents] if (
            rect[0] <= frame.extents[0] + 1 or rect[1] >= frame.extents[1] - 1 or
            rect[2] <= frame.extents[2] + 1 or rect[3] >= frame.extents[3] - 1) else "inside",
    ]
    text = profiles.dumps(parts)
    try:
        return hashlib.md5(text.encode("utf-8") if not isinstance(text, str)
                           else text).hexdigest()
    except Exception:
        return text


def _store_if_changed(el, payload):
    try:
        if _read(el) == profiles.loads(payload):
            return
    except Exception:
        pass
    _store(el, payload)


def face_frames(doc, faces, group):
    """FaceFrames for a cladding's faces with corner joints applied: trimmed
    corners (Trim Corner) first, automatic corners for the other ends.
    Returns (frames, number of corner adjustments)."""
    frames, clad, corners = [], None, 0
    joints = load_joints(doc)
    for fi, (ref, face) in enumerate(faces):
        frame = make_frame(face)
        done = set()
        if not frame.curved:
            for side, mm in trim_extensions(doc, group, fi, frame, joints).items():
                frame.extend(side, mm)
                done.add(side)
                corners += 1
        if group["recipe"].get("corners", True) and not frame.curved:
            if clad is None:
                clad = _clad_faces(doc, group)
            for side, mm in corner_extensions(frame, group["faces"][fi],
                                              None, clad).items():
                if side in done:
                    continue
                frame.extend(side, mm)
                corners += 1
        frames.append(frame)
    return frames, corners


# --------------------------------------------------------------------------
# Trimmed corners (Trim Corner button)
# --------------------------------------------------------------------------
# A joint: {"a": end, "b": end, "lap": "a" | "b"}, end = {"group", "face",
# "key", "side"}: a cladding face, the panel picked at the corner (its
# standoff/profile set the sizes) and which end of the face (-1 / +1).
def load_joints(doc):
    for ds in FilteredElementCollector(doc).OfClass(DataStorage):
        try:
            data = _read(ds)
        except Exception:
            continue
        if data and data.get("kind") == "joints":
            return data.get("joints", [])
    return []


def save_joints(doc, joints):
    for ds in FilteredElementCollector(doc).OfClass(DataStorage):
        try:
            data = _read(ds)
        except Exception:
            continue
        if data and data.get("kind") == "joints":
            _store(ds, profiles.dumps({"kind": "joints", "joints": joints}))
            return
    _store(DataStorage.Create(doc), profiles.dumps({"kind": "joints", "joints": joints}))


def _same_end(e, f):
    return e["group"] == f["group"] and e["face"] == f["face"] and e["side"] == f["side"]


def set_joint(doc, a, b, lap):
    """Add or replace the joint between ends a and b. lap None removes it."""
    joints = [j for j in load_joints(doc)
              if not any(_same_end(j[x], e) for x in ("a", "b") for e in (a, b))]
    if lap:
        joints.append({"a": a, "b": b, "lap": lap})
    save_joints(doc, joints)


def _end_sizes(doc, end, current_group):
    """(standoff, outer surface) in mm of the panel stored for a joint end."""
    from cladding import flashing
    g = current_group if end["group"] == current_group["group"] \
        else load_group(doc, end["group"])
    if g is None:
        return None
    st = groupdata.panel_settings(g, end["key"])
    r = dict(g["recipe"])
    r.update(st)
    return float(st["standoff"]), flashing.cladding_surface(r)


def _end_face(doc, end, current_group):
    g = current_group if end["group"] == current_group["group"] \
        else load_group(doc, end["group"])
    try:
        ref = Reference.ParseFromStableRepresentation(doc, g["faces"][end["face"]])
        return doc.GetElement(ref.ElementId).GetGeometryObjectFromReference(ref)
    except Exception:
        return None


def trim_extensions(doc, group, fi, frame, joints):
    """{side: mm} for ends of this face that are in a trimmed joint.

    Outside corner: the lapping side runs past the corner by the other
    side's outer surface (covering its end); the other side runs past the
    corner by the lapping side's standoff, so it tucks in under the lapping
    sheet and no gap shows. Inside corner: the lapping side runs into the
    corner, the other stops at the lapping side's outer surface.
    """
    out = {}
    for j in joints:
        for me, other in (("a", "b"), ("b", "a")):
            end = j[me]
            if end["group"] != group["group"] or end["face"] != fi:
                continue
            f = _end_face(doc, j[other], group)
            mine = _end_sizes(doc, end, group)
            theirs = _end_sizes(doc, j[other], group)
            if f is None or mine is None or theirs is None or not isinstance(f, PlanarFace):
                continue
            rel = _corner(frame, end["side"], f)
            if rel is None:
                continue                      # the walls moved apart
            gap, turn = rel
            laps = j["lap"] == me
            if turn > 0:                      # outside corner
                ext = gap + (theirs[1] if laps else theirs[0])
            else:                             # inside corner
                ext = gap if laps else gap - theirs[1] - 1.0
            out[end["side"]] = ext
    return out


def joint_end(doc, panel_el):
    """(end dict without side, face, group) for a picked cladding panel."""
    g, pd = group_for_panel(doc, panel_el)
    if not g or not pd or "key" not in pd:
        raise ValueError("pick a panel of a cladding made with Apply Cladding (not a copy)")
    fi = int(pd["key"].split(":")[0])
    ref = Reference.ParseFromStableRepresentation(doc, g["faces"][fi])
    face = doc.GetElement(ref.ElementId).GetGeometryObjectFromReference(ref)
    if not isinstance(face, PlanarFace):
        raise ValueError("trimming corners works on flat faces only")
    return {"group": g["group"], "face": fi, "key": pd["key"]}, face, g


# --------------------------------------------------------------------------
# Copied panels ("loose" panels)
# --------------------------------------------------------------------------
# A panel copied with Copy/Ctrl+C keeps the original's stored data. Such
# copies are detached from the cladding: they stay where they were put, get
# their own data, and can be edited on their own (they no longer follow the
# cladding's layout, and edits on them don't touch the original).
COPY_SUFFIX = ":copy"


def _xyz(p):
    return [p.X, p.Y, p.Z]


def _pt(a):
    return XYZ(a[0], a[1], a[2])


def centroid(el):
    """Centroid of an element's solid geometry, or None."""
    from Autodesk.Revit.DB import Options, Solid, GeometryInstance
    pts, vols = [], 0.0
    geo = el.get_Geometry(Options())
    if geo is None:
        return None
    for g in geo:
        solids = [g] if isinstance(g, Solid) else (
            list(g.GetInstanceGeometry()) if isinstance(g, GeometryInstance) else [])
        for so in solids:
            if isinstance(so, Solid) and so.Volume > 1e-9:
                c = so.ComputeCentroid()
                pts.append((c, so.Volume))
                vols += so.Volume
    if not pts or vols <= 0:
        return None
    return XYZ(sum(c.X * v for c, v in pts) / vols, sum(c.Y * v for c, v in pts) / vols,
               sum(c.Z * v for c, v in pts) / vols)


class StoredFrame(object):
    """The bits of a FaceFrame a loose panel needs, rebuilt from stored data."""

    def __init__(self, d):
        self.origin, self.right = _pt(d["o"]), _pt(d["r"])
        self.up, self.normal = _pt(d["u"]), _pt(d["n"])

    def point(self, u_mm, v_mm, n_mm=0.0):
        return (self.origin + self.right.Multiply(u_mm * MM)
                + self.up.Multiply(v_mm * MM) + self.normal.Multiply(n_mm * MM))


class StoredCurvedFrame(object):
    """A CurvedFaceFrame's mapping, rebuilt from stored data (copied panels)."""
    curved = True

    def __init__(self, d):
        import math
        self._math = math
        self.center, self.ex, self.ey = _pt(d["c"]), _pt(d["ex"]), _pt(d["ey"])
        self.radius, self.s = d["radius"], d["s"]
        self.theta_ref, self._z0 = d["theta"], d["z0"]
        self.up = XYZ.BasisZ

    def _radial(self, u_mm):
        a = self.theta_ref + self.s * (u_mm * MM) / self.radius
        return self.ex.Multiply(self._math.cos(a)).Add(self.ey.Multiply(self._math.sin(a)))

    def point(self, u_mm, v_mm, n_mm=0.0):
        r = self.radius + self.s * n_mm * MM
        return (self.center.Add(self._radial(u_mm).Multiply(r))
                .Add(XYZ(0, 0, self._z0 + v_mm * MM)))


def frame_data(frame):
    if getattr(frame, "curved", False):
        return {"type": "curved", "c": _xyz(frame.center), "ex": _xyz(frame.ex),
                "ey": _xyz(frame.ey), "radius": frame.radius, "s": frame.s,
                "theta": frame.theta_ref, "z0": frame._z0}
    return {"o": _xyz(frame.origin), "r": _xyz(frame.right),
            "u": _xyz(frame.up), "n": _xyz(frame.normal)}


def stored_frame(d):
    return StoredCurvedFrame(d) if d.get("type") == "curved" else StoredFrame(d)


def _int_id(el):
    try:
        return int(el.Id.ToString())
    except Exception:
        return 0


def detach_copies(doc, group, faces=None):
    """Find copies among a cladding's panels and turn them into loose panels.
    Returns how many were detached."""
    gid = group["group"]
    by_key = {}
    for ds in group_panels(doc, gid):
        try:
            pd = _read(ds)
        except Exception:
            pd = None
        if pd and pd.get("key"):
            by_key.setdefault(pd["key"], []).append((ds, pd))
    copies = []
    for k, items in by_key.items():
        own = [x for x in items if x[1].get("uid") == x[0].UniqueId]
        original = own[0] if own else sorted(items, key=lambda x: _int_id(x[0]))[0]
        for x in items:
            if x[0].Id != original[0].Id:
                copies.append((x[0], x[1], original[0]))
    if not copies:
        return 0

    placed = {}
    try:
        faces = faces or resolve_faces(doc, group["faces"])
        frames, _ = face_frames(doc, faces, group)
        for fi, frame in enumerate(frames):
            lay = group["layouts"][fi] if fi < len(group["layouts"]) else None
            panels, _ = groupdata.place(frame.extents, lay, group["recipe"],
                                        groupdata.piece_widths(group, fi))
            for p in panels:
                placed[groupdata.key(fi, p["col"], p["row"])] = (frame, p)
    except Exception:
        pass

    for ds, pd, original in copies:
        k = pd["key"]
        recipe = groupdata.panel_recipe(group, k)
        recipe = dict((a, b) for a, b in recipe.items() if a in (
            "profile", "material", "standoff", "orientation", "perforation"))
        data = {"kind": "loose", "version": groupdata.VERSION, "group": gid,
                "from_key": k, "uid": ds.UniqueId, "recipe": recipe}
        c_copy, c_orig = centroid(ds), centroid(original)
        if k in placed and c_copy is not None and c_orig is not None:
            frame, p = placed[k]
            data["frame"] = frame_data(frame)
            data["panel"] = {"u0": p["u0"], "v0": p["v0"],
                             "width": round(pd.get("width", p["width"]), 2),
                             "length": round(pd.get("length", p["length"]), 2)}
            data["shift"] = _xyz(c_copy - c_orig)
            data["c"] = _xyz(c_copy)
        ds.ApplicationDataId = gid + COPY_SUFFIX
        _store(ds, profiles.dumps(data))
        revit_params.set_values(ds, {"CT_Group": gid + u" (copies)",
                                     "CT_PanelId": u"copy of " + groupdata.panel_label(
                                         *[int(x) for x in k.split(":")])})
    return len(copies)


def loose_expected(data):
    p = data.get("panel") or {}
    r = data["recipe"]
    from cladding import perforation
    q = profiles.panel_quantities(r["profile"], p.get("width", 0) or 1, p.get("length", 0) or 1)
    q["weight_kg"] *= 1.0 - perforation.open_area(r.get("perforation"))
    return {
        "CT_Perforation": perforation.label(r.get("perforation")) or u"none",
        "CT_OpenArea": round(perforation.open_area(r.get("perforation")) * 100, 1),
        "CT_PanelWidth": float(p.get("width", 0)),
        "CT_PanelLength": float(p.get("length", 0)),
        "CT_Profile": r["profile"].get("name", "Custom"),
        "CT_Material": r.get("material") or u"",
        "CT_Standoff": float(r.get("standoff", 30)),
        "CT_Thickness": float(r["profile"].get("thickness", 0.7)),
        "CT_CoverArea": q["cover_area_m2"],
        "CT_DevelopedArea": q["developed_area_m2"],
        "CT_Weight": round(q["weight_kg"], 2),
    }


def rebuild_loose(doc, ds, data):
    """Rebuild a loose panel's geometry where it currently is."""
    from Autodesk.Revit.DB import SolidUtils
    frame = stored_frame(data["frame"])
    r = data["recipe"]
    mat = panel_material(doc, r.get("material") or u"", r.get("perforation"), {})
    solid = panel_solid(frame, data["panel"], r, mat)
    shift = _pt(data.get("shift", [0, 0, 0]))
    now = centroid(ds)
    if now is not None and data.get("c"):
        shift = shift + (now - _pt(data["c"]))       # moved since last build
    solid = SolidUtils.CreateTransformed(solid, Transform.CreateTranslation(shift))
    shapes = List[GeometryObject]()
    shapes.Add(solid)
    ds.SetShape(shapes)
    t = panel_type(doc, r["profile"].get("name", "Custom"), r.get("material") or u"",
                   perf=r.get("perforation"))
    if ds.GetTypeId() != t.Id:
        ds.SetTypeId(t.Id)
    data["shift"] = _xyz(shift)
    data["c"] = _xyz(solid.ComputeCentroid())
    data["uid"] = ds.UniqueId
    _store(ds, profiles.dumps(data))
    revit_params.set_values(ds, loose_expected(data))
    revit_params.set_values(ds, {"CT_Cut": u""})
    number_panels(doc, [ds])


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

    summary_copies = detach_copies(doc, group, faces)
    existing, stray = {}, []
    for ds in group_panels(doc, gid):
        try:
            pd = _read(ds)
        except Exception:
            pd = None
        if pd and pd.get("key") and pd["key"] not in existing:
            existing[pd["key"]] = (ds, pd)
        else:
            stray.append(ds)              # older version, or a duplicate

    mat_cache, type_cache, missing = {}, {}, set()
    summary = {"created": 0, "updated": 0, "unchanged": 0, "removed": 0, "skipped": 0,
               "trimmed": 0,
               "missing_materials": [],
               "totals": {"cover_area_m2": 0.0, "developed_area_m2": 0.0, "weight_kg": 0.0,
                          "total_length_m": 0.0, "count": 0}}
    layouts = []

    built = []
    frames, corners = face_frames(doc, faces, group)
    for fi, (ref, face) in enumerate(faces):
        frame = frames[fi]
        lay = group["layouts"][fi] if fi < len(group["layouts"]) else None
        panels, placed_layout = groupdata.place(frame.extents, lay, group["recipe"],
                                                groupdata.piece_widths(group, fi))
        layouts.append(placed_layout)
        for panel in panels:
            k = groupdata.key(fi, panel["col"], panel["row"])
            recipe = groupdata.panel_recipe(group, k)
            mname = recipe["material"] or u""
            perf = recipe.get("perforation")
            mat = panel_material(doc, mname, perf, mat_cache, missing)
            sig = fingerprint(frame, panel, recipe, mname)
            entry = existing.get(k)
            same = entry is not None and entry[1].get("sig") == sig and "kept" in entry[1]
            if same:
                # nothing that shapes this panel changed: keep its geometry
                ds, kept = entry[0], float(entry[1]["kept"])
                existing.pop(k)
                summary["unchanged"] += 1
            else:
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
                entry = existing.pop(k, None)
                if entry is None:
                    ds = DirectShape.CreateElement(
                        doc, ElementId(BuiltInCategory.OST_GenericModel))
                    ds.ApplicationId = APP_ID
                    ds.ApplicationDataId = gid
                    summary["created"] += 1
                else:
                    ds = entry[0]
                    summary["updated"] += 1
                shapes = List[GeometryObject]()
                shapes.Add(solid)
                ds.SetShape(shapes)
            if kept < 0.999:
                summary["trimmed"] += 1
            built.append(ds)

            values = groupdata.expected_values(group, k, panel["width"], panel["length"])
            ptype = panel_type(doc, values["CT_Profile"], mname, type_cache, perf)
            if ds.GetTypeId() != ptype.Id:
                ds.SetTypeId(ptype.Id)

            for q in ("CT_CoverArea", "CT_DevelopedArea", "CT_Weight"):
                values[q] *= kept                # cut-outs reduce the quantities
            values["CT_Weight"] = round(values["CT_Weight"], 2)
            values["CT_Cut"] = u"Cut" if kept < 0.999 else u""
            revit_params.set_values(ds, values)
            _write_comment(ds, gid, values["CT_PanelId"], values["CT_Profile"], panel)
            _store_if_changed(ds, profiles.dumps({
                "kind": "panel", "version": groupdata.VERSION, "group": gid, "key": k,
                "uid": ds.UniqueId, "sig": sig,
                "width": round(panel["width"], 2), "length": round(panel["length"], 2),
                "kept": round(kept, 4)}))

            t = summary["totals"]
            t["count"] += 1
            t["total_length_m"] += panel["length"] / 1000.0
            t["cover_area_m2"] += values["CT_CoverArea"]
            t["developed_area_m2"] += values["CT_DevelopedArea"]
            t["weight_kg"] += values["CT_Weight"]

    leftovers = List[ElementId]()
    for ds in [e[0] for e in existing.values()] + stray:
        leftovers.Add(ds.Id)
    if leftovers.Count:
        doc.Delete(leftovers)
    summary["removed"] = leftovers.Count
    summary["copies"] = summary_copies

    group["layouts"] = layouts
    save_group(doc, group)
    summary["missing_materials"] = sorted(missing)
    summary["params"] = param_report
    summary["marks"] = number_panels(doc, built)
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
        text = u"Cladding {} | {} | {} | {:.0f} x {:.0f} mm".format(
            group_id, panel_id, profile_name, panel["width"], panel["length"])
        if c.AsString() != text:
            c.Set(text)


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
    if not panel or panel.get("kind") == "loose":
        return None, None
    group = load_group(doc, panel["group"])
    if group is None and "recipe" in panel:
        group = groupdata.from_legacy(panel)
    return group, panel
