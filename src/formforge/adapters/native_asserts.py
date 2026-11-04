"""Read-only migration from PCB_mode; see docs/migrations/M4-routing-kernels.json.
Local reuse authorized by the user; no distribution license inferred.
"""
import os
_WX_APP = None
_WX_LOG_TARGET = None
NATIVE_ASSERT_MODE = None

def configure_native_asserts(mode=None):
    """Stop wxWidgets asserts from opening modal dialogs in headless runs.

    Without a wx.App, a native assert (e.g. KiCad 9 SHAPE_POLY_SET::SquaredDistance
    with aOutlineOnly) shows a blocking message box. 'exception' (default) turns
    it into wx.wxAssertionError so the faulty call fails loudly; 'log' writes it
    to stderr and continues. PRODUCTIDE_WX_ASSERTS overrides the default. An
    existing application (KiCad GUI scripting) is left untouched.
    """
    global _WX_APP, _WX_LOG_TARGET, NATIVE_ASSERT_MODE
    mode = mode or os.environ.get('PRODUCTIDE_WX_ASSERTS', 'exception')
    if mode not in ('exception', 'log'):
        raise ValueError('PRODUCTIDE_WX_ASSERTS must be exception or log: ' + mode)
    try:
        import wx
    except ImportError:
        return None
    app = wx.GetApp()
    if app is None:
        app = _WX_APP = wx.App(False)
        # A headless-owned app must never leave parser/runtime errors queued in
        # wx.LogGui modal dialogs. Retain the target for the app lifetime so
        # native diagnostics remain visible on stderr.
        _WX_LOG_TARGET = wx.LogStderr()
        wx.Log.SetActiveTarget(_WX_LOG_TARGET)
        # Every native board load re-registers image handlers at debug level.
        wx.Log.SetLogLevel(wx.LOG_Info)
    elif app is not _WX_APP:
        return None
    app.SetAssertMode(wx.APP_ASSERT_EXCEPTION if mode == 'exception' else wx.APP_ASSERT_LOG)
    NATIVE_ASSERT_MODE = mode
    return mode
