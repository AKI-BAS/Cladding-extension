# -*- coding: utf-8 -*-
"""Runs when pyRevit loads the extension: turns on live CT_ parameter edits.

The result is written to %TEMP%\\CladdingTools_live_status.txt; the Regenerate
button shows it, so you can see whether live edits are on.
"""
# Keep this script's engine alive: Revit calls back into the updater later.
__persistentengine__ = True

import datetime
import traceback

from pyrevit import HOST_APP

try:
    from cladding import live
except Exception:
    live = None
    _import_error = traceback.format_exc()[-600:]

if live is not None:
    try:
        try:
            addin_id = HOST_APP.addin_id
        except Exception:
            addin_id = __revit__.ActiveAddInId  # noqa: F821
        live.register(addin_id)
        live.write_status(u"registered {}".format(datetime.datetime.now()))
    except Exception:
        live.write_status(u"failed {}:\n{}".format(datetime.datetime.now(),
                                                   traceback.format_exc()[-600:]))
        print(u"CladdingTools: live update is off. Edit CT_ parameters and use "
              u"the Regenerate button instead.")
else:
    print(u"CladdingTools: couldn't load the live update:\n" + _import_error)
