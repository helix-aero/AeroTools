"""About window (Qt): the logo, the version and channel, the installed tools, the links, and what a bug report needs.

A fixed-size window of the suite (no scroll bar): the HELIX AERO logo (the white one on the dark skins, the gradient one on the light skin),
"HELIX AERO Tools" with its version, channel and build date, the tools installed on this computer with their one-line description, three
link buttons (website, documentation, community), the install folder and the settings file in small muted type, and "Copy info", which puts
the version, the channel, the Metashape version, the system and the two paths on the clipboard for a bug report.
"""

import os
import platform

from . import __version__, config, i18n, msapi, uikit, updater
from .qt import QtCore, QtGui, QtWidgets

WIDTH = 480
LOGO_HEIGHT = 30                                   # design px
LOGO_DARK = "helix-aero-AERO-blanc-512px.png"      # readable on the dark and the medium skins
LOGO_LIGHT = "helix-aero-AERO-degrade-512px.png"   # the brand gradient, readable on the light skin
LINKS = (("about.website", "https://helix.aero"),
         ("about.docs", "https://docs.helix.aero"),
         ("about.community", "https://www.facebook.com/groups/1852563252425052"))


def installed_tools():
    """The tools installed on this computer (registry entries), in the order of the menu."""
    return [entry for entry in config.TOOLS if updater.tool_installed(entry["id"])]


def build_date():
    """Date of the build of the installed files (manifest), or ""."""
    try:
        return updater.describe_time(updater.installed_info(updater.plugins()[0]).get("built", ""))
    except Exception:
        return ""


def version_line():
    """"Version 0.5.0 - dev channel - built 2026-11-13 14:20" (the parts that are known)."""
    parts = [i18n.t("about.version", version=__version__), i18n.t("about.channel", channel=updater.channel())]
    built = build_date()
    if built:
        parts.append(i18n.t("about.built", date=built))
    return "  ·  ".join(parts)


def install_root():
    from . import bootstrap
    return bootstrap.root()


def info_text():
    """What the "Copy info" button puts on the clipboard: plain text for a bug report."""
    lines = ["HELIX AERO Tools {}".format(__version__),
             "Channel: {}".format(updater.channel())]
    built = build_date()
    if built:
        lines.append("Built: {}".format(built))
    lines += ["Metashape: {}".format(msapi.app_version() or "unknown"),
              "OS: {}".format(platform.platform()),
              "Python: {}".format(platform.python_version()),
              "Tools: {}".format(", ".join(entry["id"] for entry in installed_tools()) or "none"),
              "Install folder: {}".format(install_root()),
              "Settings: {}".format(config.settings_path())]
    return "\n".join(lines)


def logo_pixmap(height):
    """The logo for the active skin, `height` device-independent px high, sharp on a scaled screen (or None if the file is missing)."""
    name = LOGO_DARK if uikit.tokens.DARK else LOGO_LIGHT
    pixmap = QtGui.QPixmap(os.path.join(config.ROOT, "assets", "logo", name))
    if pixmap.isNull():
        return None
    ratio = 2.0
    pixmap = pixmap.scaledToHeight(int(uikit.px(height) * ratio), QtCore.Qt.SmoothTransformation)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def open_link(address):
    try:
        return QtGui.QDesktopServices.openUrl(QtCore.QUrl(address))
    except Exception:
        return False


class AboutDialog(uikit.ToolWindow):
    MARK = "tool_suite"
    CLOSE_ONLY = True

    def __init__(self, parent=None):
        super(AboutDialog, self).__init__(parent, title=i18n.t("about.title"), modal=True)
        body = QtWidgets.QWidget()
        body.setMinimumWidth(uikit.px(WIDTH))
        lay = uikit.vbox(spacing=uikit.tokens.M, parent=body)

        top = uikit.vbox(spacing=uikit.tokens.XS)
        self.logo = QtWidgets.QLabel()
        pixmap = logo_pixmap(LOGO_HEIGHT)
        if pixmap is not None:
            self.logo.setPixmap(pixmap)
            self.logo.setAccessibleName("HELIX AERO")
        top.addWidget(self.logo, 0, QtCore.Qt.AlignLeft)
        self.name = uikit.label(i18n.t("about.name"), "title", wrap=False)
        top.addWidget(self.name)
        self.version = uikit.label(version_line(), "small", "muted", wrap=False)
        top.addWidget(self.version)
        lay.addLayout(top)

        lay.addWidget(uikit.Divider())
        self.tool_rows = []
        for entry in installed_tools():
            row = uikit.hbox(spacing=uikit.tokens.M)
            title = uikit.label(entry["title"], "strong", wrap=False)
            title.setMinimumWidth(uikit.px(110))
            row.addWidget(title, 0, QtCore.Qt.AlignTop)
            row.addWidget(uikit.label(i18n.t(entry["subtitle"]), "body", "muted"), 1)
            lay.addLayout(row)
            self.tool_rows.append(entry["id"])

        lay.addWidget(uikit.Divider())
        links = uikit.hbox(spacing=uikit.tokens.S)
        self.link_buttons = []
        for key, address in LINKS:
            button = uikit.Button(i18n.t(key).replace("&", "&&"), "secondary", handler=lambda a=address: open_link(a))   # "&" is not a shortcut here
            button.setToolTip(address)
            links.addWidget(button)
            self.link_buttons.append(button)
        links.addStretch(1)
        lay.addLayout(links)

        lay.addWidget(uikit.Divider())
        self.paths = []
        for key, value in (("about.install_folder", install_root()), ("about.settings_file", config.settings_path())):
            row = uikit.hbox(spacing=uikit.tokens.S)
            row.addWidget(uikit.label(i18n.t(key), "caption", "muted", wrap=False), 0, QtCore.Qt.AlignVCenter)
            path = uikit.ElidedLabel(value, "caption", "muted", mode="middle")
            path.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            row.addWidget(path, 1)
            lay.addLayout(row)
            self.paths.append(value)
        copy_row = uikit.hbox(spacing=uikit.tokens.S)
        self.copy_button = uikit.Button(i18n.t("about.copy"), "quiet", handler=self.copy_info)
        copy_row.addWidget(self.copy_button)
        copy_row.addWidget(uikit.InfoButton(i18n.t("about.copy_tip")))
        copy_row.addStretch(1)
        lay.addLayout(copy_row)

        self.set_content(body)
        self.cancel_button.hide()                          # one way out: Close
        self.set_primary(i18n.t("ui.close"), self.accept)

    def copy_info(self):
        """Version, channel, Metashape, system and paths to the clipboard."""
        text = info_text()
        QtWidgets.QApplication.clipboard().setText(text)
        self.set_status(i18n.t("about.copied"), "success")
        return text

    def showEvent(self, event):
        super(AboutDialog, self).showEvent(event)
        if not getattr(self, "_fixed", False):
            self._fixed = True
            self.layout().setSizeConstraint(QtWidgets.QLayout.SetNoConstraint)     # the shell's minimum-size rule would undo the fixed size
            self.setFixedSize(self.size())                 # a fixed-size window: no grip, nothing to scroll


def _run_modal(dialog):
    return (getattr(dialog, "exec_", None) or getattr(dialog, "exec"))()


def show(parent=None):
    dialog = AboutDialog(parent)
    _run_modal(dialog)
    return dialog
