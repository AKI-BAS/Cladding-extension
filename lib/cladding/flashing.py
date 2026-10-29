# -*- coding: utf-8 -*-
"""Flashings around doors and windows: sections, placement and quantities.

Pure Python (no Revit imports) so it can be tested outside Revit. mm.

A flashing type (library/flashings/*.json):
  {"name": "Angle trim 60", "face_width": 60, "kick": 0,
   "return": true, "thickness": 0.7}
  face_width  how far it laps over the cladding from the opening edge
  kick        turn-down at the outer edge of the face leg (0 = none)
  return      a leg from the cladding surface back to the wall at the
              opening edge (covers the cut cladding edge)

Sections are drawn in (x, z): x = 0 at the opening edge, positive away from
the opening (over the cladding); z = 0 at the wall face, positive outward.

Placement: the head runs the full width plus both laps and sits over the
jambs, jambs run sill-to-head, the sill (when there is one) runs full width
plus laps under the jambs. That's how trims lap on site, and nothing
overlaps.
"""
import io
import json
import os

from cladding import profiles

SIDES = ("head", "left", "right", "sill")
FLOOR_LEVEL = 150.0      # mm: windows this close to the bottom of the wall get no sill
GAP = 1.0                # mm between cladding surface and flashing


def library_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "library", "flashings"))


def load_library(folder=None):
    folder = folder or library_dir()
    out = []
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith(".json"):
            try:
                out.append(profiles.read_json(os.path.join(folder, name)))
            except Exception as ex:
                print("Skipped flashing {}: {}".format(name, ex))
    return out


def sides_for(kind, opening, face_extents, include_sill=True):
    """Which sides get flashing. Doors never get a sill; windows get one
    unless they sit at floor level (bottom within FLOOR_LEVEL of the wall)."""
    sides = ["head", "left", "right"]
    if include_sill and kind == "window":
        if opening[2] - face_extents[2] > FLOOR_LEVEL:
            sides.append("sill")
    return sides


def parts(ftype, surface):
    """Polylines of a flashing section: [("face", pts), ("return", pts)].

    surface = distance from the wall face to the outside of the cladding.
    """
    t = max(float(ftype.get("thickness", 0.7)), 1.5)
    w = float(ftype.get("face_width", 60))
    k = float(ftype.get("kick", 0))
    z = surface + GAP + t                 # top of the face leg; its underside rests on the cladding
    face = [(0.0, z), (w, z)]
    if k > 0:
        face.append((w, z - k))
    out = [("face", face)]
    if ftype.get("return", True) and z > 2.0:
        # drawn top-down so its thickness falls inside the opening reveal
        out.append(("return", [(0.0, z), (0.0, 0.0)]))
    return out


def loops(ftype, surface):
    """Closed section outlines {part: [(x, z), ...]} with sheet thickness."""
    t = max(float(ftype.get("thickness", 0.7)), 1.5)
    return dict((name, profiles.section_loop(pts, t)) for name, pts in parts(ftype, surface))


def placements(opening, side, ftype):
    """Where each part of one side goes, in the face's (u, v) frame.

    opening = (u0, u1, v0, v1). Returns {part: (start_u, start_v, run_u,
    run_v, out_u, out_v, length)}: the section is placed at start, x runs
    along out, and the part is extruded along run for length.
    """
    u0, u1, v0, v1 = opening
    w = float(ftype.get("face_width", 60))
    if side == "head":
        return {"face": (u0 - w, v1, 1, 0, 0, 1, (u1 - u0) + 2 * w),
                "return": (u0, v1, 1, 0, 0, 1, u1 - u0)}
    if side == "sill":
        return {"face": (u0 - w, v0, 1, 0, 0, -1, (u1 - u0) + 2 * w),
                "return": (u0, v0, 1, 0, 0, -1, u1 - u0)}
    if side == "left":
        return {"face": (u0, v0, 0, 1, -1, 0, v1 - v0),
                "return": (u0, v0, 0, 1, -1, 0, v1 - v0)}
    if side == "right":
        return {"face": (u1, v0, 0, 1, 1, 0, v1 - v0),
                "return": (u1, v0, 0, 1, 1, 0, v1 - v0)}
    raise ValueError("unknown side " + side)


def quantities(ftype, surface, opening, side):
    """Run length (mm), developed sheet area (m2) and weight (kg) of one side."""
    secs = dict(parts(ftype, surface))
    place = placements(opening, side, ftype)
    area = 0.0
    for name, pts in secs.items():
        area += profiles.polyline_length(pts) * place[name][6] / 1e6
    t = float(ftype.get("thickness", 0.7))
    density = float(ftype.get("density", profiles.STEEL_DENSITY))
    return {"length_mm": place["face"][6],
            "developed_area_m2": area,
            "weight_kg": area * t / 1000.0 * density}


def cladding_surface(recipe):
    """Distance from the wall to the outside of the cladding (mm)."""
    prof = recipe["profile"]
    pts = profiles.profile_points(prof)
    t = max(float(prof.get("thickness", 0.7)), 1.5)
    return float(recipe.get("standoff", 30)) + t + profiles.depth(pts)
