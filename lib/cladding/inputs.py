# -*- coding: utf-8 -*-
"""The CT_ parameters: definitions and value parsing.

Pure Python (no Revit imports) so it can be tested outside Revit.
Lengths are mm here; revit_params.py converts to Revit's internal units.
GUIDs are fixed so schedules and filters work across projects.
"""
from cladding.profiles import fold

# --- per panel: change only the selected panel(s) ---------------------------
PANEL = [
    ("CT_PanelWidth", "length", "3de3e4ce-a318-4b51-be66-b43d22145a74",
     "Width of this panel's strip. Snaps to whole profile modules; later strips shift."),
    ("CT_PanelLength", "length", "153355f0-acfc-431e-8ff0-e8372f6d9de6",
     "Length of this panel. Later panels in the strip re-split the rest."),
    ("CT_Profile", "text", "a6b8954b-b13b-4eef-b2ed-83af6dd2b14e",
     "Profile of this panel (any library profile name)."),
    ("CT_Material", "material", "4f7c2a91-6e3d-4b58-a0c7-2d9e81f5b6a3",
     "Material of this panel (or flashing piece). Pick from the project's materials."),
    ("CT_Perforation", "text", "99c985e4-87a9-4161-8eb5-288165cce053",
     "Perforation of this panel: hole/pitch and pattern, e.g. 'Ø5/8 staggered', or 'none'."),
    ("CT_Standoff", "length", "7b588b98-d9f5-40fb-8899-c176c4012762",
     "Distance from the host face to the back of this panel."),
]

# --- whole cladding: change every panel ----------------------------------------
GROUP = [
    ("CT_CoverWidth", "length", "31210a1b-a306-4436-909f-ebe4d1e914ea",
     "Standard panel width for the whole cladding (panels you resized keep their width)."),
    ("CT_MaxLength", "length", "6a5ed28e-8080-45d1-a767-b45a76792130",
     "Longest panel before a run is split, for the whole cladding."),
    ("CT_Joint", "length", "d73483f8-66fb-4a4f-ba56-d8410f301a30",
     "Gap between panels, for the whole cladding."),
    ("CT_Orientation", "text", "aa9b4eb1-e333-4d93-8ff4-592f795f3243",
     "Vertical or Horizontal ribs. Changing it resets all panel edits."),
]

# --- calculated: for schedules, snap back if typed over ------------------------
OUTPUTS = [
    ("CT_Group", "text", "b3407831-6dba-45c3-ba53-d81cafe41195",
     "Cladding id (calculated)."),
    ("CT_PanelId", "text", "e3b1c9a4-5d2f-4b7e-9a1c-8f6d2e4b7a35",
     "Panel position: face, strip, piece (calculated)."),
    ("CT_CoverArea", "area", "345c0604-169f-41b4-9fb7-9ac4fe7357f9",
     "Facade area covered by the panel (calculated)."),
    ("CT_DevelopedArea", "area", "c22d5738-e462-4a2c-832e-7703a929593c",
     "Unfolded sheet area of the panel (calculated)."),
    ("CT_Thickness", "length", "90fe6378-dad7-4ef9-8ea6-65ab7eabd7ee",
     "Sheet thickness from the profile / flashing type (calculated)."),
    ("CT_OpenArea", "number", "45c86b4b-17d0-4106-986a-0835c2ae14a0",
     "Open area of the perforation in % (calculated)."),
    ("CT_Weight", "number", "d89ae7c9-cfbe-4b16-87f8-dff2372dee8d",
     "Panel weight in kg (calculated)."),
]

INPUTS = PANEL + GROUP
# --- labels: written by the tool (marks), never treated as edits ------------
LABELS = [
    ("CT_PanelType", "text", "b2897035-9154-43ca-a3a8-fe1df7813871",
     "Panel mark: identical panels (profile, material, thickness, perforation, "
     "width, length) share one mark across the project (calculated)."),
    ("CT_Cut", "text", "3bd2ecad-d40f-4c0c-90d9-1dc0261a92d4",
     "'Cut' when the panel is cut around an opening or an edge (calculated)."),
]

ALL = INPUTS + OUTPUTS + LABELS
KINDS = dict((d[0], d[1]) for d in ALL)
PANEL_NAMES = [d[0] for d in PANEL]
GROUP_NAMES = [d[0] for d in GROUP]
OUTPUT_NAMES = [d[0] for d in OUTPUTS]

LIMITS = {   # name: (minimum mm, label)
    "CT_PanelWidth": (50.0, "Panel width"),
    "CT_PanelLength": (100.0, "Panel length"),
    "CT_Standoff": (0.0, "Standoff"),
    "CT_CoverWidth": (50.0, "Cover width"),
    "CT_MaxLength": (200.0, "Max length"),
    "CT_Joint": (0.0, "Joint"),
}

_ORIENT = {
    "vertical": "vertical", "v": "vertical", "lodrett": "vertical", "lodret": "vertical",
    "horizontal": "horizontal", "h": "horizontal", "larett": "horizontal",
    "vandret": "horizontal",
}


def orientation(value):
    """'Lóðrétt' / 'vertical' / 'V' -> 'vertical'. ValueError when unknown."""
    o = _ORIENT.get(fold(value))
    if o is None:
        raise ValueError(u"Orientation must be Vertical or Horizontal (got '{}').".format(value))
    return o


def orientation_label(o):
    return "Horizontal" if o == "horizontal" else "Vertical"


def length(name, value):
    v = float(value)
    minimum, label = LIMITS[name]
    if v < minimum - 1e-6:
        raise ValueError(u"{} must be at least {:g} mm (got {:g}).".format(label, minimum, v))
    return round(v, 1)


def material(value):
    from cladding.perforation import base_material
    m = base_material((value or u"").strip()).strip()
    return u"" if fold(m) in (u"(none)", u"none") else m


def differs(name, current, expected):
    """True when a value in Properties is a real edit of the expected value."""
    if current is None:
        return False
    kind = KINDS.get(name, "text")
    if kind == "material":
        from cladding.perforation import base_material
        return fold(base_material(current)) != fold(base_material(expected))
    if kind == "text":
        if name == "CT_Perforation":
            from cladding import perforation
            try:
                return (perforation.label(perforation.parse(current)) or u"none") != \
                    (expected or u"none")
            except ValueError:
                return True
        if name in ("CT_Profile", "CT_Orientation"):
            return fold(current) != fold(expected)
        return (current or u"").strip() != (expected or u"")
    tol = 0.5 if kind == "length" else max(0.01, abs(float(expected or 0)) * 1e-3)
    return abs(float(current) - float(expected or 0)) > tol
