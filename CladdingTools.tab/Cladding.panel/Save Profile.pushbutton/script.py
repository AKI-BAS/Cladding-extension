# -*- coding: utf-8 -*-
"""Turn detail lines drawn in a drafting view into a cladding profile.

How to draw one:
  1. Open (or create) a drafting view.
  2. Draw ONE repeat module of the profile with detail lines, left to right,
     as a single connected line. Use normal Revit dimensions to get it right.
     The lowest point is the back of the profile.
  3. Select the lines and click Save Profile.
"""
__title__ = "Save\nProfile"
__doc__ = "Save selected detail lines (one profile module) to the cladding library."

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import DetailCurve, Line

from cladding import profiles

doc = revit.doc
view = doc.ActiveView
MM = 304.8  # feet -> mm

curves = [el for el in revit.get_selection() if isinstance(el, DetailCurve)]
if not curves:
    forms.alert("Select the detail lines that make up one profile module.", exitscript=True)

segments = []
for c in curves:
    g = c.GeometryCurve
    if not isinstance(g, Line):
        forms.alert("Use straight lines only. Arcs can be approximated with a few segments.",
                    exitscript=True)
    segments.append((g.GetEndPoint(0), g.GetEndPoint(1)))

# --- chain the segments into one polyline -----------------------------------
tol = 0.5 / MM


def same(a, b):
    return a.DistanceTo(b) < tol


chain = list(segments.pop(0))
while segments:
    for i, (a, b) in enumerate(segments):
        if same(a, chain[-1]):
            chain.append(b)
        elif same(b, chain[-1]):
            chain.append(a)
        elif same(b, chain[0]):
            chain.insert(0, a)
        elif same(a, chain[0]):
            chain.insert(0, b)
        else:
            continue
        segments.pop(i)
        break
    else:
        forms.alert("The lines don't form one connected line. Check for gaps.",
                    exitscript=True)

# --- to 2D view coordinates (mm) --------------------------------------------
right, up = view.RightDirection, view.UpDirection
pts = [(p.DotProduct(right) * MM, p.DotProduct(up) * MM) for p in chain]
if pts[0][0] > pts[-1][0]:
    pts.reverse()
x0 = pts[0][0]
z0 = min(p[1] for p in pts)
pts = [(round(x - x0, 2), round(z - z0, 2)) for x, z in pts]

for a, b in zip(pts, pts[1:]):
    if b[0] < a[0] - 0.01:
        forms.alert("The profile doubles back on itself. Draw it so it always "
                    "moves left to right (vertical steps are fine).", exitscript=True)

# --- name and thickness -------------------------------------------------------
name = forms.ask_for_string(prompt="Profile name", title="Save Profile",
                            default="Custom profile")
if not name:
    script.exit()
t = forms.ask_for_string(prompt="Sheet thickness (mm)", title="Save Profile", default="0.7")
try:
    thickness = float((t or "0.7").replace(",", "."))
except ValueError:
    thickness = 0.7

profile = {"name": name, "thickness": thickness, "points": [list(p) for p in pts],
           "source": "drawn in Revit"}
path = profiles.save_profile(profile)
pp = profiles.profile_points(profile)

out = script.get_output()
out.print_md("### Saved **{}**".format(name))
out.print_md("- Module width {:.1f} mm, depth {:.1f} mm, {} points".format(
    profiles.module_width(pp), profiles.depth(pp), len(pp)))
out.print_md("- Sheet length per module {:.1f} mm".format(profiles.polyline_length(pp)))
out.print_md("- File: `{}`".format(path))
