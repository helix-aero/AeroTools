"""The AeroTools UI kit: one design system for every tool window. See docs/UIKIT.md.

    from aerotools import uikit
    window = uikit.ToolWindow.open(parent)

Names are loaded on first use, so `aerotools.uikit.tokens` (plain data) imports without Qt.
"""

_WHERE = {
    # tokens and scaling
    "tokens": "tokens", "fonts": "fonts", "metrics": "metrics", "style": "style", "icons": "icons",
    "px": "metrics",
    # shells
    "ToolWindow": "shell", "present": "shell", "HeaderBand": "shell", "DockShell": "shell", "StepBar": "shell", "StepScreen": "shell", "FooterBar": "shell", "Header": "shell",
    # controls
    "Button": "controls", "IconButton": "controls", "InfoButton": "controls", "TextField": "controls", "NumberField": "controls",
    "ChoiceBox": "controls", "Segmented": "controls", "Toggle": "controls", "PathField": "controls", "ElidedLabel": "controls",
    "label": "controls", "button": "controls", "set_tone": "controls", "WrapLabel": "controls", "ThemeButton": "controls", "FontButton": "controls", "ThemeChoice": "controls", "FontChoice": "controls", "AppearanceBar": "controls", "breakable": "controls", "unbreak": "controls",
    # blocks
    "FormGrid": "blocks", "Section": "blocks", "Disclosure": "blocks", "Banner": "blocks", "EmptyState": "blocks", "Divider": "blocks",
    "Glyph": "blocks", "DisclosureGroup": "blocks", "vbox": "blocks", "hbox": "blocks",
    # data
    "DataTable": "table", "Column": "table", "cell": "table", "draw_check": "table",
    "Card": "cards", "Stat": "cards", "KeyValue": "cards", "ChoiceCard": "cards", "ListRow": "cards", "ChoiceGroup": "cards", "Chip": "cards",
    "MultiSelect": "cards", "Slider": "cards",
    "ProgressRow": "progress", "StepProgress": "progress", "ProgressBar": "progress",
    "TaskRunner": "tasks", "Progress": "tasks", "Cancelled": "tasks",
    "confirm": "messages", "inform": "messages", "error": "messages", "MessageBox": "messages",
}

__all__ = sorted(_WHERE)


def __getattr__(name):
    module = _WHERE.get(name)
    if module is None:
        raise AttributeError("module 'aerotools.uikit' has no attribute " + repr(name))
    import importlib
    loaded = importlib.import_module("." + module, __name__)
    value = loaded if name == module else getattr(loaded, name)
    globals()[name] = value
    return value
