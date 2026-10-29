# -*- coding: utf-8 -*-
"""Panel layout on a face, in the face's own 2D frame (mm).

Pure Python so the dialog can count panels and quantities before anything is
built. `u` runs across the face (right), `v` runs up the face.
"""
import math

from cladding import profiles

DEFAULT_RECIPE = {
    "profile": None,           # profile dict (copied into the model)
    "orientation": "vertical",  # vertical: ribs run up the face
    "cover_width": 1000.0,     # mm, snapped to whole modules
    "max_length": 6000.0,      # mm, longer runs are split
    "joint": 10.0,             # mm gap between panels (end laps ignored in v1)
    "standoff": 30.0,          # mm from the host face to the back of the sheet
    "material": "",            # Revit material name, optional
}


def merged(recipe):
    out = dict(DEFAULT_RECIPE)
    out.update(recipe or {})
    return out


def snapped_cover_width(recipe):
    pts = profiles.profile_points(recipe["profile"])
    m = profiles.module_width(pts)
    n = max(1, int(round(float(recipe["cover_width"]) / m)))
    return n * m


def layout(extents, recipe):
    """extents = (u_min, u_max, v_min, v_max) in mm.

    Returns panels as dicts: u0, v0, width, length (mm), in face coordinates.
    Width is measured across the ribs, length along them.
    """
    r = merged(recipe)
    u_min, u_max, v_min, v_max = extents
    cover = snapped_cover_width(r)
    joint = float(r["joint"])
    max_len = float(r["max_length"])

    if r["orientation"] == "horizontal":
        across = (v_min, v_max)  # ribs run sideways, panels stack upwards
        along = (u_min, u_max)
    else:
        across = (u_min, u_max)
        along = (v_min, v_max)

    panels = []
    a = across[0]
    while a < across[1] - 1.0:
        w = min(cover, across[1] - a)
        run = along[1] - along[0]
        pieces = max(1, int(math.ceil((run + joint) / (max_len + joint))))
        piece_len = (run - joint * (pieces - 1)) / pieces
        for k in range(pieces):
            b = along[0] + k * (piece_len + joint)
            if r["orientation"] == "horizontal":
                panels.append({"u0": b, "v0": a, "width": w, "length": piece_len})
            else:
                panels.append({"u0": a, "v0": b, "width": w, "length": piece_len})
        a += w + joint
    return panels


def panel_center(panel, orientation):
    if orientation == "horizontal":
        return (panel["u0"] + panel["length"] / 2.0, panel["v0"] + panel["width"] / 2.0)
    return (panel["u0"] + panel["width"] / 2.0, panel["v0"] + panel["length"] / 2.0)


def totals(panels, recipe):
    r = merged(recipe)
    out = {"count": 0, "cover_area_m2": 0.0, "developed_area_m2": 0.0,
           "weight_kg": 0.0, "total_length_m": 0.0}
    for p in panels:
        q = profiles.panel_quantities(r["profile"], p["width"], p["length"])
        out["count"] += 1
        out["total_length_m"] += p["length"] / 1000.0
        for k in ("cover_area_m2", "developed_area_m2", "weight_kg"):
            out[k] += q[k]
    return out
