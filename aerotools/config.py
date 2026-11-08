"""Paths, tool registry and persistent settings of AeroTools.

The settings are stored per workstation (LOCALAPPDATA) and not next to the code:
each PC keeps its own ticked boxes.
"""

import copy
import json
import os

APP_NAME = "AeroTools"
APP_DIR_NAME = "AeroToolsApp"
VENDOR = "HELIX AERO"

# Repository root: the folder that contains the "aerotools" package.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ASSETS = os.path.join(ROOT, "assets")
FONTS = os.path.join(ASSETS, "fonts")

# Registry of the tools of the Aero menu.
#   id        : technical identifier, also the name of the sub-package
#   menu      : label of the Metashape menu (ASCII: on some versions the API goes through Qt in
#               latin-1, so accents are risky). For a tool that is not ready, bootstrap appends
#               "(coming soon)".
#   title     : name of the tool
#   subtitle  : text key (aerotools/lang/en.py) of the description of the tool
#   shortcut  : Metashape keyboard shortcut, or None
#   ready     : False => the menu entry shows "(coming soon)" instead of loading
#   reopen    : True => "Reload AeroTools" reopens this tool (the panel that stays on screen);
#               False => a one-off window: closed at reload, never reopened
TOOLS = [
    {
        "id": "fastgcp",
        "menu": "Aero-GCP",
        "title": "Aero-GCP",
        "subtitle": "tool.fastgcp.subtitle",
        "shortcut": "F8",
        "ready": True,
        "reopen": True,
    },
    # The AI detection (formerly AIGCP) is part of Aero-GCP: no menu entry of its own.
]


def tool(tool_id):
    """Registry entry of a tool, or None."""
    for entry in TOOLS:
        if entry["id"] == tool_id:
            return entry
    return None


# Packs: what "Aero > Install plugins" installs, one per tool. Everything not listed here is the core (menu, update, shared code,
# fonts, installer, docs), always present. A pack owns the files under its `paths` (prefixes of the paths in manifest.json).
#   id        same as the tool it installs
#   paths     folders (ending with /) the pack owns; the library or model files a tool needs travel with it
#   marker    a file that exists only when the pack is installed
#   requires  other packs this one imports code from (tests/test_packs.py checks it against the imports)
#   setup     optional: module (by name, the core never imports a tool) whose `run(parent, engine, training, confirm)` finishes the
#             installation once the files are there (Aero-GCP: the AI detection engine)
CORE = "core"
PACKS = [
    {"id": "fastgcp", "paths": ["aerotools/tools/fastgcp/", "models/"], "marker": "aerotools/tools/fastgcp/tool.py",
     "requires": [], "setup": "aerotools.tools.fastgcp.install_ui"},
]


def pack(pack_id):
    """Registry entry of a pack, or None."""
    for entry in PACKS:
        if entry["id"] == pack_id:
            return entry
    return None


def pack_of(path):
    """Pack that owns a file of the manifest (path with /), or CORE. The longest matching prefix wins."""
    clean = path.replace("\\", "/")
    best, length = CORE, -1
    for entry in PACKS:
        for prefix in entry["paths"]:
            if clean.startswith(prefix) and len(prefix) > length:
                best, length = entry["id"], len(prefix)
    return best


def pack_closure(pack_ids):
    """The packs asked for plus everything they require, requirements first."""
    order = []

    def visit(pack_id):
        entry = pack(pack_id)
        if entry is None or pack_id in order:
            return
        for needed in entry["requires"]:
            visit(needed)
        order.append(pack_id)

    for pack_id in pack_ids:
        visit(pack_id)
    return order


def dependents(pack_id):
    """Packs that require this one (directly or not)."""
    return [entry["id"] for entry in PACKS if entry["id"] != pack_id and pack_id in pack_closure([entry["id"]])]


# --------------------------------------------------------------------------
# Per-workstation settings
# --------------------------------------------------------------------------

def settings_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "HELIX", APP_NAME)


def settings_path():
    return os.path.join(settings_dir(), "settings.json")


def app_dir():
    """Where the installer puts the code it downloads (install/install_core.ps1 writes the same path): one writable folder per user."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "HELIX", APP_DIR_NAME)


# Renamed sections: {new name: old name}. When a tool is renamed, the choices already saved are copied to the new section at
# the first read, leaving the old one untouched (going back loses nothing).
LEGACY_SECTIONS = {
}


def _migrate_legacy_sections(data):
    """Copies the old sections to the new ones if these do not exist. True if `data` changed."""
    changed = False
    for new_name, old_name in LEGACY_SECTIONS.items():
        old = data.get(old_name)
        if new_name not in data and isinstance(old, dict):
            data[new_name] = copy.deepcopy(old)
            changed = True
    return changed


def load_settings():
    """Settings of the workstation. Never raises: a broken file gives empty settings."""
    try:
        with open(settings_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return {}
    except Exception:
        return {}
    if _migrate_legacy_sections(data):
        save_settings(data)
    return data


def save_settings(data):
    """Writes the settings. Returns True if the write succeeded."""
    try:
        os.makedirs(settings_dir(), exist_ok=True)
        tmp = settings_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, settings_path())
        return True
    except Exception:
        return False


def section(name):
    """A settings section as a dict. A corrupted section gives an empty one."""
    value = load_settings().get(name, {})
    return value if isinstance(value, dict) else {}


def get(section_name, key, default=None):
    return section(section_name).get(key, default)


def put(section_name, key, value):
    data = load_settings()
    current = data.get(section_name)
    if not isinstance(current, dict):
        current = {}
        data[section_name] = current
    current[key] = value
    return save_settings(data)
