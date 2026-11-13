"""The kit's style sheet: built once per scale factor and cached, applied to the root widget of a window.

Rules are written against dynamic properties (`role`, `type`, `tone`, `kind`, `ui`), never against object names, so a widget is restyled
by changing one property (`set_prop`). Every value comes from `tokens`, i.e. from the active skin: the cache is keyed by skin and scale.
"""

import os
from string import Template

from . import fonts, metrics, tokens

ROOT = "#aeroPanel"
_cache = {}


def fill_css(stops, shade=None, amount=0.0):
    """A QSS colour for a fill: one colour, or a left-to-right gradient of several. `shade` (a colour) is mixed in at `amount`."""
    stops = [tokens.mix(shade, c, amount) if shade and amount and c.startswith("#") else c for c in stops]
    if len(stops) == 1:
        return stops[0]
    step = 1.0 / (len(stops) - 1)
    return "qlineargradient(x1:0, y1:0, x2:1, y2:0, " + ", ".join("stop:%.3f %s" % (i * step, c) for i, c in enumerate(stops)) + ")"


def families_css(names):
    return ", ".join('"%s"' % n for n in names)


def _primary_fills():
    """(fill, hover, pressed, border) of a primary button, from the skin's PRIMARY_FILL (one colour or a gradient)."""
    t = tokens
    stops = list(t.PRIMARY_FILL)
    light = sum(t.luminance(c) for c in stops) / len(stops) > 0.4       # a pale fill darkens on hover, a dark one lifts
    hover = fill_css(stops, t.SHADOW_INK if light else t.ON_OVERLAY, 0.08 if light else 0.14)
    pressed = fill_css(stops, t.SHADOW_INK, 0.16 if light else 0.18)
    return fill_css(stops), hover, pressed, stops[0]


def _values():
    p = metrics.px
    f = metrics.font_px
    t = tokens
    primary, primary_hover, primary_pressed, primary_border = _primary_fills()
    v = {
        "card": t.CARD, "table_header": t.TABLE_HEADER, "selected_row": t.SELECTED_ROW, "button_fill": t.BUTTON_FILL,
        "button_border": t.BUTTON_BORDER, "primary": primary, "primary_hover": primary_hover, "primary_pressed": primary_pressed, "primary_border": primary_border,
        "br": p(min(t.BUTTON_RADIUS, t.BUTTON_H // 2)), "f_field": f(t.FIELD_TYPE),
        "heading": families_css(t.HEADING_FAMILIES), "label_family": families_css(t.LABEL_FAMILIES),
        "bg": t.BG, "surface": t.SURFACE, "surface_hover": t.SURFACE_HOVER, "surface_pressed": t.SURFACE_PRESSED,
        "border": t.BORDER, "border_strong": t.BORDER_STRONG,
        "text_strong": t.TEXT_STRONG, "text": t.TEXT, "text_secondary": t.TEXT_SECONDARY, "text_muted": t.TEXT_MUTED, "text_disabled": t.TEXT_DISABLED,
        "accent": t.ACCENT, "accent_hover": t.ACCENT_HOVER, "accent_pressed": t.ACCENT_PRESSED, "on_accent": t.ON_ACCENT, "on_primary": t.ON_PRIMARY, "button_text": t.BUTTON_TEXT,
        "accent_soft": t.ACCENT_SOFT,
        "success": t.SUCCESS, "warning": t.WARNING, "danger": t.DANGER, "info": t.INFO,
        "danger_solid": t.DANGER_SOLID, "danger_solid_hover": t.DANGER_SOLID_HOVER,
        "danger_solid_pressed": t.DANGER_SOLID_PRESSED, "on_danger": t.ON_DANGER,
        "success_soft": t.SUCCESS_SOFT, "warning_soft": t.WARNING_SOFT, "danger_soft": t.DANGER_SOFT, "info_soft": t.INFO_SOFT,
        "logo_0": t.LOGO_0, "logo_1": t.LOGO_1, "logo_2": t.LOGO_2,
        "family": families_css(t.FONT_FAMILIES), "mono": families_css(t.MONO_FAMILIES),
        "w_strong": t.WEIGHT_STRONG, "w_label": t.WEIGHT_LABEL, "w_regular": t.WEIGHT_REGULAR,
        "r": p(t.RADIUS), "rl": p(t.RADIUS_L), "h": p(t.CONTROL_H), "bh": p(t.BUTTON_H),
        "h_in": p(t.CONTROL_H) - 2, "bh_in": p(t.BUTTON_H) - 2,
        "pad_b": p(t.L), "pad_f": p(t.S + 2), "s1": p(t.XS), "s2": p(t.S), "s3": p(t.M), "s4": p(t.L),
        "pill_b": p(t.BUTTON_H) // 2, "bar": p(t.BAR_H), "bar_r": p(t.BAR_H) // 2,
        "sb": p(8), "sb_r": p(4), "arrow_w": p(28), "row": p(t.CONTROL_H),
    }
    for name in t.TYPE:
        v["f_" + name] = f(name)
    return v


_KIT = """
$root { background: $bg; }
$root QWidget { font-family: $family; font-size: ${f_body}px; font-weight: $w_regular; color: $text; }
$root QLabel { background: transparent; border: none; }
$root QLabel[type="caption"] { font-size: ${f_caption}px; color: $text_muted; }
$root QLabel[type="small"] { font-size: ${f_small}px; }
$root QLabel[type="field"] { font-family: $label_family; font-size: ${f_field}px; font-weight: $w_label; color: $text_secondary; }
$root QLabel[type="status"] { font-size: ${f_body}px; font-weight: $w_strong; }
$root QLabel[type="lead"] { font-family: $heading; font-size: ${f_lead}px; font-weight: $w_strong; color: $text_strong; }
$root QLabel[type="title"] { font-family: $heading; font-size: ${f_title}px; font-weight: $w_strong; color: $text_strong; }
$root QLabel[type="display"] { font-family: $heading; font-size: ${f_display}px; font-weight: $w_strong; color: $text_strong; }
$root QLabel[type="strong"] { font-weight: $w_strong; }
$root QLabel[tone="muted"] { color: $text_muted; }
$root QLabel[tone="faint"] { color: $text_disabled; }
$root QLabel[tone="accent"] { color: $accent; }
$root QLabel[tone="success"] { color: $success; }
$root QLabel[tone="warning"] { color: $warning; }
$root QLabel[tone="danger"] { color: $danger; }
$root QLabel[tone="info"] { color: $info; }
$root QLabel:disabled { color: $text_disabled; }

/* Buttons: role = primary | secondary (default) | quiet | danger */
$root QPushButton {
    background: $button_fill; color: $button_text; border: 1px solid $button_border; border-radius: ${br}px;
    padding: 0 ${pad_b}px; min-height: ${bh_in}px; font-weight: $w_strong; text-align: center;
}
$root QPushButton:hover { background: $surface_hover; border-color: $border_strong; }
$root QPushButton:pressed { background: $surface_pressed; }
$root QPushButton:disabled { background: $bg; color: $text_disabled; border-color: $border; }
$root QPushButton[role="primary"] { background: $primary; color: $on_primary; border-color: $primary_border; }
$root QPushButton[role="primary"]:hover { background: $primary_hover; border-color: $primary_border; }
$root QPushButton[role="primary"]:pressed { background: $primary_pressed; border-color: $primary_border; }
$root QPushButton[role="primary"]:disabled { background: $bg; color: $text_disabled; border: 1px dashed $border_strong; }
$root QPushButton[role="danger"] { background: $danger_solid; color: $on_danger; border-color: $danger_solid; }
$root QPushButton[role="danger"]:hover { background: $danger_solid_hover; border-color: $danger_solid_hover; }
$root QPushButton[role="danger"]:pressed { background: $danger_solid_pressed; border-color: $danger_solid_pressed; }
$root QPushButton[role="danger"]:disabled { background: $bg; color: $text_disabled; border: 1px dashed $border_strong; }   /* a red button that cannot be used yet must not look red */
$root QPushButton[role="quiet"] { background: transparent; color: $text_muted; border-color: transparent; }
$root QPushButton[role="quiet"]:hover { background: $surface_hover; color: $text; border-color: transparent; }
$root QPushButton[role="quiet"]:pressed { background: $surface_pressed; }
$root QPushButton[role="quiet"]:disabled { background: transparent; color: $text_disabled; }
$root QToolButton { background: transparent; border: none; }

/* Fields */
$root QLineEdit, $root QPlainTextEdit, $root QTextEdit {
    background: $surface; color: $text; border: 1px solid $border; border-radius: ${r}px;
    padding: 0 ${pad_f}px; min-height: ${h_in}px; selection-background-color: $accent; selection-color: $on_accent;
}
$root QPlainTextEdit, $root QTextEdit { padding: ${s1}px ${pad_f}px; }
$root QLineEdit:hover, $root QPlainTextEdit:hover, $root QTextEdit:hover { border-color: $border_strong; }
$root QLineEdit:focus, $root QPlainTextEdit:focus, $root QTextEdit:focus { border-color: $accent; }
$root QLineEdit:disabled, $root QPlainTextEdit:disabled, $root QTextEdit:disabled { background: $bg; color: $text_disabled; }
$root QLineEdit[invalid="true"], $root QFrame[ui="field"][invalid="true"] { border-color: $danger; }
$root QFrame[ui="field"] { background: $surface; border: 1px solid $border; border-radius: ${r}px; }
$root QFrame[ui="field"]:hover { border-color: $border_strong; }

$root QComboBox {
    background: $surface; color: $text; border: 1px solid $border; border-radius: ${r}px;
    padding: 0 ${arrow_w}px 0 ${pad_f}px; min-height: ${h_in}px;
}
$root QComboBox:hover { border-color: $border_strong; }
$root QComboBox:focus { border-color: $accent; }
$root QComboBox[invalid="true"] { border-color: $danger; }
$root QComboBox:disabled { background: $bg; color: $text_disabled; }
$root QComboBox::drop-down { border: none; width: ${arrow_w}px; image: none; }   /* Metashape's own style sheet puts an arrow image here; the kit paints its chevron */
$root QComboBox::down-arrow { image: none; width: 0; height: 0; }
$root QComboBox QAbstractItemView {
    background: $surface; color: $text; border: 1px solid $border_strong; border-radius: ${r}px; outline: 0;
    padding: ${s1}px; selection-background-color: $accent_soft; selection-color: $text_strong;
}
$root QComboBox QAbstractItemView::item { min-height: ${row}px; padding: 0 ${s2}px; border-radius: ${r}px; }

/* Cards, dividers, banners */
$root QFrame[ui="card"] { background: $card; border: 1px solid $border; border-radius: ${rl}px; }
$root QFrame[ui="divider"] { background: $border; border: none; }
$root QFrame[ui="banner"] { border-radius: ${rl}px; border: 1px solid $border; }
$root QFrame[ui="banner"][kind="info"] { background: $info_soft; border-color: $info; }
$root QFrame[ui="banner"][kind="success"] { background: $success_soft; border-color: $success; }
$root QFrame[ui="banner"][kind="warning"] { background: $warning_soft; border-color: $warning; }
$root QFrame[ui="banner"][kind="danger"] { background: $danger_soft; border-color: $danger; }

/* Tables: one skin */
$root QTableView[ui="table"] {
    background: $card; alternate-background-color: $card; color: $text; gridline-color: transparent;
    border: 1px solid $border; border-radius: ${rl}px; outline: none; selection-background-color: $selected_row; selection-color: $text_strong;
}
$root QTableView[ui="table"]::item { padding: 0 ${s2}px; border: none; border-bottom: 1px solid $border; }
$root QTableView[ui="table"]:focus { border-color: $accent; }
$root QTableView[ui="table"]::item:hover { background: $surface_hover; }
$root QTableView[ui="table"]::item:selected, $root QTableView[ui="table"]::item:selected:!active { background: $selected_row; color: $text_strong; }
$root QTableView[ui="table"] QHeaderView { background: transparent; border: none; }
$root QTableView[ui="table"] QHeaderView::section {
    background: $table_header; color: $text_muted; border: none; border-bottom: 1px solid $border_strong;
    padding: 0 ${s2}px; font-size: ${f_small}px; font-weight: $w_strong;
}
$root QTableView[ui="table"] QHeaderView::up-arrow, $root QTableView[ui="table"] QHeaderView::down-arrow { image: none; width: 0px; height: 0px; }   /* the chevron is drawn by DataTable */
$root QTableView[ui="table"]::indicator { width: ${s4}px; height: ${s4}px; border-radius: 4px; border: 1.5px solid $text_muted; background: $surface; }
$root QTableView[ui="table"]::indicator:hover { border-color: $text; }
$root QTableView[ui="table"]::indicator:checked { background: $accent; border-color: $accent; image: url($check); }
$root QTableView[ui="table"]::indicator:disabled { border-color: $border; background: $bg; }
$root QTableView[ui="table"] QTableCornerButton::section { background: $table_header; border: none; }

/* Scroll bars (lists and tables only: windows are sized to their content) */
$root QScrollBar:vertical { background: transparent; width: ${sb}px; margin: 0; }
$root QScrollBar::handle:vertical { background: $surface_pressed; border-radius: ${sb_r}px; min-height: ${s4}px; }
$root QScrollBar::handle:vertical:hover { background: $border_strong; }
$root QScrollBar::add-line:vertical, $root QScrollBar::sub-line:vertical,
$root QScrollBar::add-page:vertical, $root QScrollBar::sub-page:vertical { background: none; height: 0; }
$root QScrollBar:horizontal { background: transparent; height: ${sb}px; margin: 0; }
$root QScrollBar::handle:horizontal { background: $surface_pressed; border-radius: ${sb_r}px; min-width: ${s4}px; }
$root QScrollBar::add-line:horizontal, $root QScrollBar::sub-line:horizontal,
$root QScrollBar::add-page:horizontal, $root QScrollBar::sub-page:horizontal { background: none; width: 0; }

/* Tooltips and menus of every widget inside a kit window (a popup reads the sheet of the widget it belongs to) */
QToolTip {
    background: $surface_pressed; color: $text_strong; border: 1px solid $border_strong; padding: ${s1}px ${s2}px;
    font-family: $family; font-size: ${f_small}px;
}
$root QMenu { background: $surface; color: $text; border: 1px solid $border_strong; border-radius: ${r}px; padding: ${s1}px; }
$root QMenu::item { padding: ${s2}px ${s4}px ${s2}px ${s3}px; border-radius: ${r}px; }
$root QMenu::item:selected { background: $accent_soft; color: $text_strong; }
$root QMenu::item:disabled { color: $text_disabled; background: transparent; }
$root QMenu::separator { height: 1px; background: $border; margin: ${s1}px ${s2}px; }
"""

# Older object names (windows that are not migrated yet), mapped onto the tokens.
_LEGACY = """
$root #aeroRule { border: none; background: qlineargradient(x1:0, y1:1, x2:1, y2:0, stop:0 $logo_0, stop:0.5 $logo_1, stop:1 $logo_2); }
$root #aeroEyebrow { color: $text_muted; font-size: ${f_caption}px; font-weight: $w_strong; letter-spacing: 1.2px; }
$root #aeroValue { color: $text_strong; font-size: ${f_title}px; font-weight: $w_strong; }
$root #aeroMeta { color: $text_muted; font-size: ${f_small}px; }
$root #aeroStat {
    background: $surface; border: 1px solid $border; border-radius: ${rl}px;
}
$root #aeroStatNum { color: $text_strong; font-size: ${f_lead}px; font-weight: $w_strong; }
$root #aeroStatLabel { color: $text_muted; font-size: ${f_caption}px; }
$root QPushButton#aeroRun, $root QPushButton#aeroGo, $root QPushButton#aeroYes {
    background: $primary; color: $on_primary; border: 1px solid $primary_border;
}
$root QPushButton#aeroRun:hover, $root QPushButton#aeroGo:hover, $root QPushButton#aeroYes:hover {
    background: $accent_hover; border-color: $accent_hover;
}
$root QPushButton#aeroRun:disabled, $root QPushButton#aeroGo:disabled, $root QPushButton#aeroYes:disabled {
    background: $surface; color: $text_disabled; border-color: $border;
}
$root QPushButton#aeroNo { background: $danger_solid; color: $on_danger; border: 1px solid $danger_solid; }
$root QPushButton#aeroNo:hover { background: $danger_solid_hover; border-color: $danger_solid_hover; }
$root QPushButton#aeroGhost { background: transparent; color: $text_muted; border: 1px solid $border; }
$root QPushButton#aeroGhost:hover { background: $surface_hover; color: $text; }
$root QPushButton#aeroGhost:disabled { background: transparent; color: $text_disabled; }
$root QPushButton#aeroGhost[round="true"] { padding: 0; border-radius: ${pill_b}px; }
$root QPushButton#aeroGhost[round="true"]:checked { background: $surface_pressed; border-color: $border_strong; }
$root QSlider#aeroSlider::groove:horizontal { height: ${s1}px; background: $surface_pressed; border-radius: 2px; }
$root QSlider#aeroSlider::handle:horizontal { background: $accent; width: ${s3}px; margin: -${s1}px 0; border-radius: ${s3}px; }
$root QListWidget#aeroThumbs { background: $bg; border: 1px solid $border; border-radius: ${r}px; outline: 0; }
$root QListWidget#aeroThumbs::item { color: $text; border: 1px solid transparent; border-radius: ${r}px; }
$root QListWidget#aeroThumbs::item:selected { background: $accent_soft; border: 1px solid $accent; color: $text_strong; }
$root #aeroGrid { background: transparent; border: none; outline: none; }
$root #aeroGrid::item { padding: 0 ${s1}px; border: none; }
$root #aeroGrid::item:hover { background: $surface_hover; }
$root #aeroGrid::item:selected, $root #aeroGrid::item:selected:!active { background: $accent_soft; color: $text_strong; }
$root #aeroGrid QHeaderView { background: transparent; border: none; }
$root #aeroLog { background: $bg; border: 1px solid $border; border-radius: ${r}px; color: $text_muted; font-family: $mono, "Courier New", monospace; font-size: ${f_caption}px; }
$root QCheckBox { color: $text; spacing: ${s2}px; padding: 3px 0; background: transparent; }
$root QCheckBox::indicator { width: ${s4}px; height: ${s4}px; border-radius: 3px; border: 1px solid $border_strong; background: $surface; }
$root QCheckBox::indicator:checked { background: $accent; border-color: $accent; image: url($check); }
$root QCheckBox::indicator:disabled { border-color: $border; }
$root QRadioButton { color: $text; spacing: ${s3}px; background: transparent; }
$root QRadioButton:disabled { color: $text_disabled; }
$root QRadioButton::indicator { width: ${radio}px; height: ${radio}px; image: url($radio_off); }
$root QRadioButton::indicator:checked { image: url($radio_on); }
$root QFrame#aeroOption { background: $bg; border: 1px solid $border; border-radius: ${rl}px; }
$root QFrame#aeroOption[on="true"] { background: $accent_soft; border: 1px solid $accent; }
$root QFrame#aeroOption[on="true"][danger="true"] { background: $danger_soft; border: 1px solid $danger; }
"""

_POPUP = """
QToolTip { background: $surface_pressed; color: $text_strong; border: 1px solid $border_strong; padding: ${s1}px ${s2}px;
    font-family: $family; font-size: ${f_small}px; }
"""

_MENU = """
QMenu { background: $surface; color: $text; border: 1px solid $border_strong; border-radius: ${r}px; padding: ${s1}px;
    font-family: $family; font-size: ${f_body}px; }
QMenu::item { padding: ${s2}px ${s4}px ${s2}px ${s3}px; border-radius: ${r}px; }
QMenu::item:selected { background: $accent_soft; color: $text_strong; }
QMenu::item:disabled { color: $text_disabled; background: transparent; }
QMenu::separator { height: 1px; background: $border; margin: ${s1}px ${s2}px; }
"""

_TABLE = """
QTableWidget, QTableView {
    background: $surface; color: $text; gridline-color: transparent; border: 1px solid $border; border-radius: ${rl}px; outline: none;
    selection-background-color: $accent_soft; selection-color: $text_strong; font-family: $family; font-size: ${f_body}px;
}
QTableWidget::item, QTableView::item { padding: 0 ${s2}px; border: none; }
QTableWidget::item:selected, QTableWidget::item:selected:!active, QTableView::item:selected { background: $accent_soft; color: $text_strong; }
QHeaderView::section { background: $surface; color: $text_muted; border: none; border-bottom: 1px solid $border_strong; padding: ${s2}px; font-weight: $w_strong; }
QTableCornerButton::section { background: $surface; border: none; }
"""

_POPOVER = """
QFrame#uikitPopover { background: $surface_pressed; border: 1px solid $border_strong; border-radius: ${rl}px; }
QFrame#uikitPopover QLabel { background: transparent; color: $text_strong; font-family: $family; font-size: ${f_small}px; }
"""


def _legacy_values(values):
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "ui")
    out = dict(values)
    out["check"] = os.path.join(base, "check.png").replace("\\", "/")
    out["radio_off"] = os.path.join(base, "radio_off.png").replace("\\", "/")
    out["radio_on"] = os.path.join(base, "radio_on.png").replace("\\", "/")
    out["radio"] = metrics.px(20)
    return out


def _cached(name, template, legacy=False):
    key = (name, tokens.active_skin(), tokens.active_font(), metrics.scale())
    if key not in _cache:
        values = _values()
        if legacy:
            values = _legacy_values(values)
        values["root"] = ROOT
        _cache[key] = Template(template).substitute(values)
    return _cache[key]


def stylesheet():
    """The full style sheet of a kit window at the current scale (built once per scale)."""
    return _cached("main", _KIT + _LEGACY, legacy=True)


def tooltip_stylesheet():
    """Tooltip rule, for a widget that is not inside a kit window."""
    return _cached("tip", _POPUP)


def menu_stylesheet():
    """Tooltip and menu rules, for a menu that is not inside a kit window (a pop-up menu is a window of its own)."""
    return _cached("menu", _POPUP + _MENU)


def table_stylesheet():
    """Rules for one older-style QTableWidget (it carries its own sheet, outside the kit's DataTable)."""
    return _cached("table", _TABLE)


def popover_stylesheet():
    return _cached("popover", _POPOVER)


def apply(widget):
    """Give `widget` (the root of a window) the kit sheet and its name. Does nothing when it already has the current one."""
    widget.setObjectName("aeroPanel")
    metrics.ensure_fonts()
    sheet = stylesheet()
    if widget.styleSheet() != sheet:
        widget.setStyleSheet(sheet)


def set_prop(widget, name, value):
    """Set a dynamic property and re-polish the widget, only when the value changed."""
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def restyle_all(relayout=False):
    """Give every open kit window and dock the style sheet of the active skin and font and repaint every widget (custom-painted ones read
    the tokens when they paint, so a repaint is all they need). `relayout`: the text sizes changed too (a font switch), so the sizes of
    the widgets and of the windows are asked again."""
    from ..qt import QtWidgets
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    sheet = stylesheet()
    widgets = list(app.allWidgets())
    for widget in widgets:
        if widget.objectName() == "aeroPanel" and widget.styleSheet() != sheet:
            widget.setStyleSheet(sheet)
    for widget in widgets:
        refresh = getattr(widget, "refresh_skin", None)
        if refresh is not None:
            refresh()
        widget.update()
    if relayout:
        for widget in widgets:
            widget.updateGeometry()
        for widget in widgets:
            if widget.isWindow() and widget.isVisible() and widget.layout() is not None:
                widget.layout().invalidate()
                widget.layout().activate()


def switch_skin(name=None, save=True):
    """Change the look of the whole suite NOW: `name`, or the next one in the cycle dark -> medium -> light when None. Every open window
    is restyled and repainted; the choice is saved in the setting `ui.skin` (so the next start uses it) unless `save` is False.
    Returns the name of the skin now active. This is the function a test script or a live session calls to force a skin."""
    name = tokens.next_skin() if name is None else name
    name = tokens.use(name)
    metrics.ensure_fonts()
    if save:
        try:
            from .. import config
            config.put(tokens.SETTING[0], tokens.SETTING[1], name)
        except Exception:
            pass
    restyle_all()
    return name


def switch_font(name=None, save=True):
    """Change the typeface of the whole suite NOW, like `switch_skin` does for the colours: `name` (system, brand or clear; an unknown one is
    `system`), or the next one in the cycle when None. Every open window is restyled, re-measured and repainted; the choice is saved in the
    setting `ui.font` unless `save` is False. Returns the name of the font now active."""
    name = tokens.next_font() if name is None else name
    name = tokens.use_font(name)
    metrics.ensure_fonts()
    if save:
        try:
            from .. import config
            config.put(fonts.SETTING[0], fonts.SETTING[1], name)
        except Exception:
            pass
    restyle_all(relayout=True)
    return name
