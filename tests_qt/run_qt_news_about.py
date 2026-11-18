"""News window and About window: real Qt offscreen, on the three skins.

    python -B tests_qt\run_qt_news_about.py [--shots DIR]

Checks: the window lists at most three items, newest first, bold title, date, text and a "Learn more" button only for an item with a link; no scroll bar; closing
marks the items shown as seen; the "Don't show news again" switch sets the Notifications setting (and turns it back on from the menu entry); an
empty or failed fetch says so on demand; the Notifications switch of the Install plugins window follows the setting.
About: the window builds with the logo, the version, the channel, only the installed tools with their subtitles, the two link buttons (and not the
old community group), the two paths and "Copy info", whose clipboard text holds the version, channel, Metashape version, system and paths; fixed size,
no scroll bar. Screenshots with --shots.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import _isolation  # noqa: E402
GUARD = _isolation.Guard("aerotools_qt_news_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")   # the offscreen platform finds no font by itself

import types  # noqa: E402
FAKE = types.ModuleType("Metashape")                                      # about_dialog reads the Metashape version through msapi
FAKE.app = types.SimpleNamespace(version="2.2.0", messageBox=print)
sys.modules["Metashape"] = FAKE
from aerotools import config, journal  # noqa: E402
GUARD.pin(config)
from aerotools.qt import QtCore, QtWidgets  # noqa: E402
from aerotools import news, news_dialog, uikit  # noqa: E402
from aerotools.uikit import metrics, tokens  # noqa: E402

journal.print = lambda *args, **kwargs: None
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
FAILURES = []
SHOTS = sys.argv[sys.argv.index("--shots") + 1] if "--shots" in sys.argv else None
SKINS = ("dark", "medium", "light")

ITEMS = [
    {"id": "2026-11-14-new-tool", "date": "2026-11-14", "title": "Aero-GCP 0.6 is out",
     "body": "Marker residuals now show in the report with their date. Update from Aero > Check for updates.", "url": "https://helix.aero"},
    {"id": "2026-11-02-fix", "date": "2026-11-02", "title": "Faster detection",
     "body": "The light engine starts in about half the time.", "url": ""},
    {"id": "2026-10-20-note", "date": "2026-10-20", "title": "Community group", "body": "", "url": ""},
    {"id": "2026-10-01-old", "date": "2026-10-01", "title": "An older note", "body": "Not in the window.", "url": ""},
]


def check(ok, text):
    print(("ok   " if ok else "FAIL ") + text)
    if not ok:
        FAILURES.append(text)


def pump(ms=40):
    end = QtCore.QDeadlineTimer(ms)
    while not end.hasExpired():
        APP.processEvents()


def shot(widget, name, skin):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    pump()
    path = os.path.join(SHOTS, "{}_{}.png".format(name, skin))
    widget.grab().save(path)
    print("     wrote " + path)


def reset():
    config.put(news.SECTION, "seen", [])
    config.put(news.SECTION, "enabled", True)


def texts(widget):
    return [w.text() for w in widget.findChildren(QtWidgets.QLabel)]


def buttons(widget):
    return [b.text() for b in widget.findChildren(QtWidgets.QPushButton) if b.isVisible() or True]


def no_scroll(widget):
    return not widget.findChildren(QtWidgets.QScrollArea) and not widget.findChildren(QtWidgets.QScrollBar)


def news_checks(skin):
    reset()
    dialog = news_dialog.NewsDialog(ITEMS, parent=None)
    dialog.show()
    pump()
    labels = texts(dialog)
    check(sum(1 for item in ITEMS if item["title"] in labels) == 3 and ITEMS[3]["title"] not in labels, skin + ": three newest items, the fourth left out")
    check(labels.index(ITEMS[0]["title"]) < labels.index(ITEMS[1]["title"]) < labels.index(ITEMS[2]["title"]), skin + ": newest first")
    check("2026-11-14" in labels and ITEMS[0]["body"] in labels, skin + ": date and text are there")
    caps = buttons(dialog)
    check(caps.count(i18n_t("news.learn_more")) == 1, skin + ": 'Learn more' only for the item with a link")
    check(i18n_t("news.more") in caps, skin + ": 'More news' because there are more than three")
    check(i18n_t("news.close") in caps and not dialog.cancel_button.isVisible(), skin + ": Close is the way out")
    check(dialog.toggle.text() == i18n_t("news.dont_show") and not dialog.toggle.isChecked(), skin + ": 'Don't show news again' starts unticked")
    check(not dialog.isModal(), skin + ": the window does not block Metashape")
    check(no_scroll(dialog), skin + ": no scroll bar")
    check(dialog.layout().totalMinimumSize().height() <= dialog.height() + 1, skin + ": everything fits the window")
    shot(dialog, "news", skin)
    dialog.accept()
    check(news.seen() == [i["id"] for i in ITEMS[:3]], skin + ": closing marks the items shown as seen")
    check(news.enabled(), skin + ": closing without the switch keeps the notifications on")
    check(len(news.unseen(ITEMS)) == 1 and news.unseen(ITEMS)[0]["id"] == ITEMS[3]["id"], skin + ": the fourth item is still to come")

    reset()
    dialog = news_dialog.NewsDialog(ITEMS[:1])
    dialog.show()
    pump()
    check(i18n_t("news.more") not in buttons(dialog), skin + ": no 'More news' with one item")
    dialog.toggle.setChecked(True)
    dialog.close()                                    # the title bar's cross is another way out: same result
    check(news.enabled() is False and news.seen() == [ITEMS[0]["id"]], skin + ": the switch turns the notifications off, items seen")

    dialog = news_dialog.NewsDialog(ITEMS[:1], on_demand=True)
    dialog.show()
    pump()
    check(dialog.toggle.isChecked(), skin + ": on demand while off: the switch shows it is off")
    dialog.toggle.setChecked(False)
    dialog.accept()
    check(news.enabled() is True, skin + ": unticking it turns the notifications back on")

    reset()
    shown = news_dialog.NewsDialog([], on_demand=True)
    shown.show()
    pump()
    check(i18n_t("news.empty") in texts(shown), skin + ": no items: 'No news right now.'")
    shown.accept()
    failed = news_dialog.NewsDialog(None, on_demand=True)
    failed.show()
    pump()
    check(i18n_t("news.offline") in texts(failed) and news.seen() == [], skin + ": failed fetch on demand: says so, marks nothing")
    shot(failed, "news_offline", skin)
    failed.accept()


def i18n_t(key):
    from aerotools import i18n
    return i18n.t(key)


def settings_checks(skin):
    from aerotools import plugins_dialog
    plugins_dialog.PluginsDialog._start_fetch = lambda self: None       # no manifest request from a harness
    reset()
    dialog = plugins_dialog.PluginsDialog(None)
    dialog.show()
    pump()
    check(dialog.notifications.isChecked() and dialog.notifications.text() == i18n_t("news.setting"), skin + ": Install plugins: Notifications is on")
    check(i18n_t("news.setting_caption") in texts(dialog), skin + ": Install plugins: 'Notifications' caption")
    dialog.notifications.setChecked(False)
    check(news.enabled() is False, skin + ": Install plugins: switching it off sets the setting")
    dialog.notifications.setChecked(True)
    check(news.enabled() is True, skin + ": Install plugins: switching it on re-enables the popup")
    shot(dialog, "install_plugins_notifications", skin)
    dialog.close()


def about_checks(skin):
    from aerotools import __version__, about_dialog, updater
    dialog = about_dialog.AboutDialog(None)
    dialog.show()
    pump()
    labels = texts(dialog)
    check(i18n_t("about.name") in labels and any(__version__ in text for text in labels), skin + ": About: name and version")
    check(i18n_t("about.channel").format(channel=updater.channel()) in " ".join(labels), skin + ": About: the channel is shown")
    check(dialog.logo.pixmap() is not None and not dialog.logo.pixmap().isNull(), skin + ": About: the logo is there")
    from aerotools import config as cfg
    installed = [e["id"] for e in cfg.TOOLS if updater.tool_installed(e["id"])]
    check(dialog.tool_rows == installed and len(installed) >= 1, skin + ": About: the installed tools only")
    check(all(cfg.tool(t)["title"] in labels and i18n_t(cfg.tool(t)["subtitle"]) in labels for t in installed), skin + ": About: each tool with its subtitle")
    caps = buttons(dialog)
    check([i18n_t(k).replace("&", "&&") for k in ("about.website", "about.community")] == [b.text() for b in dialog.link_buttons],
          skin + ": About: two link buttons")
    check([b.toolTip() for b in dialog.link_buttons] == ["https://helix.aero", "https://www.facebook.com/groups/1852563252425052"],
          skin + ": About: the links")
    check(i18n_t("about.copy") in caps and i18n_t("ui.close") in caps, skin + ": About: Copy info and Close")
    check(dialog.paths == [about_dialog.install_root(), cfg.settings_path()], skin + ": About: install folder and settings file")
    text = dialog.copy_info()
    clip = QtWidgets.QApplication.clipboard().text()
    check(clip == text and __version__ in clip and "Channel: " + updater.channel() in clip and "Metashape: 2.2.0" in clip and "OS: " in clip
          and cfg.settings_path() in clip and about_dialog.install_root() in clip, skin + ": About: Copy info puts the bug report text on the clipboard")
    check(i18n_t("about.copied") in texts(dialog.footer), skin + ": About: 'Copied.' in the footer")
    check(dialog.minimumSize() == dialog.maximumSize(), skin + ": About: fixed size")
    check(no_scroll(dialog), skin + ": About: no scroll bar")
    check(("240884" + "1503199237") not in " ".join(labels + [b.toolTip() for b in dialog.findChildren(QtWidgets.QPushButton)]) + clip, skin + ": About: no old group id")
    shot(dialog, "about", skin)
    dialog.accept()


def main():
    metrics.set_scale(1.0)
    for skin in SKINS:
        tokens.use(skin)
        news_checks(skin)
        settings_checks(skin)
        about_checks(skin)
    tokens.use("dark")
    print("%d checks failed" % len(FAILURES))
    problem = GUARD.check()
    if problem:
        print("FAIL " + problem)
        FAILURES.append(problem)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
