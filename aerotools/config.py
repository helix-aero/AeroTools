"""Paths, tool registry and persistent settings of AeroTools.

The settings are stored per workstation (LOCALAPPDATA) and not on the server:
each PC keeps its own ticked boxes, the code stays shared.
"""

import copy
import json
import os

APP_NAME = "AeroTools"
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
#               False => a one-off window (Aero-Space): closed at reload, never reopened
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
    {
        "id": "fastspace",
        "menu": "Aero-Space",
        "title": "Aero-Space",
        "subtitle": "tool.fastspace.subtitle",
        "shortcut": None,
        "ready": True,
        "reopen": False,
    },
    {
        "id": "aeroexports",
        "menu": "Aero-Exports",
        "title": "Aero-Exports",
        "subtitle": "tool.aeroexports.subtitle",
        "shortcut": None,
        "ready": True,
        "reopen": False,
    },
    # The AI detection (formerly AIGCP) is part of Aero-GCP: no menu entry of its own.
]


def tool(tool_id):
    """Registry entry of a tool, or None."""
    for entry in TOOLS:
        if entry["id"] == tool_id:
            return entry
    return None


# --------------------------------------------------------------------------
# Per-workstation settings
# --------------------------------------------------------------------------

def settings_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "HELIX", APP_NAME)


def settings_path():
    return os.path.join(settings_dir(), "settings.json")


# Renamed sections: {new name: old name}. Aero-Exports used to be called "fastexport": the choices already saved
# (cad_format, cad_images, cad_clip, dem_look...) are copied to the new section at the first read, leaving the
# old one untouched (going back loses nothing).
LEGACY_SECTIONS = {"aeroexports": "fastexport"}


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
