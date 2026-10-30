# -*- coding: utf-8 -*-
"""One cladding ("group"): its defaults, panel layout and per-panel edits.

Pure Python (no Revit imports) so it can be tested outside Revit.

Group data (stored once per cladding in the model):
  {
    "kind": "group", "version": 2, "group": "<id>", "faces": [stable refs],
    "recipe":   defaults (profile, cover_width, max_length, joint, standoff,
                orientation, material),
    "profiles": {name: profile dict}   every profile used, so the model is
                                       self-contained,
    "layouts":  per face {"cols": [{"width", "fixed", "pieces":
                                    [{"length", "fixed"}, ...]}, ...]},
    "overrides": {"face:col:row": {"profile": name, "material": str,
                                   "standoff": mm}},
  }

Columns ("strips") run across the face, pieces run along the ribs.
A fixed width / length was set by hand and is kept; the rest follow the
defaults. Everything is in mm.
"""
import copy
import math

from cladding import profiles, layout, inputs, perforation
from cladding.profiles import fold

VERSION = 2
MIN_STRIP = 50.0   # mm, narrower leftovers at the face edge are left out
MIN_PIECE = 50.0


# --------------------------------------------------------------------------
# Creating
# --------------------------------------------------------------------------
def new_group(group_id, recipe):
    r = layout.merged(recipe)
    prof = _clean_profile(r["profile"])
    r["profile"] = prof
    return {
        "kind": "group", "version": VERSION, "group": group_id, "faces": [],
        "recipe": r,
        "profiles": {prof["name"]: prof},
        "layouts": [],
        "overrides": {},
    }


def from_legacy(payload):
    """Group data from a v1 panel payload {"group", "faces", "recipe"}."""
    g = new_group(payload["group"], payload["recipe"])
    g["faces"] = list(payload.get("faces", []))
    return g


def _clean_profile(p):
    out = dict((k, v) for k, v in p.items() if not str(k).startswith("_"))
    out.setdefault("name", "Custom")
    return out


def key(face, col, row):
    return "{}:{}:{}".format(face, col, row)


def panel_label(face, col, row):
    return "F{} S{} P{}".format(face + 1, col + 1, row + 1)


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------
def _default_pieces(run, max_len, joint):
    if run <= 1.0:
        return []
    n = max(1, int(math.ceil((run + joint) / (max_len + joint))))
    piece = (run - joint * (n - 1)) / n
    return [round(piece, 2)] * n


def _pieces(run, joint, max_len, stored):
    """Requested piece lengths for one strip.

    Pieces up to the last hand-set one keep their lengths; everything after
    re-splits the rest of the run evenly (each <= max_len).
    """
    stored = stored or []
    fixed_idx = [i for i, p in enumerate(stored) if p.get("fixed")]
    head = stored[:fixed_idx[-1] + 1] if fixed_idx else []
    out, used = [], 0.0
    for p in head:
        if used >= run - 1.0:
            break
        out.append({"length": float(p["length"]), "fixed": bool(p.get("fixed"))})
        used += float(p["length"]) + joint
    for L in _default_pieces(run - used, max_len, joint):
        out.append({"length": L, "fixed": False})
    return out


def place(extents, face_layout, recipe):
    """Panels on one face, and the layout as placed.

    extents = (u_min, u_max, v_min, v_max) in the face's frame.
    Returns (panels, layout) where each panel is
    {u0, v0, width, length, col, row} (width across ribs, length along).
    """
    r = layout.merged(recipe)
    u_min, u_max, v_min, v_max = extents
    horizontal = r["orientation"] == "horizontal"
    across = (v_min, v_max) if horizontal else (u_min, u_max)
    along = (u_min, u_max) if horizontal else (v_min, v_max)
    # After Switch Cladding with "keep sizes", the old standard width is kept
    # exactly instead of snapping to the new profile's ribs.
    cover = float(r["cover_exact"]) if r.get("cover_exact") else layout.snapped_cover_width(r)
    joint = float(r["joint"])
    max_len = float(r["max_length"])
    cols_in = list((face_layout or {}).get("cols") or [])
    run = along[1] - along[0]

    cols_out, panels = [], []
    a, c = across[0], 0
    while a < across[1] - MIN_STRIP:
        spec = cols_in[c] if c < len(cols_in) else {}
        fixed_w = bool(spec.get("fixed"))
        req_w = float(spec["width"]) if fixed_w else cover
        # A width/length set by hand is kept even past the face edge (e.g. up
        # onto the wall above); defaults stop at the edge.
        w = req_w if fixed_w else min(req_w, across[1] - a)
        pieces = _pieces(run, joint, max_len, spec.get("pieces"))
        b = along[0]
        for k, p in enumerate(pieces):
            if b >= along[1] - MIN_PIECE:
                pieces = pieces[:k]
                break
            L = p["length"] if p.get("fixed") else min(p["length"], along[1] - b)
            over_along = max(0.0, b + L - along[1])
            over_across = max(0.0, a + w - across[1])
            if horizontal:
                panel = {"u0": b, "v0": a, "width": w, "length": L, "col": c, "row": k}
            else:
                panel = {"u0": a, "v0": b, "width": w, "length": L, "col": c, "row": k}
            panel["over"] = overshoot_boxes(panel, horizontal, over_along, over_across)
            panels.append(panel)
            b += L + joint
        cols_out.append({"width": req_w, "fixed": fixed_w, "pieces": pieces})
        a += w + joint
        c += 1
    return panels, {"cols": cols_out}


def overshoot_boxes(panel, horizontal, over_along, over_across):
    """Parts of a panel that reach past the face, as (u0, u1, v0, v1) boxes in
    the face frame. They are added to the trimming outline so the overhang
    isn't cut off."""
    u0, v0 = panel["u0"], panel["v0"]
    if horizontal:
        u1, v1 = u0 + panel["length"], v0 + panel["width"]
        along_box = (u1 - over_along - 1.0, u1, v0, v1)
        across_box = (u0, u1, v1 - over_across - 1.0, v1)
    else:
        u1, v1 = u0 + panel["width"], v0 + panel["length"]
        along_box = (u0, u1, v1 - over_along - 1.0, v1)
        across_box = (u1 - over_across - 1.0, u1, v0, v1)
    boxes = []
    if over_along > 0.5:
        boxes.append(along_box)
    if over_across > 0.5:
        boxes.append(across_box)
    return boxes


# --------------------------------------------------------------------------
# Per-panel settings
# --------------------------------------------------------------------------
def panel_settings(group, k):
    """Profile dict, material and standoff for panel key k."""
    r = group["recipe"]
    ov = group["overrides"].get(k, {})
    prof = r["profile"]
    if ov.get("profile"):
        prof = group["profiles"].get(ov["profile"], prof)
    return {
        "profile": prof,
        "material": ov["material"] if "material" in ov else (r["material"] or u""),
        "standoff": float(ov["standoff"]) if "standoff" in ov else float(r["standoff"]),
        "perforation": ov["perforation"] if "perforation" in ov else r.get("perforation"),
    }


def panel_recipe(group, k):
    """Full recipe for building one panel."""
    r = dict(group["recipe"])
    r.update(panel_settings(group, k))
    return r


def expected_values(group, k, width, length):
    """Every CT_ value a panel should show (inputs and outputs)."""
    r = group["recipe"]
    s = panel_settings(group, k)
    q = profiles.panel_quantities(s["profile"], width, length)
    q["weight_kg"] *= 1.0 - perforation.open_area(s["perforation"])
    face, col, row = [int(x) for x in k.split(":")]
    return {
        "CT_Perforation": perforation.label(s["perforation"]) or u"none",
        "CT_OpenArea": round(perforation.open_area(s["perforation"]) * 100, 1),
        "CT_PanelWidth": float(width),
        "CT_PanelLength": float(length),
        "CT_Profile": s["profile"].get("name", "Custom"),
        "CT_Material": s["material"],
        "CT_Standoff": s["standoff"],
        "CT_CoverWidth": float(r["cover_width"]),
        "CT_MaxLength": float(r["max_length"]),
        "CT_Joint": float(r["joint"]),
        "CT_Orientation": inputs.orientation_label(r["orientation"]),
        "CT_Group": group["group"],
        "CT_PanelId": panel_label(face, col, row),
        "CT_CoverArea": q["cover_area_m2"],
        "CT_DevelopedArea": q["developed_area_m2"],
        "CT_Weight": round(q["weight_kg"], 2),
        "CT_Thickness": float(s["profile"].get("thickness", 0.7)),
    }


def find_edits(current, expected):
    """{name: value} for inputs that were edited; True if outputs were typed over."""
    edits = {}
    for name in inputs.PANEL_NAMES + inputs.GROUP_NAMES:
        if inputs.differs(name, current.get(name), expected.get(name)):
            edits[name] = current[name]
    outputs_edited = any(inputs.differs(n, current.get(n), expected.get(n))
                         for n in inputs.OUTPUT_NAMES)
    return edits, outputs_edited


# --------------------------------------------------------------------------
# Applying edits
# --------------------------------------------------------------------------
def _find_profile(name, group, library):
    for p in list(group["profiles"].values()) + list(library):
        if fold(p.get("name")) == fold(name):
            return _clean_profile(p)
    names = ", ".join(sorted(set([p.get("name", "?") for p in library] +
                                 list(group["profiles"].keys()))))
    raise ValueError(u"No profile called '{}'. Available: {}".format(name.strip(), names))


def _module(profile):
    return profiles.module_width(profiles.profile_points(profile))


def _ensure_col(group, face, col):
    lay = group["layouts"]
    while len(lay) <= face:
        lay.append({"cols": []})
    cols = lay[face]["cols"]
    while len(cols) <= col:
        cols.append({"width": layout.snapped_cover_width(group["recipe"]),
                     "fixed": False, "pieces": []})
    return cols[col]


def apply_edits(group, panel_edits, panel_count, library):
    """Apply edits made in Properties.

    panel_edits: {panel key: {CT_ name: new value}} for the panels that changed.
    panel_count: number of panels in the cladding (to spot "all panels").
    Returns (new group, [change descriptions]). Raises ValueError with a
    message for the user; nothing is changed then.
    """
    g = copy.deepcopy(group)
    r = g["recipe"]
    changes = []
    if not panel_edits:
        return g, changes

    # ---- whole-cladding values (first panel that has one wins) ------------
    def first(name):
        for ed in panel_edits.values():
            if name in ed:
                return ed[name]
        return None

    o = first("CT_Orientation")
    if o is not None and inputs.orientation(o) != r["orientation"]:
        r["orientation"] = inputs.orientation(o)
        g["layouts"], g["overrides"] = [], {}
        changes.append(u"orientation → {} (panel edits reset)".format(r["orientation"]))
        return g, changes     # nothing else makes sense after a reset

    v = first("CT_Joint")
    if v is not None:
        r["joint"] = inputs.length("CT_Joint", v)
        changes.append(u"joint → {:g} mm".format(r["joint"]))
    v = first("CT_CoverWidth")
    if v is not None:
        r["cover_width"] = inputs.length("CT_CoverWidth", v)
        r.pop("cover_exact", None)
        changes.append(u"standard width → {:g} mm".format(layout.snapped_cover_width(r)))
    v = first("CT_MaxLength")
    if v is not None:
        r["max_length"] = inputs.length("CT_MaxLength", v)
        for lay in g["layouts"]:          # re-split strips nobody adjusted by hand
            for col in lay["cols"]:
                if not any(p.get("fixed") for p in col.get("pieces", [])):
                    col["pieces"] = []
        changes.append(u"max length → {:g} mm".format(r["max_length"]))

    # ---- the same value on every panel becomes the cladding default ------
    all_panels = len(panel_edits) >= panel_count
    for name, field in (("CT_Profile", "profile"), ("CT_Material", "material"),
                        ("CT_Standoff", "standoff"), ("CT_Perforation", "perforation")):
        vals = [ed[name] for ed in panel_edits.values() if name in ed]
        if not (all_panels and len(vals) == len(panel_edits)):
            continue
        if any(inputs.differs(name, x, vals[0]) for x in vals):
            continue
        if field == "profile":
            p = _find_profile(vals[0], g, library)
            g["profiles"][p["name"]] = p
            r["profile"] = p
            changes.append(u"profile → {} (all panels)".format(p["name"]))
        elif field == "material":
            r["material"] = inputs.material(vals[0])
            changes.append(u"material → {} (all panels)".format(r["material"] or "none"))
        elif field == "perforation":
            r["perforation"] = perforation.parse(vals[0])
            changes.append(u"perforation → {} (all panels)".format(
                perforation.describe(r["perforation"])))
        else:
            r["standoff"] = inputs.length("CT_Standoff", vals[0])
            changes.append(u"standoff → {:g} mm (all panels)".format(r["standoff"]))
        for ov in g["overrides"].values():
            ov.pop(field, None)
        for ed in panel_edits.values():
            ed.pop(name, None)

    # ---- the same width on every panel becomes the standard width ---------
    widths = [ed["CT_PanelWidth"] for ed in panel_edits.values() if "CT_PanelWidth" in ed]
    if all_panels and len(widths) == len(panel_edits) and \
            not any(inputs.differs("CT_PanelWidth", x, widths[0]) for x in widths):
        r["cover_width"] = inputs.length("CT_PanelWidth", widths[0])
        r.pop("cover_exact", None)
        for lay in g["layouts"]:
            for col in lay["cols"]:
                col["fixed"] = False
        for ed in panel_edits.values():
            ed.pop("CT_PanelWidth", None)
        changes.append(u"standard width → {:g} mm (all panels)".format(
            layout.snapped_cover_width(r)))

    # ---- per-panel values --------------------------------------------------
    for k in sorted(panel_edits, key=lambda s: [int(x) for x in s.split(":")]):
        ed = panel_edits[k]
        face, col, row = [int(x) for x in k.split(":")]
        label = panel_label(face, col, row)
        ov = g["overrides"].setdefault(k, {})

        if "CT_Profile" in ed:
            p = _find_profile(ed["CT_Profile"], g, library)
            g["profiles"][p["name"]] = p
            ov["profile"] = p["name"]
            changes.append(u"{}: profile → {}".format(label, p["name"]))
        if "CT_Material" in ed:
            ov["material"] = inputs.material(ed["CT_Material"])
            changes.append(u"{}: material → {}".format(label, ov["material"] or "none"))
        if "CT_Perforation" in ed:
            ov["perforation"] = perforation.parse(ed["CT_Perforation"])
            changes.append(u"{}: perforation → {}".format(
                label, perforation.describe(ov["perforation"])))
        if "CT_Standoff" in ed:
            ov["standoff"] = inputs.length("CT_Standoff", ed["CT_Standoff"])
            changes.append(u"{}: standoff → {:g} mm".format(label, ov["standoff"]))
        if "CT_PanelWidth" in ed:
            w = inputs.length("CT_PanelWidth", ed["CT_PanelWidth"])
            m = _module(panel_settings(g, k)["profile"])
            w = max(1, int(round(w / m))) * m
            c = _ensure_col(g, face, col)
            c["width"], c["fixed"] = round(w, 2), True
            changes.append(u"{}: strip width → {:g} mm".format(label, round(w, 1)))
        if "CT_PanelLength" in ed:
            L = inputs.length("CT_PanelLength", ed["CT_PanelLength"])
            c = _ensure_col(g, face, col)
            pieces = c.setdefault("pieces", [])
            while len(pieces) <= row:
                pieces.append({"length": L, "fixed": False})
            pieces[row] = {"length": L, "fixed": True}
            changes.append(u"{}: length → {:g} mm".format(label, L))
        if not ov:
            g["overrides"].pop(k, None)
    return g, changes


def reset_edits(group):
    g = copy.deepcopy(group)
    g["layouts"], g["overrides"] = [], {}
    return g


def set_default_profile(group, profile):
    g = copy.deepcopy(group)
    p = _clean_profile(profile)
    g["profiles"][p["name"]] = p
    g["recipe"]["profile"] = p
    for ov in g["overrides"].values():
        ov.pop("profile", None)
    g["overrides"] = dict((k, v) for k, v in g["overrides"].items() if v)
    return g


def expected_for(group, panel_data):
    """expected_values() for a stored panel, with cut-outs taken into account."""
    ev = expected_values(group, panel_data["key"], panel_data["width"], panel_data["length"])
    kept = float(panel_data.get("kept", 1.0))
    for q in ("CT_CoverArea", "CT_DevelopedArea", "CT_Weight"):
        ev[q] *= kept
    return ev


# --------------------------------------------------------------------------
# Panel types (one per profile + material, shared by all claddings)
# --------------------------------------------------------------------------
_BAD_NAME_CHARS = u'\\:{}[]|;<>?`~'


def type_name(profile_name, material, perf=None):
    """Revit type name for a profile + material (+ perforation), e.g.
    'Cladding - Corrugated 18/76 - Stál - Ø5/8 staggered'. Characters Revit
    doesn't allow in names are replaced."""
    name = u"Cladding - {} - {}".format(profile_name or u"Custom", material or u"No material")
    if perf:
        name += u" - " + perforation.label(perf)
    return u"".join(u"-" if c in _BAD_NAME_CHARS else c for c in name)


def type_edits(type_data, expected):
    """CT_ edits implied by switching a panel to another cladding type."""
    edits = {}
    if not type_data:
        return edits
    if inputs.differs("CT_Profile", type_data.get("profile"), expected.get("CT_Profile")):
        edits["CT_Profile"] = type_data["profile"]
    if inputs.differs("CT_Material", type_data.get("material", u""), expected.get("CT_Material")):
        edits["CT_Material"] = type_data.get("material", u"")
    want = perforation.label(type_data.get("perforation")) or u"none"
    if want != expected.get("CT_Perforation", u"none"):
        edits["CT_Perforation"] = want
    return edits


# --------------------------------------------------------------------------
# Switch cladding
# --------------------------------------------------------------------------
def switch_profile(group, profile, keys=None, material=None, snap=False):
    """Swap the cladding profile (and optionally material), keeping the layout.

    keys:     None = every panel; else only these panel keys.
    material: None = keep current materials; a name ("" = none) = use it.
    snap:     False = keep every panel width exactly (ribs may be cut at the
              panel edge); True = round widths to whole ribs of the new profile
              (strips shift a little). Only used when keys is None.
    Returns (new group, [change descriptions]).
    """
    g = copy.deepcopy(group)
    r = g["recipe"]
    p = _clean_profile(profile)
    g["profiles"][p["name"]] = p
    changes = []

    if keys is None:
        old_cover = float(r["cover_exact"]) if r.get("cover_exact") \
            else layout.snapped_cover_width(r)
        r["profile"] = p
        for ov in g["overrides"].values():
            ov.pop("profile", None)
        if snap:
            r.pop("cover_exact", None)
            m = _module(p)
            for lay in g["layouts"]:
                for col in lay["cols"]:
                    if col.get("fixed"):
                        col["width"] = round(max(1, int(round(col["width"] / m))) * m, 2)
            changes.append(u"all panels → {}, widths snapped to its ribs ({:g} mm standard)"
                           .format(p["name"], round(layout.snapped_cover_width(r), 1)))
        else:
            r["cover_exact"] = round(old_cover, 2)
            changes.append(u"all panels → {}, panel sizes kept".format(p["name"]))
        if material is not None:
            r["material"] = inputs.material(material)
            for ov in g["overrides"].values():
                ov.pop("material", None)
            changes.append(u"all panels → material {}".format(r["material"] or u"none"))
    else:
        for k in keys:
            ov = g["overrides"].setdefault(k, {})
            ov["profile"] = p["name"]
            if material is not None:
                ov["material"] = inputs.material(material)
        changes.append(u"{} selected panel(s) → {}{}".format(
            len(keys), p["name"],
            u", material {}".format(inputs.material(material) or u"none")
            if material is not None else u""))
    g["overrides"] = dict((k, v) for k, v in g["overrides"].items() if v)
    return g, changes


def switch_settings(group, new, snap=False):
    """Make the window's settings the cladding's defaults (Switch Cladding).

    Only what actually changed is applied; panel edits that don't conflict
    are kept (a changed profile/material/standoff replaces per-panel ones,
    a changed orientation resets the layout).
    snap: when the profile changes, round widths to whole ribs instead of
    keeping panel sizes exactly.
    Returns (new group, [change descriptions]).
    """
    g = copy.deepcopy(group)
    r = g["recipe"]
    new = layout.merged(new)
    changes = []

    if new["orientation"] != r["orientation"]:
        r["orientation"] = new["orientation"]
        g["layouts"], g["overrides"] = [], {}
        changes.append(u"orientation → {} (panel edits reset)".format(new["orientation"]))

    if fold(new["profile"].get("name")) != fold(r["profile"].get("name")):
        g, c = switch_profile(g, new["profile"], snap=snap)
        r = g["recipe"]
        changes.extend(c)

    if inputs.differs("CT_Material", new["material"] or u"", r["material"] or u""):
        r["material"] = inputs.material(new["material"])
        for ov in g["overrides"].values():
            ov.pop("material", None)
        changes.append(u"material → {} (all panels)".format(r["material"] or u"none"))

    new_perf = perforation.normalize(new.get("perforation"))
    if new_perf != perforation.normalize(r.get("perforation")):
        r["perforation"] = new_perf
        for ov in g["overrides"].values():
            ov.pop("perforation", None)
        changes.append(u"perforation → {} (all panels)".format(perforation.describe(new_perf)))

    if abs(float(new["standoff"]) - float(r["standoff"])) > 0.05:
        r["standoff"] = round(float(new["standoff"]), 1)
        for ov in g["overrides"].values():
            ov.pop("standoff", None)
        changes.append(u"standoff → {:g} mm (all panels)".format(r["standoff"]))

    if abs(float(new["joint"]) - float(r["joint"])) > 0.05:
        r["joint"] = round(float(new["joint"]), 1)
        changes.append(u"joint → {:g} mm".format(r["joint"]))

    if abs(float(new["cover_width"]) - float(r["cover_width"])) > 0.05:
        r["cover_width"] = round(float(new["cover_width"]), 1)
        r.pop("cover_exact", None)
        changes.append(u"standard width → {:g} mm".format(round(layout.snapped_cover_width(r), 1)))

    if abs(float(new["max_length"]) - float(r["max_length"])) > 0.05:
        r["max_length"] = round(float(new["max_length"]), 1)
        for lay in g["layouts"]:
            for col in lay["cols"]:
                if not any(p.get("fixed") for p in col.get("pieces", [])):
                    col["pieces"] = []
        changes.append(u"max length → {:g} mm".format(r["max_length"]))

    g["overrides"] = dict((k, v) for k, v in g["overrides"].items() if v)
    return g, changes

