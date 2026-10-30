# -*- coding: utf-8 -*-
"""The cladding window, shared by Apply Cladding and Switch Cladding.

Apply mode:  empty settings, button "Apply to faces".
Switch mode: filled in with the cladding's current settings, button
             "Switch cladding", plus the keep-panel-sizes option.
"""
import os

from pyrevit import forms
from Autodesk.Revit.DB import FilteredElementCollector, Material

import System.Windows.Media as Media
import System.Windows.Shapes as Shapes
from System.Windows import Point, Visibility

from cladding import profiles, layout, preview, groupdata

XAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.xaml")
NO_MATERIAL = "(none)"


class CladdingWindow(forms.WPFWindow):
    # Class-level so XAML events that fire while the window loads are ignored
    _ready = False

    def __init__(self, doc, frames, library, initial=None, layouts=None,
                 mode="apply", note=None):
        forms.WPFWindow.__init__(self, XAML)
        self.frames = frames
        self.layouts = layouts or []
        self.mode = mode
        self.result = None
        self.keep_sizes = True

        # profiles: library, plus the current one if it isn't in the library
        self.library = list(library)
        if initial and not any(profiles.fold(p.get("name")) ==
                               profiles.fold(initial["profile"].get("name"))
                               for p in self.library):
            self.library.append(initial["profile"])
        self.ProfileList.ItemsSource = [p.get("name", "Unnamed") for p in self.library]

        names = sorted(m.Name for m in FilteredElementCollector(doc).OfClass(Material))
        self.MaterialBox.ItemsSource = [NO_MATERIAL] + names

        if initial:
            self._fill(initial, names)
        else:
            self.ProfileList.SelectedIndex = 0
            self.MaterialBox.SelectedIndex = 0

        if mode == "switch":
            self.Title = "Switch cladding"
            self.ApplyButton.Content = "Switch cladding"
            self.KeepSizesBox.Visibility = Visibility.Visible
            if note:
                self.ModeNote.Text = note
                self.ModeNote.Visibility = Visibility.Visible
        self._ready = True
        self.refresh()

    def _fill(self, r, material_names):
        r = layout.merged(r)
        idx = [i for i, p in enumerate(self.library)
               if profiles.fold(p.get("name")) == profiles.fold(r["profile"].get("name"))]
        self.ProfileList.SelectedIndex = idx[0] if idx else 0
        self.WidthBox.Text = u"{:g}".format(float(r["cover_width"]))
        self.LengthBox.Text = u"{:g}".format(float(r["max_length"]))
        self.JointBox.Text = u"{:g}".format(float(r["joint"]))
        self.StandoffBox.Text = u"{:g}".format(float(r["standoff"]))
        self.OrientationBox.SelectedIndex = 1 if r["orientation"] == "horizontal" else 0
        hit = profiles.match_name(r["material"] or u"", material_names) if r["material"] else None
        self.MaterialBox.SelectedIndex = (material_names.index(hit) + 1) if hit else 0

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
            "material": u"" if mat in (None, NO_MATERIAL) else mat,
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
        for i, fr in enumerate(self.frames):
            lay = self.layouts[i] if i < len(self.layouts) else None
            placed, _ = groupdata.place(fr.extents, lay, r)
            for p in placed:
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
        self.keep_sizes = bool(self.KeepSizesBox.IsChecked)
        self.Close()
