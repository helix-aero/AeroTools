"""Scaling: `px()` is the one helper that turns a design size (px at 100 %) into device-independent pixels of this screen.

Fonts are set in pixels too (`font_px`), so a text and the box around it always scale together. When Qt itself scales the UI
(logical DPI stays 96) the factor is 1; when it does not (Metashape under Qt 5 on a 125 % or 150 % display) the factor is the logical DPI / 96.
"""

import os

from . import tokens

_state = {"override": None, "cached": None}


def set_scale(value=None):
    """Force a scale factor (tests, screenshots). `None` goes back to the screen's own."""
    _state["override"] = None if value is None else float(value)
    _state["cached"] = None


def refresh():
    """Forget the measured factor (the screen changed)."""
    _state["cached"] = None


def scale():
    """Current scale factor, 1.0 at 100 %."""
    if _state["override"] is not None:
        return _state["override"]
    if _state["cached"] is None:
        factor = 1.0
        try:
            from ..qt import QtWidgets
            app = QtWidgets.QApplication.instance()
            if app is None:
                return 1.0                    # no screen yet: do not remember this
            factor = float(app.primaryScreen().logicalDotsPerInch()) / 96.0
        except Exception:
            factor = 1.0
        _state["cached"] = min(3.0, max(1.0, factor))
    return _state["cached"]


def px(value):
    """`value` px at 100 % as pixels on this screen (an int, at least 1 for a positive value)."""
    scaled = int(round(value * scale()))
    return max(1, scaled) if value > 0 else scaled


def font_px(role):
    """Pixel size of a type role ('caption' .. 'display') on this screen."""
    return px(tokens.TYPE[role])


_loaded = set()


def ensure_fonts():
    """Register the font files the active font names (files of assets/fonts), each once. Needs a QApplication; harmless without."""
    files = [f for f in getattr(tokens, "FONT_FILES", ()) if f not in _loaded]
    if not files:
        return
    try:
        from .. import config
        from ..qt import QtGui, QtWidgets
        if QtWidgets.QApplication.instance() is None:
            return
        for name in files:
            _loaded.add(name)
            path = os.path.join(config.FONTS, name)
            if os.path.isfile(path):
                QtGui.QFontDatabase.addApplicationFont(path)
    except Exception:
        pass


def font(role="body", strong=False, family="text"):
    """A QFont for a type role (used by painted widgets; labels get theirs from the style sheet). `family`: text, heading, label or mono."""
    from ..qt import QtGui
    names = {"heading": tokens.HEADING_FAMILIES, "label": tokens.LABEL_FAMILIES, "mono": tokens.MONO_FAMILIES}.get(family, tokens.FONT_FAMILIES)
    result = QtGui.QFont(names[0])
    if len(names) > 1 and hasattr(result, "setFamilies"):
        result.setFamilies(list(names))
    result.setPixelSize(font_px(role))
    result.setWeight(QtGui.QFont.DemiBold if strong else QtGui.QFont.Normal)
    return result


def apply_case(widget, kind="label"):
    """Give a text widget the skin's label casing and letter spacing (`kind`: label, button, title). Only those two attributes are
    set, so the style sheet still decides family, size and weight. Does nothing in a skin without them."""
    from ..qt import QtGui
    spaced = tokens.LABEL_SPACING > 0
    upper = tokens.LABEL_CASE == "upper"
    if not (spaced or upper) and not widget.property("_cased"):
        return
    f = QtGui.QFont()
    f.setCapitalization(QtGui.QFont.AllUppercase if upper else QtGui.QFont.MixedCase)
    share = {"label": 1.0, "button": 0.6, "title": 0.5}.get(kind, 1.0)
    f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, tokens.LABEL_SPACING * share * font_px("body") if spaced else 0.0)
    widget.setFont(f)
    widget.setProperty("_cased", True)
