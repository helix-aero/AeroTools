"""Aero-PPK, the Result step, its Details and Compare windows and the Export step, one base. A synthetic job, no network, no Metashape.

    "C:\\Program Files\\Agisoft\\Metashape_v22\\python\\python.exe" -B tests_qt\\run_qt_aeroppk_results.py [out folder]

Result: for a good, a mixed and a bad result, in the dark, medium and light skins: the one sentence of the verdict, the bar and the four
counters (their counts add up to the photos), the base and the typical precision (the median), the advice and the four buttons; the colours
come from the kit (the bar is painted with the tokens of the skin). Continue with a result that is not reliable asks first. Try another base
goes back to the Mode step and keeps the result. Details opens next to the window with its three views, the bins, the processing settings (the
frame of the base and the heights are told there) and the bases. Store keeps a solution, Compare lists it with the current one and says how
close they are. Export: the ticks, the folder, the replacement of the positions (a patched question, the positions handed to Metashape, Undo
with its backup), what each row says after it ran, the question about a result that is not good, the case without chunk. The small question
before the PDF report and the header. Screenshots go to the out folder. Settings go to a temporary LOCALAPPDATA. Prints PASS / FAIL lines.
"""

import os
import re
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.argv = [sys.argv[0]] + ([sys.argv[1]] if len(sys.argv) > 1 else [tempfile.mkdtemp(prefix="aeroppk_results_out_")])
sys.path.insert(0, HERE)
import run_qt_aeroppk_hittest as H  # noqa: E402  (the same window and helpers)

from aerotools import theme, uikit  # noqa: E402
from aerotools.qt import QtCore, QtGui, QtWidgets  # noqa: E402
from aerotools.tools.aeroppk import exportprompt, outputs, report, state as S, summary  # noqa: E402
from aerotools.uikit import shell, tokens  # noqa: E402

check, pump, shot = H.check, H.pump, H.shot
SKINS = ("dark", "medium", "light")
TONE_OF = {"good": "success", "check": "warning", "bad": "danger"}
COLOUR_OF = {"good": "SUCCESS", "check": "WARNING", "bad": "DANGER", "none": "TEXT_DISABLED"}      # the token of each part of the bar
SOURCES = ("screen_results", "screen_details", "screen_export", "screen_compare", "screen_mode", "screen_drone", "screen_files")


class Asker(object):
    """Replaces the window's question: records what was asked and answers from a list (yes when it runs out)."""

    def __init__(self, dlg, answers=()):
        self.asked, self.answers = [], list(answers)
        dlg.confirm = self.ask

    def ask(self, text, ok=None, danger=False):
        self.asked.append((text, ok))
        return self.answers.pop(0) if self.answers else True


def show(app, dlg, results):
    return H.show_results(app, dlg, results)


def counter_numbers(res):
    """The count of each counter (reliable, to check, not reliable, no position), read from what it shows."""
    return [int(res.counters[key].value().split()[0]) for key in ("good", "check", "bad", "none")]


def bar_colours(res):
    """The colour painted in the middle of each part of the bar (None for a part with no photo)."""
    image = res.bar.grab().toImage()
    counts = res.bar.counts()
    total = float(sum(counts))
    out, left = [], 0.0
    for count in counts:
        if not count:
            out.append(None)
            continue
        middle = (left + count / 2.0) / total * image.width()
        out.append(image.pixelColor(int(middle), image.height() // 2))
        left += count
    return out


def result_page(app, dlg, kind, skin):
    """One verdict in one skin: the sentence, the bar, the counters, the facts, the advice, the buttons."""
    flights = dlg.state.flights
    res = show(app, dlg, H.scenario_results(flights, kind))
    tag = "%s %s" % (kind, skin)
    totals = summary.figures(dlg.state.results)
    counts = summary.level_counts(dlg.state.results)
    photos = totals["photos"]
    ok = counts[0]
    sentence = {"good": "Your PPK is good. %d of %d photos have a reliable position." % (ok, photos),
                "check": "Mostly good. %d of %d photos are reliable, %d need a look." % (ok, photos, photos - ok),
                "bad": "This result is not reliable. Only %d of %d photos have a reliable position." % (ok, photos)}[kind]
    check("%s: the verdict is one sentence" % tag, res.verdict.fullText() == sentence, res.verdict.fullText())
    check("%s: the sentence has the tone of the verdict" % tag, res.verdict.property("tone") == TONE_OF[kind], str(res.verdict.property("tone")))
    check("%s: the page is the done page and nothing else is in the way" % tag, res.pages.currentIndex() == 1 and photos == 472, str(res.pages.currentIndex()))
    check("%s: the four counters add up to the photos" % tag, sum(counter_numbers(res)) == photos == sum(res.bar.counts()) and counter_numbers(res) == counts,
          "%s %s %d" % (counter_numbers(res), counts, photos))
    check("%s: the counters are named Reliable, To check, Not reliable, No position" % tag,
          [res.counters[k].caption_label.text() for k in ("good", "check", "bad", "none")] == ["Reliable", "To check", "Not reliable", "No position"])
    shown = [res.counters[k].value() for k in ("good", "check", "bad", "none")]
    check("%s: each counter shows its count and its percent" % tag,
          all(re.match(r"^\d+ \(\d+ %\)$", v) for v in shown) and shown[0] == "%d (%d %%)" % (counts[0], int(round(100.0 * counts[0] / photos))), str(shown))
    tones = [res.counters[k].value_label.property("tone") for k in ("good", "check", "bad", "none")]
    check("%s: a counter is coloured only when it counts something" % tag, tones == [(t if c else "muted") for t, c in zip(("success", "warning", "danger", "muted"), counts)],
          "%s %s" % (tones, counts))
    colours = bar_colours(res)
    wanted = [QtGui.QColor(getattr(tokens, COLOUR_OF[k])) if c else None for k, c in zip(("good", "check", "bad", "none"), counts)]
    check("%s: the bar is painted with the colours of the skin" % tag, all((a is None and b is None) or (a is not None and b is not None and a.rgb() == b.rgb())
                                                                           for a, b in zip(colours, wanted)),
          str([c.name() if c is not None else None for c in colours]) + " " + str([w.name() if w is not None else None for w in wanted]))
    typical = summary.typical(dlg.state.results)
    check("%s: the base and the typical precision (the median)" % tag, res.fact_base.fullText() == "Base: CRTE, 12.6 km" and
          res.fact_precision.fullText() == "Typical precision: %.1f cm horizontal, %.1f cm vertical" % (100.0 * typical["h"], 100.0 * typical["v"]),
          res.fact_base.fullText() + " / " + res.fact_precision.fullText())
    advice = {"good": "You can go on to the export.", "check": "Open Details to see which photos, or try another base.",
              "bad": "Try another base or another mode before you export."}[kind]
    check("%s: one line of advice" % tag, res.advice.fullText() == advice, res.advice.fullText())
    check("%s: four buttons, Compare is off until there is something to compare" % tag, [b.text() for b in (res.details_button, res.other_button, res.store_button, res.compare_button)]
          == ["Details...", "Try another base", "Store this solution", "Compare..."] and res.details_button.isEnabled() and res.other_button.isEnabled()
          and res.store_button.isEnabled() and not res.compare_button.isEnabled(), "")
    check("%s: the footer says Continue and Close" % tag, dlg.next_button.text() == "Continue" and dlg.next_button.isEnabled() and dlg.cancel_button.text() == "Close",
          "%s %s" % (dlg.next_button.text(), dlg.cancel_button.text()))
    check("%s: nothing is cut" % tag, not H.cut_problems(dlg), str(H.cut_problems(dlg)))
    check("%s: the window fits its design size" % tag, not H.fit_problems(dlg), str(H.fit_problems(dlg)))
    return res


def colours_from_the_kit():
    """The screens of this rework paint nothing with a colour of their own."""
    bad = []
    for name in SOURCES:
        path = os.path.join(os.path.dirname(HERE), "aerotools", "tools", "aeroppk", name + ".py")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        for number, line in enumerate(text.splitlines(), 1):
            code = line.split("#")[0]
            if re.search(r"#[0-9A-Fa-f]{6}\b|rgb\(|rgba\(", line) or re.search(r"QColor\((?!tokens\.|_fill_color|tone_color)", code):
                bad.append("%s:%d" % (name, number))
    check("colours: no colour of its own in the screens, everything comes from the kit", not bad, ", ".join(bad))


def verdicts(app, dlg):
    for skin in SKINS:
        uikit.style.switch_skin(skin, save=False)
        pump(app, 0.3)
        for kind in ("good", "check", "bad"):
            res = result_page(app, dlg, kind, skin)
            if skin == "dark" or kind == "bad":
                shot(app, dlg, "result_%s_%s" % (kind, skin))
    uikit.style.switch_skin("dark", save=False)
    pump(app, 0.2)


def continue_and_back(app, dlg):
    """Continue with a result that is not reliable asks first; Try another base goes back and keeps the result."""
    asker = Asker(dlg, [False, True])
    flights = dlg.state.flights
    res = show(app, dlg, H.scenario_results(flights, "bad"))
    dlg.next_button.click()
    pump(app, 0.3)
    check("continue: a result that is not reliable asks first", asker.asked and asker.asked[0][0] == "This result is not reliable. Export anyway?" and
          asker.asked[0][1] == "Export anyway", str(asker.asked))
    check("continue: Cancel stays on the Result step", dlg.current_step() == S.RESULTS)
    dlg.next_button.click()
    pump(app, 0.3)
    check("continue: Export anyway goes to the Export step, and it does not ask again", dlg.current_step() == S.EXPORT and dlg.export_asked is True, str(dlg.current_step()))
    ex = dlg.screens[S.EXPORT]
    dlg.confirm = lambda text, ok=None, danger=False: (_ for _ in ()).throw(AssertionError("asked again: " + text))
    ex.replace_row.toggle.setChecked(False)
    for kind, row in ex.rows.items():
        row.toggle.setChecked(kind == "csv")
    ex.run()
    check("continue: the Export step does not ask a second time", len(ex.exported) == 1 and ex.export_state.get("csv"), str(ex.export_state))
    ex.replace_row.toggle.setChecked(True)
    dlg.go(S.RESULTS, force=True)
    # a result that is good goes through at once
    dlg.confirm = Asker(dlg).ask
    res = show(app, dlg, H.scenario_results(flights, "good"))
    dlg.next_button.click()
    pump(app, 0.3)
    check("continue: a good result goes on without a question", dlg.current_step() == S.EXPORT and not dlg.confirm.__self__.asked)
    # back: the result is kept
    dlg.go(S.RESULTS, force=True)
    before = list(dlg.state.results)
    res.other_button.click()
    pump(app, 0.3)
    check("try another base: back to the Mode step, the result is kept", dlg.current_step() == S.MODE and dlg.state.results == before and res.mode == "done")
    check("try another base: a click on a mode card is enough to start again", dlg.screens[S.MODE].cards[S.MODE_SINGLE].isVisible())
    dlg.go(S.RESULTS, force=True)
    check("try another base: the Result step still shows the verdict", res.pages.currentIndex() == 1 and res.verdict.fullText().startswith("Your PPK is good."))


def details_window(app, dlg):
    flights = dlg.state.flights
    res = show(app, dlg, H.scenario_results(flights, "check"))
    res.details_button.click()
    pump(app, 0.5)
    d = res.details
    check("details: the button opens a window of its own", d is not None and d.isVisible() and d.windowTitle() == "Details" and not d.isModal(), str(d and d.windowTitle()))
    check("details: it fits the screen and its content", not H.sweep_window(app, d, "details (check)"))
    totals = summary.figures(dlg.state.results)
    check("details: the tiles say the photos, the fixed and the float shares and the precision", d.tile_photos[1].text() == str(totals["photos"]) and d.tile_fix[1].text().endswith("%")
          and d.tile_float[1].text().endswith("%") and d.tile_sd[1].text().endswith("cm"),
          str([t[1].text() for t in (d.tile_photos, d.tile_fix, d.tile_float, d.tile_sd)]))
    bins = [int(t[1].text()) for t in d.bin_tiles]
    check("details: the precision bins add up to the photos", sum(bins) == totals["photos"] and bins == summary.precision_bins(dlg.state.results), str(bins))
    check("details: the verdict sentence is repeated", d.verdict.text() == res.verdict.fullText())
    views = {}
    for key in ("summary", "plan", "settings"):
        d.rail.set_value(key)
        d._on_view(key)
        pump(app, 0.2)
        views[key] = d.views.currentIndex()
    check("details: three views, each on its own page", sorted(views.values()) == [0, 1, 2], str(views))
    d.rail.set_value("settings")
    d._on_view("settings")
    pump(app, 0.2)
    rows = dict((key, w.fullText()) for key, w in d.settings.values.items())
    check("details: the processing settings are listed, read from the options used", all(k in rows and rows[k] != "-" for k in ("frequency", "systems", "elevation_mask", "solution_type")),
          str(rows))
    check("details: the frame of the base and the heights are told here", rows.get("frame") == "RGF93 v2b (ETRS89)" and rows.get("heights") == "Ellipsoidal", str(rows))
    check("details: the bases are listed (one row per flight) with the facts of the selected one", d.bases.row_count() == 2 and d.g_base.values["base"].fullText() == "CRTE"
          and d.g_base.values["baseline"].fullText() == "12.60 km", "%d %s" % (d.bases.row_count(), d.g_base.values["baseline"].fullText()))
    shot(app, d, "details_settings")
    d.rail.set_value("plan")
    d._on_view("plan")
    shot(app, d, "details_plan")
    check("details: the plan is coloured by level", d.plan.levels() == summary.levels(dlg.state.results) and len(d.plan._points) > 100)
    d.rail.set_value("summary")
    d._on_view("summary")
    shot(app, d, "details_summary")
    # an own base: the frame is what the user declared
    dlg.state.set_mode(S.MODE_OWN)
    d._fill_settings()
    check("details: an own base says its frame is the one entered", d.settings.values["frame"].fullText() == "As entered for the base", d.settings.values["frame"].fullText())
    dlg.state.set_mode(S.MODE_SINGLE)
    d.close()
    check("details: closed", not d.isVisible())


def store_and_compare(app, dlg):
    flights = dlg.state.flights
    dlg.stored[:] = []
    res = show(app, dlg, H.scenario_results(flights, "good"))
    check("compare: nothing stored, nothing to compare, and the button says why", not res.compare_button.isEnabled() and "Store a solution" in res.compare_button.toolTip(),
          res.compare_button.toolTip())
    res.store_button.click()
    pump(app, 0.2)
    check("compare: Store keeps the solution and the banner says so", len(dlg.stored) == 1 and "CRTE, single base" in dlg.banner.message.text(), dlg.banner.message.text())
    check("compare: the stored solution is the one on show, so there is still nothing to compare", not res.compare_button.isEnabled())
    res.store_button.click()
    check("compare: the same solution is not stored twice", len(dlg.stored) == 1 and "already" in dlg.banner.message.text().lower(), dlg.banner.message.text())
    other = show(app, dlg, H.scenario_results(flights, "good", codes=("ATST",), north=0.02))
    check("compare: another base gives another solution, and Compare is on", other.compare_button.isEnabled() and other.compare_button.toolTip() == "")
    other.compare_button.click()
    pump(app, 0.5)
    window = other.compare_window
    check("compare: the button opens the window", window is not None and window.isVisible() and window.windowTitle() == "Compare solutions")
    check("compare: it fits the screen", not H.sweep_window(app, window, "compare"))
    check("compare: two rows, the stored solution and the current one", window.table.row_count() == 2 and window.items[1].name.endswith("(current)"),
          str([s.name for s in window.items]))
    check("compare: the sentence says how close the two solutions are", window.sentence.text() == "These two solutions agree within 2.0 cm for 95 % of the photos.",
          window.sentence.text())
    check("compare: the facts say the photos and the difference", window.facts.values["photos"].fullText() == "472 / 472" and window.facts.values["over"].fullText() == "0",
          str((window.facts.values["photos"].fullText(), window.facts.values["over"].fullText())))
    shot(app, window, "compare")
    check("compare: a stored solution can be removed", window.remove_button.isEnabled())
    window.remove_button.click()
    pump(app, 0.2)
    check("compare: removing it leaves the current one alone and Compare off again", dlg.stored == [] and not other.compare_button.isEnabled() and window.table.row_count() == 1)
    window.close()
    dlg.stored[:] = []


def export_step(app, dlg):
    flights = dlg.state.flights
    ex = dlg.screens[S.EXPORT]
    written = H.fake_writers()
    del written[:]
    res = show(app, dlg, H.scenario_results(flights, "good"))
    dlg.go(S.EXPORT, force=True)
    pump(app, 0.4)
    check("export: the lead question", ex.content.itemAt(0).widget().text() == "What do you want to do with this result?")
    check("export: four files, each with the name it will get", [ex.rows[k].toggle.text().rsplit("_", 1)[0].split("_", 1)[1] for k in ("pdf", "csv", "kml", "txt")] ==
          ["PPK_report", "PPK_positions", "PPK_positions", "PPK_positions"] and all(re.search(r"\.(pdf|csv|kml|txt)$", ex.rows[k].toggle.text()) for k in ex.rows),
          str([ex.rows[k].toggle.text() for k in ex.rows]))
    check("export: the ticks are those of the saved choice", [ex.rows[k].toggle.isChecked() for k in ("pdf", "csv", "kml", "txt")] ==
          [dlg.state.outputs.pdf, dlg.state.outputs.csv, dlg.state.outputs.kml, dlg.state.outputs.txt])
    ex.rows["kml"].toggle.setChecked(False)
    ex.rows["txt"].toggle.setChecked(False)
    ex.rows["pdf"].toggle.setChecked(True)
    ex.rows["csv"].toggle.setChecked(True)
    check("export: a tick is remembered", dlg.state.outputs.pdf and dlg.state.outputs.csv and not dlg.state.outputs.kml and not dlg.state.outputs.txt and
          H.config.get("aeroppk", "outputs", {}).get("csv") is True, str(H.config.get("aeroppk", "outputs", {})))
    check("export: the folder is told, Open folder waits for the files", ex.folder_label.fullText() == ex.folder() and not ex.open_button.isVisible())
    check("export: the Metashape card names the chunk and the safety", ex.replace_row.toggle.text() == "Replace the photo positions in chunk Flight July (472)"
          and ex.replace_note.text() == "The current positions are saved first. Undo puts them back." and ex.replace_row.toggle.isChecked() and ex.replace_row.toggle.isEnabled())
    check("export: Undo waits for a backup", not ex.undo_button.isEnabled())
    check("export: the footer says Export and Back, and is ready", dlg.next_button.text() == "Export" and dlg.next_button.isEnabled() and dlg.back_button.isVisible())
    check("export: no row about the EXIF positions", not any("exif" in w.text().lower() for w in ex.findChildren(QtWidgets.QAbstractButton)))
    check("export: nothing cut, everything fits", not H.cut_problems(dlg) and not H.fit_problems(dlg), str(H.cut_problems(dlg) + H.fit_problems(dlg)))
    shot(app, dlg, "export_before")
    # Export does everything that is ticked: the files, then the positions (the patched question says yes)
    asker = Asker(dlg, [True])
    asked_report = []
    dlg.ask_report_options = lambda photos: asked_report.append(photos) or True
    dlg.next_button.click()
    pump(app, 0.6)
    check("export: the question before the PDF report was asked with the photos", asked_report == [472], str(asked_report))
    check("export: the question about the positions says the photos and the chunk", len(asker.asked) == 1 and "472 photos of chunk Flight July (472)" in asker.asked[0][0]
          and asker.asked[0][1] == "Replace positions in Metashape", str(asker.asked))
    check("export: the files are written", sorted(ex.export_state) == ["csv", "pdf"] and all(ex.export_state.values()) and all(os.path.isfile(p) for p in ex.exported),
          str(ex.export_state))
    check("export: each file row says what was done", ex.rows["pdf"].text.fullText().startswith("Written: ") and ex.rows["pdf"].text.fullText().endswith(".pdf")
          and ex.rows["csv"].result.isVisible() and not ex.rows["kml"].result.isVisible(), ex.rows["pdf"].text.fullText())
    photos = set(p for f in flights for p in f["files"].photos)
    check("export: 472 positions went to Metashape, one per photo of the chunk", len(written) == 472 and set(i["path"] for i in written) == photos, str(len(written)))
    check("export: the positions row says how many", ex.replace_row.text.fullText() == "472 positions replaced" and ex.replace_row.result.isVisible(), ex.replace_row.text.fullText())
    check("export: Open folder is there and the banner says it worked", ex.open_button.isVisible() and dlg.banner.isVisible())
    check("export: the window stays open on the Export step", dlg.isVisible() and dlg.current_step() == S.EXPORT)
    check("export: a backup was saved, Undo is on", ex.undo_button.isEnabled())
    shot(app, dlg, "export_done")
    # Undo: its own question, then the positions are put back
    restored = []
    H.screen_export.msapi.restore_camera_references = lambda chk, entries: restored.append(len(entries)) or len(entries)
    asker = Asker(dlg, [False, True])
    ex.undo_button.click()
    check("export: Undo asks first, Cancel changes nothing", len(asker.asked) == 1 and "Put back the original positions" in asker.asked[0][0] and not restored, str(asker.asked))
    ex.undo_button.click()
    pump(app, 0.3)
    check("export: Undo puts the saved positions back", restored == [472] and "restored" in dlg.banner.message.text() and not ex.replace_row.result.isVisible(),
          "%s %s" % (restored, dlg.banner.message.text()))
    # the positions are not written when the row is not ticked
    del written[:]
    ex.replace_row.toggle.setChecked(False)
    asker = Asker(dlg)
    dlg.next_button.click()
    pump(app, 0.4)
    check("export: with the row unticked only the files are written", not written and not asker.asked and ex.exported, str((len(written), asker.asked)))
    # nothing ticked
    ex.rows["pdf"].toggle.setChecked(False)
    ex.rows["csv"].toggle.setChecked(False)
    pump(app, 0.2)
    check("export: with nothing ticked Export is off and the footer says what to do", not dlg.next_button.isEnabled() and dlg.status.fullText() == "Tick something to export.",
          dlg.status.fullText())
    ex.rows["csv"].toggle.setChecked(True)
    ex.replace_row.toggle.setChecked(True)
    # a result that is not good: the first click asks
    res = show(app, dlg, H.scenario_results(flights, "check"))
    dlg.export_asked = False
    dlg.go(S.EXPORT, force=True)
    dlg.export_asked = False
    totals, counts = summary.figures(dlg.state.results), summary.level_counts(dlg.state.results)
    asker = Asker(dlg, [False, True, True])
    del written[:]
    dlg.next_button.click()
    pump(app, 0.3)
    pct = int(round(100.0 * counts[0] / totals["photos"]))
    check("export: a result that is not good asks first, with the share of reliable photos", asker.asked[0] == ("Only %d %% of the photos are reliable. Export anyway?" % pct, "Export anyway"),
          str(asker.asked))
    check("export: Cancel writes nothing", not written and len(asker.asked) == 1)
    dlg.next_button.click()
    pump(app, 0.5)
    check("export: Export anyway goes on to the files and the positions", len(written) > 0 and len(asker.asked) >= 2, str((len(written), asker.asked)))
    # no chunk: the Metashape row is off, with its reason
    document = H.meta.app.document
    dlg.chunk_timer.stop()                                               # (the window would start again on its own: it follows the document)
    H.meta.app.document = None
    try:
        ex.enter()
        pump(app, 0.3)
        check("export: no chunk: the row is off and says why", not ex.replace_row.toggle.isEnabled() and not ex.replace_row.toggle.isChecked()
              and ex.replace_row.text.fullText() == "There is no chunk to write to." and not ex.undo_button.isEnabled(), ex.replace_row.text.fullText())
        check("export: no chunk: the files can still be written", dlg.next_button.isEnabled())
        shot(app, dlg, "export_no_chunk")
    finally:
        H.meta.app.document = document
        ex.enter()
        dlg.chunk_timer.start()


def prompt_checks(app, dlg):
    """The small question before the PDF report: the box, its default and the grey line about the pages."""
    small = exportprompt.ExportPrompt(dlg, 300)
    check("prompt: 300 photos, the box is ticked and there is no warning", small.include() and small.warning_text() == "" and not small.warning.isVisible())
    small.check.setChecked(False)
    check("prompt: the box can be unticked", not small.include())
    big = exportprompt.ExportPrompt(dlg, 600)
    check("prompt: 600 photos, the box is not ticked and there is no warning", not big.include() and big.warning_text() == "")
    big.check.setChecked(True)
    pages = report.photo_table_pages(600)
    check("prompt: ticked for 600 photos, a grey line says about how many pages", big.warning_text() == "About %d pages, this can take a while" % pages and pages > 10,
          big.warning_text())
    big.show()
    pump(app, 0.3)
    check("prompt: the line and its 'i' are shown, in plain grey", big.warning.isVisible() and big.tip.isVisible() and big.warning.property("tone") == "muted"
          and "every photo" in big.tip.text_tip())
    shot(app, big, "prompt_600_ticked")
    big.check.setChecked(False)
    check("prompt: unticked again, the line goes", not big.warning.isVisible())
    edge = exportprompt.ExportPrompt(dlg, 500)
    check("prompt: exactly 500 photos still ticked", edge.include())
    edge = exportprompt.ExportPrompt(dlg, 501)
    check("prompt: 501 photos not ticked", not edge.include())
    for box in (small, big, edge):
        box.close()


def header(app, dlg):
    dlg.go(S.RESULTS, force=True)
    pump(app, 0.2)
    chunk = dlg.chunk_combo.mapTo(dlg, dlg.chunk_combo.rect().topLeft())
    title = dlg.project_label.mapTo(dlg, dlg.project_label.rect().topLeft())
    steps = dlg.steps.mapTo(dlg, dlg.steps.rect().topLeft())
    check("header: the project is under the title, the chunk drop-down is at the right end of the header, the steps are a row below",
          chunk.x() > dlg.width() / 2 and chunk.y() < steps.y() and title.x() < dlg.width() / 3 and abs(chunk.y() - title.y()) < dlg.height() / 6,
          "%s %s %s" % (chunk, title, steps))
    check("header: the drop-down stays inside the window", dlg.chunk_combo.mapTo(dlg, dlg.chunk_combo.rect().bottomRight()).x() < dlg.width())
    check("header: the name of the chunk is not cut", dlg.chunk_combo.minimumWidth() >= dlg.chunk_combo.fontMetrics().horizontalAdvance(dlg.chunk_combo.currentText()),
          dlg.chunk_combo.currentText())
    check("header: the footer line says the photos and the flights, no jargon", dlg.status.fullText() == "472 photos in 2 flights", dlg.status.fullText())
    shot(app, dlg, "header")


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.load_fonts(QtGui)
    real_area = shell.screen_area
    shell.screen_area = lambda widget=None: QtCore.QRect(0, 0, 1920, 1040)
    flights = H.realistic_flights()
    H.install_document(flights)
    H.fake_writers()
    dlg = H.make_dialog(app, flights=flights)
    try:
        colours_from_the_kit()
        verdicts(app, dlg)
        continue_and_back(app, dlg)
        details_window(app, dlg)
        store_and_compare(app, dlg)
        export_step(app, dlg)
        prompt_checks(app, dlg)
        header(app, dlg)
    finally:
        H.close_popups(app)
        for window in (dlg.screens[S.RESULTS].details, dlg.screens[S.RESULTS].compare_window):
            if window is not None:
                window.close()
        dlg.reject = lambda: None
        dlg.chunk_timer.stop()
        QtWidgets.QWidget.hide(dlg)
        dlg.deleteLater()
        pump(app, 0.1)
        shell.screen_area = real_area
    problem = H.GUARD.check()
    check("real settings untouched", problem is None, str(problem))
    print("out:", H.OUT)
    return 1 if H.FAILURES else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
