# -*- coding: utf-8 -*-
"""Perforated panels: the hole pattern, open area and naming.

Pure Python. Perforation is a material effect (fidelity level 1 from the
design notes): the panel geometry stays solid, and a perforated variant of
the panel material gets a cutout map (real see-through holes in Ray Trace
and render plugins such as Enscape/Twinmotion/D5) and a dot surface pattern
(holes shown in elevations and 3D views). Weight is reduced by the open area.

A perforation is {"d": hole diameter mm, "pitch": centre distance mm,
"pattern": "staggered" | "straight"} or None.
"""
import math
import re

from cladding.profiles import fold

PATTERNS = ("staggered", "straight")
NONE_WORDS = (u"", u"none", u"no", u"-", u"0", u"engin")
MARK = u" | perforated "


def normalize(perf):
    if not perf:
        return None
    d, p = float(perf["d"]), float(perf["pitch"])
    pattern = perf.get("pattern", "staggered")
    if pattern not in PATTERNS:
        raise ValueError(u"Pattern must be staggered or straight.")
    if d < 0.5:
        raise ValueError(u"Hole diameter must be at least 0.5 mm.")
    if p <= d + 0.3:
        raise ValueError(u"Pitch must be bigger than the hole diameter "
                         u"(hole {:g} mm, pitch {:g} mm).".format(d, p))
    return {"d": round(d, 2), "pitch": round(p, 2), "pattern": pattern}


def open_area(perf):
    """Open area as a fraction 0..1."""
    perf = normalize(perf)
    if not perf:
        return 0.0
    hole = math.pi * perf["d"] ** 2 / 4.0
    if perf["pattern"] == "straight":
        cell = perf["pitch"] ** 2
    else:                                  # 60 degree staggered
        cell = perf["pitch"] ** 2 * math.sqrt(3) / 2.0
    return min(0.9, hole / cell)


def label(perf):
    """'Ø5/8 staggered' (what CT_Perforation shows), '' for none."""
    perf = normalize(perf)
    if not perf:
        return u""
    return u"Ø{:g}/{:g} {}".format(perf["d"], perf["pitch"], perf["pattern"])


def describe(perf):
    perf = normalize(perf)
    if not perf:
        return u"not perforated"
    return u"{}, {:.0f}% open".format(label(perf), open_area(perf) * 100)


def parse(text):
    """'Ø5/8 staggered', '5/8', '5 8 straight', 'none' -> perforation or None."""
    t = fold(text)
    if t in NONE_WORDS:
        return None
    nums = re.findall(r"\d+(?:[.,]\d+)?", t)
    if len(nums) < 2:
        raise ValueError(u"Write the perforation as hole/pitch, e.g. 'Ø5/8 staggered' "
                         u"or 'none' (got '{}').".format(text))
    pattern = "straight" if ("straight" in t or "beint" in t or "lige" in t) else "staggered"
    return normalize({"d": float(nums[0].replace(",", ".")),
                      "pitch": float(nums[1].replace(",", ".")), "pattern": pattern})


def tile(perf):
    """Repeat tile for the cutout image: (width mm, height mm, [hole centres])."""
    perf = normalize(perf)
    p = perf["pitch"]
    if perf["pattern"] == "straight":
        return p, p, [(p / 2.0, p / 2.0)]
    h = p * math.sqrt(3)                   # two rows
    return p, h, [(p / 2.0, h / 4.0), (0.0, 3 * h / 4.0), (p, 3 * h / 4.0)]


def material_name(base, perf):
    """Name of the perforated variant of a material."""
    return u"{}{}{}".format(base or u"CladdingTools", MARK, label(perf))


def base_material(name):
    """'Stál | perforated Ø5/8 staggered' -> 'Stál'."""
    name = name or u""
    i = name.find(MARK)
    base = name[:i] if i >= 0 else name
    return u"" if base == u"CladdingTools" and i >= 0 else base


def file_stub(perf):
    perf = normalize(perf)
    return u"perf_d{:g}_p{:g}_{}".format(perf["d"], perf["pitch"], perf["pattern"]).replace(
        ".", "-")
