# -*- coding: utf-8 -*-
"""Runs when pyRevit loads the extension: turns on live cladding updates."""
# Keep this script's engine alive: Revit calls back into the updater later.
__persistentengine__ = True

from pyrevit import HOST_APP

try:
    from cladding import live
    try:
        addin_id = HOST_APP.addin_id
    except Exception:
        addin_id = __revit__.ActiveAddInId  # noqa: F821
    live.register(addin_id)
except Exception as ex:
    print(u"CladdingTools: live update is off ({}). "
          u"Edit CT_ parameters and use the Regenerate button instead.".format(ex))
