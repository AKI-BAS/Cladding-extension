# -*- coding: utf-8 -*-
"""Profile maths for CladdingTools.

Pure Python (no Revit imports) so it runs in pyRevit's IronPython and can be
tested outside Revit. All values are millimetres.

A profile is a 2D polyline across ONE repeat module:
  x = across the panel width (0 .. module width)
  z = depth out of the wall (0 = back of the profile)

Profile files (library/profiles/*.json) describe it either as
  "pattern": a list of segments (flat, zig, sine, trapezoid, seam), or
  "points":  a raw list of [x, z] pairs (what Save Profile writes).
"""
import io
import json
import math
import os

SEGMENT_TYPES = ("flat", "zig", "sine", "trapezoid", "seam")


# --------------------------------------------------------------------------
# JSON that survives non-ASCII text in IronPython
# --------------------------------------------------------------------------
# IronPython 2.7's json encoder crashes on characters like á, æ, ð, þ
# (e.g. a material called "Stál"). Swap them for ASCII tokens before
# encoding, then turn the tokens into standard \uXXXX escapes.
_TOKEN = "@@CT{:04x}@@"


def _protect(obj):
    if isinstance(obj, dict):
        return dict((_protect(k), _protect(v)) for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return [_protect(v) for v in obj]
    if isinstance(obj, basestring_types):
        return u"".join(c if ord(c) < 128 else _TOKEN.format(ord(c)) for c in _text(obj))
    return obj


def _text(s):
    if isinstance(s, bytes) and not isinstance(s, str):  # CPython 3 bytes
        return s.decode("utf-8")
    try:
        return unicode(s)  # noqa: F821  (IronPython / Py2)
    except NameError:
        return s
    except UnicodeDecodeError:
        return s.decode("utf-8")


try:
    basestring_types = (basestring,)  # noqa: F821
except NameError:
    basestring_types = (str,)


def dumps(obj, **kw):
    text = json.dumps(_protect(obj), **kw)
    i = text.find("@@CT")
    while i >= 0:
        code = text[i + 4:i + 8]
        text = text[:i] + "\\u" + code + text[i + 10:]
        i = text.find("@@CT", i)
    return str(text)


def loads(text):
    return json.loads(text)


def read_json(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return json.loads(f.read())


def write_json(path, obj):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(u"" + dumps(obj, indent=2))


# --------------------------------------------------------------------------
# Library
# --------------------------------------------------------------------------
def library_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "library", "profiles"))


def load_library(folder=None):
    folder = folder or library_dir()
    profiles = []
    if not os.path.isdir(folder):
        return profiles
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".json"):
            continue
        path = os.path.join(folder, name)
        try:
            data = read_json(path)
            data["_file"] = path
            profile_points(data)  # validate early
            profiles.append(data)
        except Exception as ex:
            print("Skipped profile {}: {}".format(name, ex))
    return profiles


def save_profile(profile, folder=None):
    folder = folder or library_dir()
    if not os.path.isdir(folder):
        os.makedirs(folder)
    slug = "".join(c if (c.isalnum() and ord(c) < 128) else "-"
                   for c in profile["name"].lower()).strip("-") or "profile"
    path = os.path.join(folder, slug + ".json")
    clean = dict((k, v) for k, v in profile.items() if not k.startswith("_"))
    write_json(path, clean)
    return path


# --------------------------------------------------------------------------
# Pattern -> points
# --------------------------------------------------------------------------
def pattern_to_points(pattern, samples=10):
    pts = [(0.0, 0.0)]
    x = 0.0
    for seg in pattern:
        t = seg.get("type", "flat")
        w = float(seg.get("width", 0))
        d = float(seg.get("depth", 0))
        if w <= 0:
            raise ValueError("segment width must be > 0")
        if t == "flat":
            pts.append((x + w, 0.0))
        elif t == "zig":
            pts.append((x + w / 2.0, d))
            pts.append((x + w, 0.0))
        elif t == "sine":
            for i in range(1, samples + 1):
                a = 2.0 * math.pi * i / samples
                pts.append((x + w * i / samples, d * (1.0 - math.cos(a)) / 2.0))
        elif t == "trapezoid":
            top = float(seg.get("top", w * 0.25))
            bottom = float(seg.get("bottom", w * 0.25))
            slope = (w - top - bottom) / 2.0
            if slope < 0:
                raise ValueError("trapezoid top + bottom wider than segment")
            pts.append((x + bottom / 2.0, 0.0))
            pts.append((x + bottom / 2.0 + slope, d))
            pts.append((x + bottom / 2.0 + slope + top, d))
            pts.append((x + w - bottom / 2.0, 0.0))
            pts.append((x + w, 0.0))
        elif t == "seam":
            seam = float(seg.get("seam", 25))
            pts.append((x + w - seam, 0.0))
            pts.append((x + w - seam, d))
            pts.append((x + w, d))
            pts.append((x + w, 0.0))
        else:
            raise ValueError("unknown segment type '{}'".format(t))
        x += w
    return _clean(pts)


def profile_points(profile):
    """One module of the profile as [(x, z), ...] in mm."""
    if "points" in profile:
        pts = [(float(p[0]), float(p[1])) for p in profile["points"]]
    else:
        pts = pattern_to_points(profile.get("pattern", []))
    pts = _clean(pts)
    if len(pts) < 2:
        raise ValueError("profile needs at least two points")
    if pts[-1][0] <= pts[0][0]:
        raise ValueError("profile must run left to right")
    return pts


def _clean(pts, tol=0.01):
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol:
            out.append(p)
    # drop collinear middle points
    i = 1
    while i < len(out) - 1:
        a, b, c = out[i - 1], out[i], out[i + 1]
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(cross) < tol:
            out.pop(i)
        else:
            i += 1
    return out


def module_width(points):
    return points[-1][0] - points[0][0]


def depth(points):
    zs = [p[1] for p in points]
    return max(zs) - min(zs)


def polyline_length(points):
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += math.hypot(b[0] - a[0], b[1] - a[1])
    return total


# --------------------------------------------------------------------------
# Tiling and clipping across a panel
# --------------------------------------------------------------------------
def tile(points, width):
    """Repeat a module across `width` mm and clip at the panel edge."""
    m = module_width(points)
    x0 = points[0][0]
    base = [(p[0] - x0, p[1]) for p in points]
    out = []
    n = int(math.ceil(width / m)) + 1
    for k in range(n):
        for i, p in enumerate(base):
            if k > 0 and i == 0:
                continue
            out.append((p[0] + k * m, p[1]))
    return clip(out, 0.0, width)


def clip(points, x_min, x_max):
    # Profiles run left to right (x never decreases), so walking the
    # segments in order keeps the output in order.
    out = []
    for a, b in zip(points, points[1:]):
        if x_min <= a[0] <= x_max:
            out.append(a)
        for edge in (x_min, x_max):
            if (a[0] - edge) * (b[0] - edge) < 0:
                t = (edge - a[0]) / (b[0] - a[0])
                out.append((edge, a[1] + t * (b[1] - a[1])))
    if x_min <= points[-1][0] <= x_max:
        out.append(points[-1])
    return _clean(out)


# --------------------------------------------------------------------------
# Sheet thickness: offset the polyline to the back side (mitred joins)
# --------------------------------------------------------------------------
def offset(points, dist, miter_limit=4.0):
    """Offset to the back (-normal) side by `dist` mm."""
    normals = []
    for a, b in zip(points, points[1:]):
        dx, dz = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dz) or 1.0
        normals.append((dz / L, -dx / L))  # right-hand normal = back side
    out = []
    for i, p in enumerate(points):
        if i == 0:
            n = normals[0]
            s = dist
        elif i == len(points) - 1:
            n = normals[-1]
            s = dist
        else:
            n1, n2 = normals[i - 1], normals[i]
            nx, nz = n1[0] + n2[0], n1[1] + n2[1]
            L = math.hypot(nx, nz)
            if L < 1e-9:
                n, s = n1, dist
            else:
                n = (nx / L, nz / L)
                cos_half = n[0] * n1[0] + n[1] * n1[1]
                s = dist / max(cos_half, 1.0 / miter_limit)
        out.append((p[0] + n[0] * s, p[1] + n[1] * s))
    return out


def section_loop(points, thickness):
    """Closed outline of the sheet section: front line + reversed back line."""
    back = offset(points, thickness)
    return list(points) + list(reversed(back))


# --------------------------------------------------------------------------
# Quantities
# --------------------------------------------------------------------------
STEEL_DENSITY = 7850.0  # kg/m3


def panel_quantities(profile, cover_width_mm, length_mm):
    pts = tile(profile_points(profile), cover_width_mm)
    dev = polyline_length(pts)
    t = float(profile.get("thickness", 0.7))
    density = float(profile.get("density", STEEL_DENSITY))
    cover_area = cover_width_mm * length_mm / 1e6
    developed_area = dev * length_mm / 1e6
    return {
        "cover_area_m2": cover_area,
        "developed_area_m2": developed_area,
        "weight_kg": developed_area * t / 1000.0 * density,
    }
