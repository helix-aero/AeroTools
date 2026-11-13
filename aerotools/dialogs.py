"""Dialog boxes (Qt): a question and a notice, in the kit's language (see `uikit/messages.py`).

Under Metashape a QMessageBox cuts its text when it is long; these boxes have a fixed width, wrapped text that takes all the height it
needs, and the panel style. Buttons follow the one footer rule: the secondary answer on the left, the primary (right-most) on the right.

New code: `uikit.confirm`, `uikit.inform`, `uikit.error`, or `ToolWindow.show_banner` for a message that does not need a modal box.
`ask` (Yes / No) and `inform` keep their old signatures.
"""

from . import i18n

# Older object names of the buttons -> kit roles.
_ROLES = {"aeroYes": "primary", "aeroRun": "primary", "aeroNo": "secondary", "aeroGhost": "secondary"}


def _run(dialog):
    return (getattr(dialog, "exec_", None) or getattr(dialog, "exec"))()


def _build(parent, title, text, buttons):
    """Modal window: `buttons` = [(text, returned value, objectName)]. Returns (dialog, choice)."""
    from .uikit import messages
    return messages.build(parent, title, text, [(caption, value, _ROLES.get(name, "secondary")) for caption, value, name in buttons])


def ask(parent, title, text):
    """Yes / No question (No on close). True if Yes. Yes is the primary button, on the right."""
    dialog, choice = _build(parent, title, text, [
        (i18n.t("dialogs.yes"), True, "aeroYes"), (i18n.t("dialogs.no"), False, "aeroNo")])
    _run(dialog)
    return bool(choice["value"])


def inform(parent, title, text):
    """Message with a single button."""
    dialog, _choice = _build(parent, title, text, [(i18n.t("dialogs.ok"), True, "aeroRun")])
    _run(dialog)


def confirm(parent, title, text, ok=None, cancel=None, danger=False):
    """Question with Cancel on the left and the action on the right (red when `danger`). True when the action was chosen."""
    from .uikit import messages
    return messages.confirm(parent, title, text, ok, cancel, danger)
