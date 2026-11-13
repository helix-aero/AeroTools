"""The fonts of the UI kit: ONE data table. Adding a font later is a data change here, nowhere else.

Pure Python, no Qt. The choice is the setting `ui.font` (`system` when unset or unknown), independent of the colour skin (dark / medium /
light): a skin says colours and shapes, a font says families, sizes, weights and the files to register. `tokens.use_font(name)` loads one,
`style.switch_font(name)` applies it to every open window.

Each entry:
  label            what the UI calls it (the i18n key `ui.font_<name>` has the words shown to the user)
  family           text (a list: the first one installed wins, so the last is a safe fallback)
  heading_family   titles, leads, display
  label_family     form-row labels (a "medium" or "semibold" face where one exists as its own family)
  mono             log and code
  scale            px at 100 % for caption, small, body, lead, title, display (never below MIN_SIZE)
  weight_regular   weight of plain text; weight_strong: buttons, headings, status; weight_label: form-row labels
  label_case / label_spacing   "upper" + tracking only for text of MIN_SIZE or more (none of the shipped fonts uses them)
  files            font files of assets/fonts to register (static files: Qt 5 reads one weight per variable font, so none is used)

Why static files: Qt 5.15 (Metashape 2.2) shows a variable .ttf at ONE weight, its lightest (Manrope ExtraLight, Rubik Light), whatever
`font-weight` says. The bundled Manrope and Rubik are therefore static instances cut from the variable fonts: Regular (400) and a Bold
slot that holds the 600 weight, so `font-weight: 600` finds a real face. "Manrope Medium" (500) is a family of its own, used for labels.
"""

DEFAULT = "system"
ORDER = ("system", "brand", "clear")      # the order the font button cycles through
SETTING = ("ui", "font")                  # config section / key of the choice
MIN_SIZE = 12                             # no text of the kit is smaller than this at 100 % (the AERO wordmark is not text)
ROLES = ("caption", "small", "body", "lead", "title", "display")

_SYSTEM = ["Segoe UI", "Helvetica Neue", "Arial", "sans-serif"]

FONTS = {
    # What the product looked like before Rubik and Manrope, and what Metashape itself uses.
    "system": {
        "label": "System",
        "family": _SYSTEM,
        "heading_family": _SYSTEM,
        "label_family": _SYSTEM,
        "mono": ["Consolas", "Courier New"],
        "scale": {"caption": 12, "small": 12, "body": 13, "lead": 15, "title": 21, "display": 26},
        "weight_regular": 400,
        "weight_strong": 600,
        "weight_label": 400,
        "label_case": "none",
        "label_spacing": 0.0,
        "files": [],
    },
    # The suite's own faces: Rubik for headings, Manrope for text (static instances, see the module doc).
    "brand": {
        "label": "Brand",
        "family": ["Manrope"] + _SYSTEM,
        "heading_family": ["Rubik", "Manrope"] + _SYSTEM,
        "label_family": ["Manrope Medium", "Manrope"] + _SYSTEM,
        "mono": ["Consolas", "Courier New"],
        "scale": {"caption": 12, "small": 12, "body": 13, "lead": 15, "title": 21, "display": 26},
        "weight_regular": 400,
        "weight_strong": 600,
        "weight_label": 400,
        "label_case": "none",
        "label_spacing": 0.0,
        "files": ["Manrope-Regular.ttf", "Manrope-SemiBold.ttf", "Manrope-Medium.ttf", "Rubik-Regular.ttf", "Rubik-SemiBold.ttf"],
    },
    # Legibility first: the system face one pixel larger on every step, labels in its semibold face.
    "clear": {
        "label": "Large",
        "family": _SYSTEM,
        "heading_family": _SYSTEM,
        "label_family": ["Segoe UI Semibold"] + _SYSTEM,
        "mono": ["Consolas", "Courier New"],
        "scale": {"caption": 13, "small": 13, "body": 14, "lead": 16, "title": 22, "display": 27},
        "weight_regular": 400,
        "weight_strong": 600,
        "weight_label": 400,
        "label_case": "none",
        "label_spacing": 0.0,
        "files": [],
    },
}


def names():
    """The fonts in cycling order (system first)."""
    return [n for n in ORDER if n in FONTS] + [n for n in FONTS if n not in ORDER]


def known(name):
    return isinstance(name, str) and name in FONTS


def get(name):
    """The entry of a font (a copy). An unknown name gives `system`."""
    import copy
    return copy.deepcopy(FONTS[name if known(name) else DEFAULT])


def next_font(name):
    """The font after `name` in the cycle system -> brand -> clear -> system."""
    order = names()
    return order[(order.index(name) + 1) % len(order)] if name in order else DEFAULT


def problems(name):
    """What is wrong in one entry, as a list of texts (empty when fine): a complete scale, no size below MIN_SIZE, families, weights."""
    entry = FONTS.get(name)
    if not isinstance(entry, dict):
        return ["unknown font " + str(name)]
    out = []
    scale = entry.get("scale") or {}
    for role in ROLES:
        value = scale.get(role)
        if not isinstance(value, int):
            out.append("scale." + role + " missing")
        elif value < MIN_SIZE:
            out.append("scale.%s is %d, below %d" % (role, value, MIN_SIZE))
    sizes = [scale.get(r) for r in ROLES if isinstance(scale.get(r), int)]
    if sizes != sorted(sizes):
        out.append("scale does not grow")
    for key in ("family", "heading_family", "label_family", "mono"):
        value = entry.get(key)
        if not (isinstance(value, (list, tuple)) and value and all(isinstance(v, str) and v for v in value)):
            out.append(key + " is not a list of family names")
    for key in ("weight_regular", "weight_strong", "weight_label"):
        if not isinstance(entry.get(key), int) or not 100 <= entry[key] <= 900:
            out.append(key + " is not a weight")
    if not isinstance(entry.get("files"), (list, tuple)):
        out.append("files is not a list")
    if entry.get("label_case") == "upper" or float(entry.get("label_spacing") or 0) > 0:
        if scale.get("caption", 0) < MIN_SIZE:
            out.append("tracked / upper-case labels need a size of at least %d" % MIN_SIZE)
    return out
