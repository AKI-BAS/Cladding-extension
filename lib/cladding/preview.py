# -*- coding: utf-8 -*-
"""Small 3D preview for the dialog: a sample wall bay drawn in 2D.

Pure Python. Returns shaded polygons in screen space; the dialog turns them
into WPF Polygons. The same algorithm drives the phone preview page.
"""
import math

from cladding import profiles, layout

LIGHT = (-0.45, 0.55, 0.70)


def _norm(v):
    L = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / L for c in v)


def polygons(recipe, width_px, height_px, angle_deg=35.0,
             bay_width=2400.0, bay_height=1800.0):
    """List of (points[(x, y)...], shade 0..1) back to front."""
    r = layout.merged(recipe)
    panels = layout.layout((0.0, bay_width, 0.0, bay_height), r)
    t = float(r["profile"].get("thickness", 0.7))
    a = math.radians(angle_deg)
    tilt = math.radians(18.0)
    ca, sa, ct, st = math.cos(a), math.sin(a), math.cos(tilt), math.sin(tilt)
    light = _norm(LIGHT)
    exaggerate = 2.0  # deepen ribs a little so they read at thumbnail size

    def project(x, y, z):
        # x across, y up, z out of wall; centre the bay
        x -= bay_width / 2.0
        y -= bay_height / 2.0
        z *= exaggerate
        rx = x * ca + z * sa
        rz = -x * sa + z * ca
        ry = y * ct - rz * st
        dz = y * st + rz * ct
        return rx, ry, dz

    quads = []
    for p in panels:
        pts = profiles.tile(profiles.profile_points(r["profile"]), p["width"])
        horizontal = r["orientation"] == "horizontal"
        for (x1, z1), (x2, z2) in zip(pts, pts[1:]):
            if horizontal:
                c = [(p["u0"], p["v0"] + x1, z1), (p["u0"], p["v0"] + x2, z2),
                     (p["u0"] + p["length"], p["v0"] + x2, z2),
                     (p["u0"] + p["length"], p["v0"] + x1, z1)]
                n = (0.0, -(z2 - z1), (x2 - x1))
            else:
                c = [(p["u0"] + x1, p["v0"], z1), (p["u0"] + x2, p["v0"], z2),
                     (p["u0"] + x2, p["v0"] + p["length"], z2),
                     (p["u0"] + x1, p["v0"] + p["length"], z1)]
                n = (-(z2 - z1), 0.0, (x2 - x1))
            n = _norm((n[0], n[1], n[2] * 1.0))
            # rotate normal with the view
            nx = n[0] * ca + n[2] * sa
            nz = -n[0] * sa + n[2] * ca
            ny = n[1] * ct - nz * st
            nz2 = n[1] * st + nz * ct
            shade = max(0.0, nx * light[0] + ny * light[1] + nz2 * light[2])
            proj = [project(*q) for q in c]
            quads.append((sum(q[2] for q in proj) / 4.0, proj, shade))

    quads.sort(key=lambda q: q[0])
    xs = [pt[0] for _, pr, _ in quads for pt in pr] or [0]
    ys = [pt[1] for _, pr, _ in quads for pt in pr] or [0]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    scale = 0.82 * min(width_px, height_px) / span
    cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
    out = []
    for _, pr, shade in quads:
        screen = [(width_px / 2.0 + (x - cx) * scale,
                   height_px / 2.0 - (y - cy) * scale) for x, y, _ in pr]
        out.append((screen, 0.25 + 0.75 * shade))
    return out
