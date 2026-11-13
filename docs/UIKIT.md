# UI kit

One design system for every window of the suite: `aerotools/uikit/` (core code, every pack gets it). Ink-dark by default (also Medium and
Light), one accent (the brand green), pill buttons, Rubik headings and Manrope text, dense but calm. A window built from the kit needs no colour, font size, radius or pixel size of its own.

```python
from aerotools import uikit

class ExportWindow(uikit.ToolWindow):
    TITLE = "Export"                                  # or i18n.t("...")
    def __init__(self, parent=None):
        super().__init__(parent, info=i18n.t("export.info"))
        body = QtWidgets.QWidget()
        lay = uikit.vbox(spacing=24, parent=body)
        section = uikit.Section(i18n.t("export.output"))
        form = section.form()
        self.pixel = uikit.NumberField(0.05, 0.001, 10, 3, 0.01, "m")
        form.add_row(i18n.t("export.pixel"), self.pixel, info=i18n.t("export.pixel.tip"))
        lay.addWidget(section)
        self.set_content(body)
        self.set_primary(i18n.t("export.run"), self.start)

    def start(self):
        self.run(self.work, mode="cooperative", success=i18n.t("export.done"))   # Metashape work stays on the UI thread
```
`ExportWindow.open(parent)` builds the window once and raises it on the next call.

## Tokens (`uikit/tokens.py`, the only place a value is read; the values live in the skin)

| Group | Values |
|---|---|
| Colour roles | `BG` window, `SURFACE` fields (+ `_HOVER`, `_PRESSED`), `CARD` cards and table bodies, `BORDER` hairline (`BORDER_STRONG` on hover), `TEXT_STRONG`, `TEXT`, `TEXT_MUTED`, `TEXT_DISABLED`, `ACCENT` green (+ `ON_ACCENT`), `PRIMARY_FILL` / `ON_PRIMARY` of the primary button, `BUTTON_FILL` / `BUTTON_BORDER` / `BUTTON_TEXT`, `SELECTED_ROW`, `TABLE_HEADER`, `SUCCESS`, `WARNING`, `DANGER`, `INFO` with an opaque `*_SOFT` tint each, `OVERLAY` / `ON_OVERLAY` / `SHADOW_INK` for what is drawn over a photo, `DATA_PALETTE` |
| Type (px at 100 %) | caption 11, small 12, body 13, lead 15, title 21, display 26; Manrope for text, Rubik for headings |
| Space | 4 8 12 16 24 32 (`XS S M L XL XXL`); 20 inside a window, 24 between sections, 12 between form rows, 8 inside a row |
| Radius | 10 controls, 12 cards and banners, pill buttons |
| Size | one control height and one button height (36, equal on purpose: a button next to a field lines up), icon 16, step dot 24 |
| Focus | 2 px ring, green (ink or white on a filled button), drawn over the control so nothing moves |

`tests/test_uikit_tokens.py` computes the WCAG contrast of every text / background pair of every skin (4.5:1 for text, 3:1 for
disabled text and graphics). Add a colour or a pair there first.

## Skins

The whole look lives in ONE small data-only file per skin, `aerotools/uikit/skins/<name>.py`, like a style sheet: change the file, the
tools do not change. Three ship: `dark` (the default), `medium` (Metashape's own greys) and `light` (cream and white). They share one
geometry and one type scale, so switching only recolours.

```python
SKIN = {
    "label": "Dark", "dark": True,
    "colors": {"bg": "#111214", "surface": ..., "text": ..., "accent": ..., "on_primary": ..., "overlay": ..., ...},   # every colour role
    "fills": {"primary": "#ffffff" or ["#a", "#b"] (a gradient), "button": ..., "card": ..., "table_header": ..., "selected_row": ...,
              "header": None or a colour / gradient, "header_rule": None or a colour / gradient under the header},
    "space": [4, 8, 12, 16, 24, 32],
    "shape": {"radius": 10, "radius_l": 12, "button_radius": 999, "control_h": 36, "button_h": 36, "ring_w": 2, "window_margin": 20, ...},
    "data": {"palette": [...]},
}
```

To make a skin: copy `dark.py` to `skins/<name>.py`, change the values, run `python -c "from aerotools.uikit import tokens; print(tokens.contrast_failures('<name>'))"`
(it must print `[]`), and add the name to `tokens.ORDER` if the theme button should cycle through it. A missing or malformed key falls back to the dark
skin's value (`tokens.problems(skin)` lists what is wrong). A skin holds colours, fills, spacing and shapes only: families, sizes and weights
are the font's (next section). `text_secondary` is the colour of form-row labels, `text_muted` of captions and hints; both must pass AA on every
surface they sit on.

The active skin is the setting `ui.skin` (`dark` when unset or unknown), read when the kit loads. The round button at the right of every
window header cycles Dark, Medium, Light and calls `style.switch_skin()`: every open window and dock is restyled and repainted at once
and the choice is saved. A test or a live session can force a skin with `uikit.style.switch_skin("light")` (add `save=False` to leave the
setting alone); `tokens.use(name)` alone changes the tokens without restyling open windows. Custom-painted widgets read the tokens when they
paint, never at import: do not keep a token in a module constant.

## Fonts

ONE data table, `aerotools/uikit/fonts.py`, holds the three choices; the skin does not know about them, so any font goes with any skin.

| name | what it is |
|---|---|
| `system` (default) | Segoe UI, the Windows UI font and Metashape's own. Body 13 px, caption and small 12 px |
| `brand` | Rubik headings, Manrope text, labels in Manrope Medium (static files, see below) |
| `clear` ("Large") | Segoe UI one pixel larger on every step (caption 13, body 14 ... display 27), labels in Segoe UI Semibold |

Each entry gives the families (a list: first installed wins, Segoe UI and generic faces behind), the six sizes (`caption` ... `display`, never
below `fonts.MIN_SIZE` = 12 px at 100 %), the weights for text / strong / labels, and the files of `assets/fonts` to register. To add a font:
add an entry to `FONTS` (and its name to `ORDER`, plus the key `ui.font_<name>` in `lang/en.py`), nothing else; `tests/test_uikit_fonts.py`
checks that the scale is complete and not below 12 px.

The choice is the setting `ui.font` (`system` when unset or unknown). `uikit.style.switch_font(name=None, save=True)` applies it NOW to every
open window (style sheet cached per skin + font + scale, sizes asked again, custom-painted widgets repaint from the tokens) and saves it;
`tokens.active_font()` names it, `tokens.use_font(name)` changes the tokens only. It is changed from: the "Aa" round button next to the theme
button in every header (cycles System, Brand, Large), the Appearance tab of Aero-GCP Settings (Theme and Font, with a sample line) and the
Appearance row of Install plugins; all follow each other because `restyle_all` calls `refresh_skin` on every widget.

Why static font files: Qt 5.15 (Metashape 2.2) loads a variable `.ttf` at one weight only, its lightest (Manrope ExtraLight 200, Rubik
Light 300), whatever `font-weight` says, which made the text thin. `assets/fonts` therefore holds static instances cut from the variable fonts
(OFL allows it): Regular (400) and a Bold slot holding the 600 weight, so `font-weight: 600` finds a real face; "Manrope Medium" (500) is a family
of its own used for labels.

Legibility floor (all fonts, all skins): form labels use `text_secondary` at body size; status text (`label(..., "status", tone)`) is body size,
weight 600, in a semantic colour next to a glyph; nothing is smaller than 12 px except the AERO wordmark; uppercase or tracked text only at 12 px
or more; `text_muted` reaches 4.5:1 on every surface (`tokens.pairs` lists them).

## Scaling

`metrics.px(n)` turns design pixels into pixels of this screen (logical DPI / 96, 1 when Qt scales by itself); fonts are set in pixels
through the same helper so text and its box scale together. Icons are drawn with QPainter (`uikit/icons.py`). The style sheet is built once per
scale and cached (`style.stylesheet()`); apply it with `style.apply(root)` (done by `ToolWindow`). Change a look with `style.set_prop(widget, "role", "danger")`,
never with `setStyleSheet`.

## Components

| Name | What it is |
|---|---|
| `ToolWindow` | Shell of a tool window: header (mark, title, "i", subtitle), optional `StepBar`, `stack`, `banner`, `footer`. Owns size (design size or content, clamped to the screen, centred on the host screen), Esc / Enter, the close veto while busy, `open()` single instance. Aliases: `next_button`, `back_button`, `cancel_button`, `status`, `stack`, `steps`, `screens` |
| `DockShell` | The same header / banner / status / optional footer for a `QDockWidget`; content goes in `body` |
| `StepBar`, `StepScreen` | Wizard steps; a page overrides `enter`, `leave`, `can_leave`, `next_enabled`, `next_text`, `show_back`, `hint` and emits `changed` |
| `FooterBar` | `[Cancel or Close] [extras] ... status or progress ... [Back] [Primary]`, always in this order; `add_left()` for secondary or destructive actions |
| `FormGrid` | Rows `label / control / (i) / inline error` on one grid; `add_row(text, control, info=None)` returns a row with `set_error()` |
| `NumberField`, `TextField` | Fields with the error edge; `NumberField` has range, decimals, unit, Up / Down, `valueChanged`, `errorChanged` |
| `ChoiceBox`, `Segmented`, `Toggle`, `PathField` | Drop-down (Tab reachable), 2 to 5 short options, on / off switch, folder or file chooser (override `pick()` in tests) |
| `Button`, `IconButton` | Roles `primary` (one per view, right-most), `secondary`, `quiet`, `danger` |
| `InfoButton` | The "i": all explanations live behind it, in a styled bubble (hover, click, keyboard focus) |
| `Section`, `Disclosure` | A titled block; content that does not fit goes behind a closed Disclosure. No scroll bars in windows: split into steps or sections |
| `Card`, `Stat`, `KeyValue` | A bordered box; a figure with its caption (tone-coloured); `key  value` lines whose values wrap and break a long path |
| `ChoiceCard`, `ListRow`, `ChoiceGroup` | Radio card, selectable list row, the group that makes them exclusive |
| `Chip`, `MultiSelect`, `Slider` | Small on / off pill; drop-down with ticks; value slider (optional typed box, Shift = ten steps) |
| `ThemeButton` | The round Dark / Metashape / Light button of every header |
| `FontButton` | The round "Aa" button next to it: System / Brand / Large |
| `ThemeChoice`, `FontChoice`, `AppearanceBar` | The same two choices as Segmented controls (Settings, Install plugins), kept in sync with the buttons |
| `present`, `DisclosureGroup`, `WrapLabel` | Single-instance window helper; exclusive disclosures; wrapped text with honest sizes (`label(..., break_long=True)` breaks file names) |
| `Banner` | `info / success / warning / danger`, optional action, optional Details, close X. The single place messages and errors end up |
| `EmptyState` | Icon, title, one line, optional action (empty, error, loading) |
| `DataTable` | The one table skin: sortable, elided cells, built-in empty / loading / error states; check and status columns, `cell(text, tone=, bold=, icon=)`, disabled rows |
| `ProgressRow`, `StepProgress` | Bar with text, percent and Cancel; list of steps with state glyphs |
| `TaskRunner`, `Progress` | `threaded` (pure Python) or `cooperative` (UI thread, Qt pumped; anything that touches Metashape); progress, cancel, `Cancelled`, `failed` signal |
| `confirm`, `inform`, `error` | Modal helpers replacing QMessageBox and Metashape's message box for questions and notices |

## Rules

- One primary button per view, right-most. Cancel / Close on the left, then Back, then the primary. Destructive actions go left (`add_left`), never beside the primary.
- Shared texts: `ui.cancel`, `ui.close`, `ui.back`, `ui.next`, `ui.done`, `ui.retry`, `ui.ok`, `ui.browse`. Never add a "Close" or "Cancel" key of your own.
- Little text. One short label, and the explanation behind an `InfoButton` (a couple of sentences). Plain `setToolTip` is styled too.
- Long work goes through `ToolWindow.run()` (or a `TaskRunner`): no `processEvents` in a window, no polling timer, errors end in the danger banner with Retry.
- Keyboard: Enter presses the primary (or the focused button), Esc cancels a running task, else closes; every control is a Tab stop in visual order and shows the ring.
- Every state is designed: disabled, busy (footer progress), empty (`EmptyState`), error (`Banner`).

## Verifying

`python -B tests_qt/run_qt_uikit.py --shots DIR [--skin light] [--only100]` builds the gallery (every component in every state) and renders PNGs at 100, 125 and 150 %,
then runs the behaviour checks. `tests/test_uikit_*.py` cover tokens, the style sheet, the old names (`theme`, `ui`, `dialogs`) and the pack;
`test_uikit_qt.py` runs the harness when a Qt binding is installed.

## Migrating a window

1. Derive from `ToolWindow` (or `DockShell`), delete the hand-made root, header, footer, `show()`, size helpers and Esc / Enter handling.
2. Replace local widgets by the kit's (`InfoButton`, `Segmented`, `NumberField`, `Disclosure`, `DataTable`, `Banner`); delete local style sheets and `setStyleSheet` calls.
3. Move long calls to `self.run(func, mode=...)`; replace `QMessageBox`, `msapi.message_box` and status-label errors by `show_banner` / `show_error` / `confirm`.
4. Keep the attribute names the Qt harness drives (`next_button`, `back_button`, `status`, `stack`).
5. Old names keep working meanwhile: `theme.*`, `ui.button`, `ui.styled_root`, `dialogs.ask` are thin aliases onto the kit.
