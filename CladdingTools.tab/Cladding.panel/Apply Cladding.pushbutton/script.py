# -*- coding: utf-8 -*-
"""Pick faces, choose a profile and panel settings, build profiled cladding."""
__title__ = "Apply\nCladding"
__doc__ = "Pick wall, roof, floor or ceiling faces and clad them with a profiled sheet."

import os

from pyrevit import revit, forms, script
from Autodesk.Revit.UI.Selection import ObjectType
from Autodesk.Revit.DB import FilteredElementCollector, Material, PlanarFace
from Autodesk.Revit.Exceptions import OperationCanceledException

import System.Windows.Media as Media
import System.Windows.Shapes as Shapes
from System.Windows import Point

from cladding import profiles, layout, preview, revit_build

doc = revit.doc
uidoc = revit.uidoc
XAML = os.path.join(os.path.dirname(__file__), "ui.xaml")


def pick_faces():
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Face, "Pick flat faces to clad, then click Finish")
    except OperationCanceledException:
        return []
    faces, skipped = [], 0
    for ref in refs:
        face = doc.GetElement(ref.ElementId).GetGeometryObjectFromReference(ref)
        if isinstance(face, PlanarFace):
            faces.append((ref, face))
        else:
            skipped += 1
    if skipped:
        forms.alert("{} curved face(s) skipped. This version clads flat faces only."
                    .format(skipped))
    return faces


class CladdingWindow(forms.WPFWindow):
    # Class-level so XAML events that fire while the window loads are ignored
    _ready = False

    def __init__(self, faces, library):
        forms.WPFWindow.__init__(self, XAML)
        self.faces = faces
        self.frames = [revit_build.FaceFrame(f) for _, f in faces]
        self.result = None
        self.library = library
        self.ProfileList.ItemsSource = [p.get("name", "Unnamed") for p in library]
        names = sorted(m.Name for m in FilteredElementCollector(doc).OfClass(Material))
        self.MaterialBox.ItemsSource = ["(none)"] + names
        self.MaterialBox.SelectedIndex = 0
        self.ProfileList.SelectedIndex = 0
        self._ready = True
        self.refresh()

    # ----------------------------------------------------------------- input
    def recipe(self):
        i = self.ProfileList.SelectedIndex
        if i < 0:
            raise ValueError("Pick a profile.")

        def num(box, label, minimum):
            try:
                v = float(box.Text.replace(",", "."))
            except ValueError:
                raise ValueError("{} must be a number.".format(label))
            if v < minimum:
                raise ValueError("{} must be at least {:g}.".format(label, minimum))
            return v

        mat = self.MaterialBox.SelectedItem
        return {
            "profile": self.library[i],
            "cover_width": num(self.WidthBox, "Cover width", 50),
            "max_length": num(self.LengthBox, "Max length", 200),
            "joint": num(self.JointBox, "Joint", 0),
            "standoff": num(self.StandoffBox, "Standoff", 0),
            "orientation": "horizontal" if self.OrientationBox.SelectedIndex == 1 else "vertical",
            "material": "" if mat in (None, "(none)") else mat,
        }

    # --------------------------------------------------------------- refresh
    def on_change(self, sender, args):
        if self._ready:
            self.refresh()

    def refresh(self):
        self.AngleText.Text = u"{:.0f}°".format(self.AngleSlider.Value)
        try:
            r = self.recipe()
        except ValueError as ex:
            self.StatusText.Text = str(ex)
            self.ApplyButton.IsEnabled = False
            return
        self.StatusText.Text = ""
        self.ApplyButton.IsEnabled = True
        pts = profiles.profile_points(r["profile"])
        snapped = layout.snapped_cover_width(r)
        self.ProfileInfo.Text = (
            u"Module {:.0f} mm · depth {:.0f} mm · t {} mm\n"
            u"Cover width snaps to {:.0f} mm".format(
                profiles.module_width(pts), profiles.depth(pts),
                r["profile"].get("thickness", 0.7), snapped))
        self.draw_preview(r)
        self.show_quantities(r)

    def draw_preview(self, r):
        c = self.PreviewCanvas
        c.Children.Clear()
        w, h = c.ActualWidth, c.ActualHeight
        if w < 10 or h < 10:
            return
        for pts, shade in preview.polygons(r, w, h, self.AngleSlider.Value):
            g = int(60 + 175 * shade)
            poly = Shapes.Polygon()
            poly.Fill = Media.SolidColorBrush(Media.Color.FromRgb(g, g, min(255, g + 6)))
            poly.Stroke = Media.SolidColorBrush(Media.Color.FromArgb(90, 22, 24, 27))
            poly.StrokeThickness = 0.5
            pc = Media.PointCollection()
            for x, y in pts:
                pc.Add(Point(x, y))
            poly.Points = pc
            c.Children.Add(poly)

    def show_quantities(self, r):
        panels = []
        for fr in self.frames:
            for p in layout.layout(fr.extents, r):
                cu, cv = layout.panel_center(p, r["orientation"])
                if fr.contains(cu, cv):
                    panels.append(p)
        t = layout.totals(panels, r)
        self.QtyText.Text = (
            u"Panels      {count:>8}\n"
            u"Length      {total_length_m:>8.1f} m\n"
            u"Cover area  {cover_area_m2:>8.1f} m²\n"
            u"Sheet area  {developed_area_m2:>8.1f} m²\n"
            u"Weight      {weight_kg:>8.0f} kg").format(**t)

    def apply_click(self, sender, args):
        try:
            self.result = self.recipe()
        except ValueError as ex:
            self.StatusText.Text = str(ex)
            return
        self.Close()


# ---------------------------------------------------------------------------
library = profiles.load_library()
if not library:
    forms.alert("No profiles found in:\n{}".format(profiles.library_dir()), exitscript=True)

faces = pick_faces()
if not faces:
    script.exit()

win = CladdingWindow(faces, library)
win.ShowDialog()

if win.result:
    with revit.Transaction("Apply cladding"):
        group, made, skipped, totals = revit_build.build(doc, faces, win.result)
    out = script.get_output()
    out.print_md("### Cladding `{}` created".format(group))
    out.print_md("- {} panels, {} skipped (openings or failed geometry)".format(made, skipped))
    out.print_md("- Cover area {:.1f} m², sheet area {:.1f} m², weight {:.0f} kg".format(
        totals["cover_area_m2"], totals["developed_area_m2"], totals["weight_kg"]))
