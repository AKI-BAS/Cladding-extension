# -*- coding: utf-8 -*-
"""Perforated materials in Revit: cutout image, dot pattern, material variant.

ensure_material(doc, base_name, perf) returns the id of
"<base> | perforated Ø5/8 staggered", creating it once per project:
  1. duplicate the base material (or make a new one) and its appearance
  2. draw a black/white tile of the hole pattern (black = hole) to a PNG in
     the extension's library/textures folder (shared, so the path works on
     every machine that loads the extension from the same place)
  3. connect it as the appearance's cutout map at real-world size
  4. give the material a model surface pattern of short dashes, one per hole
Steps 3 and 4 are best effort; problems are reported, not fatal.
"""
import math
import os

import clr
clr.AddReference("RevitAPI")
clr.AddReference("System.Drawing")
from System.Collections.Generic import List
from Autodesk.Revit.DB import (
    ElementId, FilteredElementCollector, Material, FillPattern, FillPatternElement,
    FillPatternTarget, FillPatternHostOrientation, FillGrid, UV, Color,
    AppearanceAssetElement,
)

from cladding import perforation, profiles

MM = 1.0 / 304.8
PX_PER_MM = 24
problems = []          # filled with text for the output window


def textures_dir():
    return os.path.normpath(os.path.join(profiles.library_dir(), "..", "textures"))


def cutout_png(perf):
    """Draw (once) the repeat tile and return its path."""
    import System.Drawing as D
    folder = textures_dir()
    if not os.path.isdir(folder):
        os.makedirs(folder)
    path = os.path.join(folder, perforation.file_stub(perf) + ".png")
    if os.path.exists(path):
        return path
    w, h, holes = perforation.tile(perf)
    W, H = int(round(w * PX_PER_MM)), int(round(h * PX_PER_MM))
    r = perf["d"] / 2.0 * PX_PER_MM
    bmp = D.Bitmap(W, H)
    g = D.Graphics.FromImage(bmp)
    g.SmoothingMode = D.Drawing2D.SmoothingMode.AntiAlias
    g.Clear(D.Color.White)                       # white = solid sheet
    brush = D.SolidBrush(D.Color.Black)          # black = hole
    for cx, cy in holes:
        for dx in (-W, 0, W):                    # wrap around the tile edges
            for dy in (-H, 0, H):
                x, y = cx * PX_PER_MM + dx, cy * PX_PER_MM + dy
                g.FillEllipse(brush, float(x - r), float(y - r), float(2 * r), float(2 * r))
    g.Dispose()
    bmp.Save(path, D.Imaging.ImageFormat.Png)
    bmp.Dispose()
    return path


def _find_material(doc, name):
    for m in FilteredElementCollector(doc).OfClass(Material):
        if m.Name == name:
            return m
    return None


def _dot_pattern(doc, perf):
    name = u"CladdingTools {}".format(perforation.label(perf))
    for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):
        if fp.Name == name:
            return fp.Id
    p, d = perf["pitch"] * MM, perf["d"] * MM
    grid = FillGrid()
    grid.Angle = 0.0
    grid.Origin = UV(0, 0)
    if perf["pattern"] == "straight":
        grid.Offset, grid.Shift = p, 0.0
    else:
        grid.Offset, grid.Shift = p * math.sqrt(3) / 2.0, p / 2.0
    segs = List[float]()
    segs.Add(d)
    segs.Add(p - d)
    grid.SetSegments(segs)
    pattern = FillPattern(name, FillPatternTarget.Model, FillPatternHostOrientation.ToHost)
    grids = List[FillGrid]()
    grids.Add(grid)
    pattern.SetFillGrids(grids)
    return FillPatternElement.Create(doc, pattern).Id


def _set_cutout(doc, mat, png, perf):
    from Autodesk.Revit.DB.Visual import (
        AppearanceAssetEditScope, Generic, UnifiedBitmap, AssetPropertyString,
        AssetPropertyDistance,
    )
    from Autodesk.Revit.DB import UnitUtils
    aae = doc.GetElement(mat.AppearanceAssetId)
    if aae is None:
        raise ValueError("the material has no appearance asset")
    scope = AppearanceAssetEditScope(doc)
    try:
        asset = scope.Start(aae.Id)
        prop = asset.FindByName(Generic.GenericCutoutOpacity)
        if prop is None:
            raise ValueError("its appearance isn't the Generic type, so it has no cutout slot")
        if prop.NumberOfConnectedProperties == 0:
            prop.AddConnectedAsset("UnifiedBitmapSchema")
        bitmap = prop.GetSingleConnectedAsset()
        path_prop = bitmap.FindByName(UnifiedBitmap.UnifiedbitmapBitmap)
        if isinstance(path_prop, AssetPropertyString):
            path_prop.Value = png
        w, h, _ = perforation.tile(perf)
        for pname, size in ((UnifiedBitmap.TextureRealWorldScaleX, w),
                            (UnifiedBitmap.TextureRealWorldScaleY, h)):
            sp = bitmap.FindByName(pname)
            if isinstance(sp, AssetPropertyDistance):
                try:
                    sp.Value = UnitUtils.ConvertFromInternalUnits(size * MM, sp.GetUnitTypeId())
                except Exception:
                    sp.Value = size / 25.4        # older assets are in inches
        scope.Commit(True)
    except Exception:
        try:
            scope.Cancel()
        except Exception:
            pass
        raise


def ensure_material(doc, base_name, perf):
    """Id of the perforated variant of base_name, created if needed."""
    perf = perforation.normalize(perf)
    name = perforation.material_name(base_name, perf)
    found = _find_material(doc, name)
    if found is not None:
        return found.Id
    base = _find_material(doc, base_name) if base_name else None
    if base is not None:
        mat = doc.GetElement(base.Duplicate(name))
    else:
        mat = doc.GetElement(Material.Create(doc, name))
        mat.Color = Color(160, 165, 170)

    # own copy of the appearance, so the base material isn't changed
    try:
        aae = doc.GetElement(mat.AppearanceAssetId)
        if aae is not None:
            mat.AppearanceAssetId = aae.Duplicate(name).Id
        else:
            generic = [a for a in FilteredElementCollector(doc).OfClass(AppearanceAssetElement)
                       if "generic" in a.Name.lower()]
            if generic:
                mat.AppearanceAssetId = generic[0].Duplicate(name).Id
    except Exception as ex:
        problems.append(u"{}: couldn't copy its appearance ({})".format(name, ex))

    try:
        _set_cutout(doc, mat, cutout_png(perf), perf)
    except Exception as ex:
        problems.append(u"{}: holes won't show in renders ({}). Set the cutout image "
                        u"by hand: {}".format(name, ex, cutout_png(perf)))
    try:
        mat.SurfaceForegroundPatternId = _dot_pattern(doc, perf)
        mat.SurfaceForegroundPatternColor = Color(60, 60, 60)
    except Exception as ex:
        problems.append(u"{}: no hole pattern in views ({})".format(name, ex))
    return mat.Id
