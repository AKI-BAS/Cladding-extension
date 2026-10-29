# -*- coding: utf-8 -*-
"""The CT_ parameters on each panel, and how edits map back to a recipe.

Pure Python (no Revit imports) so it can be tested outside Revit.
Lengths are mm here; revit_params.py converts to Revit's internal units.
"""
from cladding import layout

# name, kind, GUID (fixed so schedules work across projects), description
INPUTS = [
    ("CT_Profile", "text", "a6b8954b-b13b-4eef-b2ed-83af6dd2b14e",
     "Cladding profile name. Change it to another library profile, then Regenerate."),
    ("CT_CoverWidth", "length", "31210a1b-a306-4436-909f-ebe4d1e914ea",
     "Target panel cover width. Snaps to whole profile modules. Regenerate to apply."),
    ("CT_MaxLength", "length", "6a5ed28e-8080-45d1-a767-b45a76792130",
     "Longest panel before a run is split. Regenerate to apply."),
    ("CT_Joint", "length", "d73483f8-66fb-4a4f-ba56-d8410f301a30",
     "Gap between panels. Regenerate to apply."),
    ("CT_Standoff", "length", "7b588b98-d9f5-40fb-8899-c176c4012762",
     "Distance from the host face to the back of the sheet. Regenerate to apply."),
    ("CT_Orientation", "text", "aa9b4eb1-e333-4d93-8ff4-592f795f3243",
     "Vertical or Horizontal ribs. Regenerate to apply."),
    ("CT_Material", "text", "89940c4a-ce58-454b-9f8a-eba81e7b8fb1",
     "Revit material name, blank for none. Regenerate to apply."),
]

OUTPUTS = [
    ("CT_Group", "text", "b3407831-6dba-45c3-ba53-d81cafe41195",
     "Cladding group id (calculated)."),
    ("CT_PanelLength", "length", "153355f0-acfc-431e-8ff0-e8372f6d9de6",
     "Panel length along the ribs (calculated)."),
    ("CT_PanelWidth", "length", "3de3e4ce-a318-4b51-be66-b43d22145a74",
     "Panel cover width (calculated)."),
    ("CT_CoverArea", "area", "345c0604-169f-41b4-9fb7-9ac4fe7357f9",
     "Facade area covered by the panel (calculated)."),
    ("CT_DevelopedArea", "area", "c22d5738-e462-4a2c-832e-7703a929593c",
     "Unfolded sheet area of the panel (calculated)."),
    ("CT_Weight", "number", "d89ae7c9-cfbe-4b16-87f8-dff2372dee8d",
     "Panel weight in kg (calculated)."),
]

ALL = INPUTS + OUTPUTS

_ORIENT = {
    "vertical": "vertical", "v": "vertical", u"lóðrétt": "vertical", "lodrett": "vertical",
    "lodret": "vertical",
    "horizontal": "horizontal", "h": "horizontal", u"lárétt": "horizontal",
    "larett": "horizontal", "vandret": "horizontal",
}

LIMITS = {
    "CT_CoverWidth": (50.0, "Cover width"),
    "CT_MaxLength": (200.0, "Max length"),
    "CT_Joint": (0.0, "Joint"),
    "CT_Standoff": (0.0, "Standoff"),
}
_KEYS = {
    "CT_CoverWidth": "cover_width",
    "CT_MaxLength": "max_length",
    "CT_Joint": "joint",
    "CT_Standoff": "standoff",
}


def recipe_inputs(recipe):
    """CT_ input values (mm / text) that describe a recipe."""
    r = layout.merged(recipe)
    out = {
        "CT_Profile": r["profile"].get("name", "Custom"),
        "CT_Orientation": "Horizontal" if r["orientation"] == "horizontal" else "Vertical",
        "CT_Material": r["material"] or "",
    }
    for pname, key in _KEYS.items():
        out[pname] = float(r[key])
    return out


def panel_outputs(group_id, panel, quantities):
    return {
        "CT_Group": group_id,
        "CT_PanelLength": float(panel["length"]),
        "CT_PanelWidth": float(panel["width"]),
        "CT_CoverArea": quantities["cover_area_m2"],
        "CT_DevelopedArea": quantities["developed_area_m2"],
        "CT_Weight": quantities["weight_kg"],
    }


def _norm(s):
    from cladding.profiles import fold
    return fold(s)


def apply_inputs(recipe, values, library):
    """Merge edited CT_ values into a copy of `recipe`.

    values: {param name: value or None}. None / missing keeps the stored value.
    Returns (new_recipe, [change descriptions]). Raises ValueError with a
    message meant for the user when a value can't be used.
    """
    r = layout.merged(recipe)
    r = dict(r)
    changes = []
    current = recipe_inputs(r)

    # profile
    name = values.get("CT_Profile")
    if name is not None and _norm(name) and _norm(name) != _norm(current["CT_Profile"]):
        match = [p for p in library if _norm(p.get("name")) == _norm(name)]
        if not match:
            names = ", ".join(p.get("name", "?") for p in library)
            raise ValueError(u"No profile called '{}' in the library. Available: {}"
                             .format(name.strip(), names))
        r["profile"] = dict((k, v) for k, v in match[0].items() if not k.startswith("_"))
        changes.append(u"profile → {}".format(match[0]["name"]))

    # numbers
    for pname, key in _KEYS.items():
        v = values.get(pname)
        if v is None:
            continue
        v = float(v)
        minimum, label = LIMITS[pname]
        if v < minimum - 1e-6:
            raise ValueError(u"{} must be at least {:g} mm (got {:g}).".format(label, minimum, v))
        if abs(v - float(r[key])) > 0.5:
            changes.append(u"{} {:g} → {:g} mm".format(label.lower(), float(r[key]), round(v, 1)))
            r[key] = round(v, 1)

    # orientation
    o = values.get("CT_Orientation")
    if o is not None and _norm(o):
        mapped = _ORIENT.get(_norm(o))
        if mapped is None:
            raise ValueError(u"Orientation must be Vertical or Horizontal (got '{}').".format(o))
        if mapped != r["orientation"]:
            changes.append(u"orientation → {}".format(mapped))
            r["orientation"] = mapped

    # material (blank is allowed and means none)
    m = values.get("CT_Material")
    if m is not None:
        m = m.strip()
        if m.lower() in ("(none)", "none"):
            m = ""
        if m != (r["material"] or ""):
            changes.append(u"material → {}".format(m or "none"))
            r["material"] = m

    return r, changes
