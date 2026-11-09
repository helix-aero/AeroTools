"""PDF report and CSV export of the GCPs of the current chunk (FastGCP).

Three layers, kept apart so that the first one can be tested without Qt:

  collect_data(chunk)        reads the chunk and computes the statistics (pure Python)
  collect_images(state, d)   takes the screenshots (Qt, inside Metashape)
  build_pdf(data, path, ..)  paints the PDF with QPdfWriter and QPainter

`export_pdf(state)` and `export_csv(state)` are the entry points of the panel:
they ask for the file, write it, log, and never raise.

Rendering choices:
  - A4 at 300 dpi, everything is painted as vector graphics (charts included);
  - the variable fonts Rubik and Manrope do not keep their weights in a Qt PDF
    (Qt5 only exposes the default instance, Qt6 embeds it without its variations).
    Titles and key figures are therefore painted as outlines (QPainterPath), the
    weight coming from the font if Qt provides it, otherwise from a light outline
    stroke. Running text (table, legends) stays real, selectable text, in
    Segoe UI (fallback: the sans-serif font of the machine);
  - no dependency outside Qt.
"""

import csv
import datetime
import math
import os
import re

from ... import i18n, journal, msapi, pdfkit
from ...pdfkit import (A4_H, A4_W, CARD, DOT, EM_DASH, FAINT, GREEN, GREEN_DEEP, GRID, INK, INK_2, LINE, MARGIN, MUTED, ORANGE, PAPER,
                       PT_TO_DEVICE, SITE_TEXT, SITE_URL, TABLE_HEAD_H, TABLE_TOP, WARN_BG, WARN_TEXT, safe_name)
from ...pdfkit import Canvas as _Canvas, Style as _Style, card_title as _card_title, empty_note as _empty_note
from ...pdfkit import gradient_brush as _gradient_brush, kpi_card as _kpi_card, nice_top as _nice_top, remove as _remove
from ...pdfkit import rule as _rule, tick_decimals as _tick_decimals, typo as _typo
from ...qt import QtCore, QtGui, QtWidgets
from . import review

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

BAR_LIMIT = 20                      # bars of the error chart
THRESHOLD_FACTOR = 2.0              # highlighted when the value is > 2 x RMS
MIN_FOR_THRESHOLD = 3               # no threshold with fewer than 3 measurements
CROP_W, CROP_H = 330, 220           # thumbnail around the projection, in image pixels
CROP_ZOOM = 3                       # thumbnail magnification
ROW_H = 15.0                        # height of a table row


# --------------------------------------------------------------------------
# Formats
# --------------------------------------------------------------------------

def fmt_num(value, decimals=3):
    """Number formatted for the report (decimal point).

    Em dash if the value is missing or unreadable.
    """
    return i18n.fmt_number(value, decimals) or EM_DASH


def fmt_m(value, decimals=3):
    """"0.123 m" or a dash."""
    text = fmt_num(value, decimals)
    return text if text == EM_DASH else text + " m"


def suggested_filename(chunk_label, ext=".pdf", today=None, kind=None):
    """`YYYY-MM-DD_GCP_report_<chunk>.pdf`.

    `kind`: the word of the name (by default the report one, from the text catalog).
    """
    day = today or datetime.date.today()
    if kind is None:
        kind = i18n.t("file.report")
    return "{}_{}_{}{}".format(day.strftime("%Y-%m-%d"), kind, safe_name(chunk_label), ext)


def project_path():
    """Path of the open .psx project, or an empty string."""
    try:
        return str(getattr(msapi.document(), "path", "") or "")
    except Exception:
        return ""


def project_directory():
    """Folder of the .psx project if it exists, otherwise None."""
    path = project_path()
    if path:
        folder = os.path.dirname(path)
        if folder and os.path.isdir(folder):
            return folder
    return None


def default_directory():
    """Folder offered when saving: project, else Documents, else home."""
    folder = project_directory()
    if folder:
        return folder
    try:
        docs = QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.DocumentsLocation)
        if docs and os.path.isdir(docs):
            return docs
    except Exception:
        pass
    return os.path.expanduser("~")


# --------------------------------------------------------------------------
# Data (no Qt)
# --------------------------------------------------------------------------

def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _fallback_errors(chk, marker):
    """Errors of a marker if msapi.marker_errors does not exist (yet).

    Method of the official Metashape API example: estimated minus reference,
    brought into the local frame (east, north, up) at the marker point.
    """
    try:
        reference = marker.reference.location
        position = marker.position
        if reference is None or position is None:
            return None
        crs, matrix = chk.crs, chk.transform.matrix
        if crs is None or matrix is None:
            return None
        world = matrix.mulp(position)
        estimated = crs.unproject(crs.project(world))
        delta = estimated - crs.unproject(reference)
        local = crs.localframe(world).mulv(delta)
        x, y, z = local[0], local[1], local[2]
        return {"x": x, "y": y, "z": z, "total": math.sqrt(x * x + y * y + z * z)}
    except Exception:
        return None


def _marker_errors(chk, marker):
    """{"x","y","z","total"} in metres, None values when unknown. Never raises."""
    reader = getattr(msapi, "marker_errors", None)
    try:
        raw = reader(chk, marker) if reader is not None else _fallback_errors(chk, marker)
    except Exception:
        raw = None
    raw = raw or {}
    out = {key: _finite(raw.get(key)) for key in ("x", "y", "z", "total")}
    if out["total"] is None and None not in (out["x"], out["y"], out["z"]):
        out["total"] = math.sqrt(out["x"] ** 2 + out["y"] ** 2 + out["z"] ** 2)
    return out


def _reference_xyz(marker):
    """Reference coordinates (x, y, z) of the marker, or None."""
    try:
        location = marker.reference.location
        if location is None:
            return None
        try:
            values = (location.x, location.y, location.z)
        except AttributeError:
            values = (location[0], location[1], location[2])
        values = tuple(_finite(v) for v in values)
        return None if None in values else values
    except Exception:
        return None


def _rms(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return math.sqrt(sum(v * v for v in values) / len(values))


def _group_rms(markers):
    """RMS of a group of markers: {"n": markers with an error, "x", "y", "z", "xy", "total"} (None where nothing to average)."""
    rms = {axis: _rms([m[axis] for m in markers]) for axis in ("x", "y", "z", "total")}
    xy = math.sqrt(rms["x"] ** 2 + rms["y"] ** 2) if rms["x"] is not None and rms["y"] is not None else None
    return {"n": sum(1 for m in markers if m["total"] is not None), "x": rms["x"], "y": rms["y"], "z": rms["z"], "xy": xy,
            "total": rms["total"]}


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _meta():
    path = project_path()
    return {
        "project": os.path.basename(path) if path else i18n.t("report.no_project"),
        "project_path": path,
        "metashape": msapi.app_version(),
    }


def collect_data(chunk, today=None):
    """Reads the markers of the chunk. Pure Python, no Qt call.

    Returns {"chunk": name, "date": date, "meta": {...}, "markers": [...],
    "stats": {...}}. A marker: label, x, y, z, total (m, or None),
    projections (int), pointed (bool), ref ((x, y, z) or None).
    """
    markers = []
    for marker in msapi.markers(chunk):
        errors = _marker_errors(chunk, marker)
        try:
            count = int(msapi.projection_count(marker))
        except Exception:
            count = 0
        markers.append({
            "label": msapi.marker_label(marker),
            "x": errors["x"], "y": errors["y"], "z": errors["z"],
            "total": errors["total"],
            "projections": count,
            "pointed": count > 0,
            "ref": _reference_xyz(marker),
            "enabled": msapi.marker_enabled(marker),     # True: control point (used in the adjustment), False: check point, None: unknown
        })

    totals = [m["total"] for m in markers if m["total"] is not None]
    rms = {axis: _rms([m[axis] for m in markers]) for axis in ("x", "y", "z", "total")}
    rms_xy = None
    if rms["x"] is not None and rms["y"] is not None:
        rms_xy = math.sqrt(rms["x"] ** 2 + rms["y"] ** 2)

    worst = None
    for m in markers:
        if m["total"] is not None and (worst is None or m["total"] > worst["total"]):
            worst = m
    counts = [m["projections"] for m in markers]

    def threshold(value):
        if value is None or len(totals) < MIN_FOR_THRESHOLD or value <= 0:
            return None
        return THRESHOLD_FACTOR * value

    control = _group_rms([m for m in markers if m["enabled"] is True])
    check = _group_rms([m for m in markers if m["enabled"] is False])
    stats = {
        "n_control": sum(1 for m in markers if m["enabled"] is True),
        "n_check": sum(1 for m in markers if m["enabled"] is False),
        "control_n": control["n"], "control_rms_x": control["x"], "control_rms_y": control["y"], "control_rms_z": control["z"],
        "control_rms_xy": control["xy"], "control_rms_total": control["total"],
        "check_n": check["n"], "check_rms_x": check["x"], "check_rms_y": check["y"], "check_rms_z": check["z"],
        "check_rms_xy": check["xy"], "check_rms_total": check["total"],
        "n_markers": len(markers),
        "n_pointed": sum(1 for m in markers if m["pointed"]),
        "n_with_error": len(totals),
        "rms_x": rms["x"], "rms_y": rms["y"], "rms_z": rms["z"],
        "rms_xy": rms_xy, "rms_total": rms["total"],
        "worst_label": worst["label"] if worst else None,
        "worst_total": worst["total"] if worst else None,
        "total_projections": sum(counts),
        "mean_projections": (sum(counts) / float(len(counts))) if counts else None,
        "median_projections": _median(counts),
        "min_projections": min(counts) if counts else None,
        "max_projections": max(counts) if counts else None,
        "n_low_projections": sum(1 for c in counts if 0 < c < 3),
        "threshold_x": threshold(rms["x"]), "threshold_y": threshold(rms["y"]),
        "threshold_z": threshold(rms["z"]), "threshold_total": threshold(rms["total"]),
    }
    return {
        "chunk": msapi.chunk_label(chunk),
        "date": today or datetime.date.today(),
        "meta": _meta(),
        "markers": markers,
        "stats": stats,
    }


def over_threshold(value, threshold):
    """True if |value| exceeds the threshold (a None threshold: never)."""
    return value is not None and threshold is not None and abs(value) > threshold


def projection_bins(counts, max_bins=12):
    """Groups the projection counts for the histogram.

    Returns [(label, count, contains_zero)]. One bin per value as long as the
    maximum is small, otherwise bins of equal width.
    """
    if not counts:
        return []
    top = max(counts)
    width = max(1, int(math.ceil((top + 1) / float(max_bins))))
    bins = []
    start = 0
    while start <= top:
        end = start + width - 1
        n = sum(1 for c in counts if start <= c <= end)
        label = str(start) if width == 1 else "{}-{}".format(start, min(end, top))
        bins.append((label, n, start == 0))
        start += width
    return bins


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

def csv_header(with_reference):
    head = [i18n.t(key) for key in ("csv.id", "csv.x", "csv.y", "csv.z",
                                    "csv.total", "csv.projections")]
    if with_reference:
        head += [i18n.t(key) for key in ("csv.ref_x", "csv.ref_y", "csv.ref_z")]
    return head


def _csv_num(value):
    """CSV number: empty when unknown (Excel reads an empty cell better than a dash)."""
    return "" if value is None else fmt_num(value)


def csv_rows(data):
    """Rows of the CSV (lists of strings), header included."""
    markers = data["markers"]
    with_ref = any(m.get("ref") for m in markers)
    rows = [csv_header(with_ref)]
    for m in markers:
        row = [m["label"], _csv_num(m["x"]), _csv_num(m["y"]), _csv_num(m["z"]),
               _csv_num(m["total"]), str(m["projections"])]
        if with_ref:
            ref = m.get("ref")
            if ref:
                geographic = max(abs(ref[0]), abs(ref[1])) <= 360.0
                row += [fmt_num(ref[0], 8 if geographic else 3),
                        fmt_num(ref[1], 8 if geographic else 3), fmt_num(ref[2], 3)]
            else:
                row += ["", "", ""]
        rows.append(row)
    return rows


def write_csv(data, path):
    """Writes the CSV, UTF-8 with BOM (Excel), comma-delimited with a decimal point."""
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=i18n.csv_delimiter(), lineterminator="\r\n")
        for row in csv_rows(data):
            writer.writerow(row)
    return path


# --------------------------------------------------------------------------
# Page elements
# --------------------------------------------------------------------------

def _fmt_date(day):
    return i18n.fmt_date(day)


def _footer(cv, W, H, page, total):
    y = H - 34.0
    cv.line(MARGIN, y, W - MARGIN, y, LINE, 0.6)
    credit = "HELIX AERO {} AeroTools Aero-GCP {} ".format(DOT, DOT)
    cv.text(MARGIN, y + 15.0, credit, 7.5, MUTED)
    start = MARGIN + cv.width(credit, 7.5)
    cv.text(start, y + 15.0, SITE_TEXT, 7.5, GREEN_DEEP, "body", 2)
    cv.link(start, y + 15.0, SITE_TEXT, 7.5, SITE_URL, "body", 2)
    cv.text(W - MARGIN, y + 15.0, i18n.t("report.footer.page", page=page, total=total),
            7.5, MUTED, align="r")


def _page_background(cv, W, H):
    cv.rect(0, 0, W, H, fill=PAPER)


def _page_title(cv, W, data, title, subtitle=None):
    _rule(cv, W)
    cv.text(MARGIN, 52.0, title, 17, INK, "display", 2)
    right = "{}  {}  {}".format(data["chunk"], DOT, _fmt_date(data["date"]))
    cv.text(W - MARGIN, 52.0, cv.fit(right, 8.5, 260), 8.5, MUTED, align="r")
    if subtitle:
        cv.text(MARGIN, 66.0, subtitle, 8.5, MUTED)


def _draw_band(cv, W, data):
    """Title band of page 1."""
    height = 132.0
    cv.p.setPen(QtCore.Qt.NoPen)
    cv.p.setBrush(_gradient_brush(0, height, W, 0))
    cv.p.drawRect(QtCore.QRectF(0, 0, W, height))
    cv.text(MARGIN, 36.0, "HELIX AERO", 9, "#ffffff", "display", 2, spacing=1.8)
    cv.link(MARGIN, 36.0, "HELIX AERO", 9, SITE_URL, "display", 2)
    cv.text(W - MARGIN, 36.0, "AeroTools {} Aero-GCP".format(DOT), 9, "#ffffff", "display", 1,
            align="r", alpha=0.85)
    cv.text(MARGIN, 76.0, i18n.t("report.title"), 29, "#ffffff", "display", 2)
    meta = data["meta"]
    version = meta.get("metashape") or EM_DASH
    cells = [
        (i18n.t("report.band.project").upper(), meta.get("project") or EM_DASH),
        (i18n.t("report.band.chunk").upper(), data["chunk"] or EM_DASH),
        (i18n.t("report.band.date").upper(), _fmt_date(data["date"])),
        (i18n.t("report.band.metashape").upper(), version),
    ]
    inner = W - 2 * MARGIN
    weights = (0.34, 0.34, 0.16, 0.16)
    x = MARGIN
    for (label, value), share in zip(cells, weights):
        cell_w = inner * share
        cv.text(x, 104.0, label, 6.8, "#ffffff", "body", 2, alpha=0.72, spacing=1.2)
        cv.text(x, 119.0, cv.fit(value, 10.5, cell_w - 10), 10.5, "#ffffff", "body", 2)
        x += cell_w


def _control_sub(stats, key):
    """Second line of a check-point RMS card: the control points' value, said as such."""
    if not stats["control_n"] or stats["control_rms_" + key] is None:
        return i18n.t("report.kpi.no_control")
    return i18n.t("report.kpi.control_value", value=fmt_m(stats["control_rms_" + key]))


def _rms_cards(s):
    """The two RMS cards of the summary. With control / check flags: the RMS of the CHECK points (not used in the adjustment, so
    an independent measure) with the control points' RMS under it, or a plain statement that there are no check points. Without the
    flags (unreadable): the RMS of all the markers, labelled so."""
    if not (s["n_control"] or s["n_check"]):
        xy = None
        if s["rms_x"] is not None and s["rms_y"] is not None:
            xy = "X {}  {}  Y {}".format(fmt_m(s["rms_x"]), DOT, fmt_m(s["rms_y"]))
        z_sub = (i18n.t("report.kpi.rms_z_sub", value=fmt_m(s["rms_total"])) if s["rms_total"] is not None else None)
        return [(i18n.t("report.kpi.rms_xy_all"), fmt_m(s["rms_xy"]), xy, GREEN),
                (i18n.t("report.kpi.rms_z_all"), fmt_m(s["rms_z"]), z_sub, GREEN)]
    if not s["check_n"]:
        none = i18n.t("report.kpi.no_check")
        return [(i18n.t("report.kpi.check_xy"), fmt_m(None), none, GREEN),
                (i18n.t("report.kpi.check_z"), fmt_m(None), none, GREEN)]
    return [(i18n.t("report.kpi.check_xy"), fmt_m(s["check_rms_xy"]), _control_sub(s, "xy"), GREEN),
            (i18n.t("report.kpi.check_z"), fmt_m(s["check_rms_z"]), _control_sub(s, "z"), GREEN)]


def _draw_kpis(cv, x, y, w, data):
    s = data["stats"]
    gap, h = 10.0, 68.0
    cw = (w - 2 * gap) / 3.0
    pct = ""
    if s["n_markers"]:
        pct = i18n.t("report.kpi.pointed_sub",
                     pct=int(round(100.0 * s["n_pointed"] / s["n_markers"])))
    mean = s["mean_projections"]
    worst = s["worst_label"]
    worst_hot = (s["worst_total"] is not None and s["threshold_total"] is not None
                 and s["worst_total"] > s["threshold_total"])
    markers_sub = (i18n.t("report.kpi.markers_split", control=s["n_control"], check=s["n_check"])
                   if (s["n_control"] or s["n_check"]) else i18n.t("report.kpi.markers_sub"))
    rms_xy, rms_z = _rms_cards(s)
    cards = [
        (i18n.t("report.kpi.markers"), str(s["n_markers"]), markers_sub, GREEN),
        (i18n.t("report.kpi.pointed"), "{} / {}".format(s["n_pointed"], s["n_markers"]),
         pct or None, GREEN),
        (i18n.t("report.kpi.projections"), str(s["total_projections"]),
         i18n.t("report.kpi.projections_sub", mean=fmt_num(mean, 1))
         if mean is not None else None, GREEN),
        rms_xy,
        rms_z,
        (i18n.t("report.kpi.worst"), fmt_m(s["worst_total"]), worst,
         ORANGE if worst_hot else GREEN),
    ]
    for index, (label, value, sub, accent) in enumerate(cards):
        col, row = index % 3, index // 3
        _kpi_card(cv, x + col * (cw + gap), y + row * (h + gap), cw, h, label, value, sub, accent)
    return 2 * h + gap


def _bar_chart(cv, x, y, w, h, data):
    """Horizontal bars: total error per marker, sorted in descending order."""
    s = data["stats"]
    cv.card(x, y, w, h)
    valid = [m for m in data["markers"] if m["total"] is not None]
    valid.sort(key=lambda m: -m["total"])
    shown = valid[:BAR_LIMIT]
    sub = i18n.t("report.bar.sub")
    if len(valid) > len(shown):
        sub = i18n.t("report.bar.sub_top", shown=len(shown), total=len(valid))
    _card_title(cv, x, y, i18n.t("report.bar.title"), sub)
    if not shown:
        _empty_note(cv, x, y, w, h)
        return
    label_w, value_w = 76.0, 40.0
    left = x + 14 + label_w
    right = x + w - 14 - value_w
    top_y = y + 54.0
    bottom_reserve = 44.0
    row_h = min(17.0, (h - 54.0 - bottom_reserve) / float(len(shown)))
    bar_h = row_h * 0.56
    peak = max(shown[0]["total"], s["threshold_total"] or 0.0, s["rms_total"] or 0.0)
    top, step = _nice_top(peak, 4)
    decimals = _tick_decimals(step)
    scale = (right - left) / top
    plot_bottom = top_y + row_h * len(shown)
    n_ticks = int(round(top / step))
    for i in range(n_ticks + 1):
        gx = left + i * step * scale
        cv.line(gx, top_y - 3, gx, plot_bottom + 1, GRID if i else LINE, 0.5)
        cv.text(gx, plot_bottom + 11, fmt_num(i * step, decimals), 6.6, FAINT, align="c")
    thr = s["threshold_total"]
    rms = s["rms_total"]
    if rms:
        rx = left + rms * scale
        cv.line(rx, top_y - 3, rx, plot_bottom + 1, INK_2, 0.8, dash=(3, 2.5))
    for i, m in enumerate(shown):
        cy = top_y + i * row_h + row_h / 2.0
        hot = over_threshold(m["total"], thr)
        cv.text(x + 14, cy + 2.6, cv.fit(m["label"], 7.8, label_w - 6), 7.8, INK_2)
        bw = max(1.2, m["total"] * scale)
        cv.rect(left, cy - bar_h / 2.0, bw, bar_h, fill=ORANGE if hot else GREEN,
                radius=min(2.0, bar_h / 2.0))
        value = fmt_num(m["total"])
        vw = cv.width(value, 7.2)
        cv.rect(left + bw + 2.0, cy - 5.2, vw + 4.0, 10.4, fill=CARD)
        cv.text(left + bw + 4, cy + 2.6, value, 7.2, WARN_TEXT if hot else MUTED)
    ly = y + h - 14
    lx = x + 14
    cv.rect(lx, ly - 6, 8, 8, fill=GREEN, radius=2)
    lx += 12
    lx += cv.text(lx, ly + 1, i18n.t("report.bar.legend_total"), 7.2, MUTED) + 12
    if thr:
        cv.rect(lx, ly - 6, 8, 8, fill=ORANGE, radius=2)
        lx += 12
        lx += cv.text(lx, ly + 1, i18n.t("report.bar.legend_over"), 7.2, MUTED) + 12
    if rms:
        cv.line(lx, ly - 2, lx + 10, ly - 2, INK_2, 0.8, dash=(3, 2.5))
        lx += 14
        cv.text(lx, ly + 1, i18n.t("report.bar.legend_rms", value=fmt_m(rms)), 7.2, MUTED)


def _scatter_chart(cv, x, y, w, h, data):
    """Plan scatter: X error on the abscissa, Y error on the ordinate."""
    s = data["stats"]
    cv.card(x, y, w, h)
    _card_title(cv, x, y, i18n.t("report.scatter.title"), i18n.t("report.scatter.sub"))
    pts = [m for m in data["markers"] if m["x"] is not None and m["y"] is not None]
    if not pts:
        _empty_note(cv, x, y, w, h)
        return
    left_pad = 26.0
    side = w - 14 - left_pad - 14
    px0, py0 = x + left_pad + 14, y + 48.0
    extent = max([abs(m["x"]) for m in pts] + [abs(m["y"]) for m in pts]
                 + [s["rms_xy"] or 0.0, 0.005])
    top, step = _nice_top(extent * 1.08, 2)
    decimals = _tick_decimals(step)
    k = side / (2.0 * top)
    ox, oy = px0 + side / 2.0, py0 + side / 2.0
    cv.rect(px0, py0, side, side, fill="#fbfbf9", line=LINE, radius=2)
    n = int(round(top / step))
    for i in range(-n, n + 1):
        gx, gy = ox + i * step * k, oy - i * step * k
        if i:
            cv.line(gx, py0, gx, py0 + side, GRID, 0.5)
            cv.line(px0, gy, px0 + side, gy, GRID, 0.5)
        if n <= 2 or i % 2 == 0:
            cv.text(gx, py0 + side + 10, _typo(fmt_num(i * step, decimals)), 6.4, FAINT, align="c")
            cv.text(px0 - 4, gy + 2.2, _typo(fmt_num(i * step, decimals)), 6.4, FAINT, align="r")
    cv.line(ox, py0, ox, py0 + side, FAINT, 0.7)
    cv.line(px0, oy, px0 + side, oy, FAINT, 0.7)
    rms = s["rms_xy"]
    if rms:
        cv.circle(ox, oy, rms * k, fill=GREEN, alpha=0.10)
        cv.circle(ox, oy, rms * k, line=GREEN_DEEP, line_w=0.9, dash=(3, 2.2))
    thr = s["threshold_total"]
    for m in pts:
        hot = over_threshold(m["total"], thr)
        cv.circle(ox + m["x"] * k, oy - m["y"] * k, 3.0, fill=ORANGE if hot else GREEN,
                  line="#ffffff", line_w=0.8)
    far = sorted(pts, key=lambda m: -(m["x"] ** 2 + m["y"] ** 2))[:3]
    for m in far:
        cx, cy = ox + m["x"] * k, oy - m["y"] * k
        label = cv.fit(m["label"], 6.8, 60)
        if cx > ox + side * 0.2:
            cv.text(cx - 5, cy - 4.5, label, 6.8, INK_2, align="r")
        else:
            cv.text(cx + 5, cy - 4.5, label, 6.8, INK_2)
    ly = y + h - 12
    if rms:
        cv.circle(x + 20, ly - 2.2, 3.4, line=GREEN_DEEP, line_w=0.9, dash=(2, 1.6))
        cv.text(x + 28, ly, i18n.t("report.scatter.legend_rms", value=fmt_m(rms)), 7.2, MUTED)
    cv.text(x + w - 14, ly, i18n.t("report.scatter.unit"), 7.2, FAINT, align="r")


def _axis_rms_chart(cv, x, y, w, h, data):
    s = data["stats"]
    cv.card(x, y, w, h)
    _card_title(cv, x, y, i18n.t("report.axis.title"))
    rows = [("X", s["rms_x"]), ("Y", s["rms_y"]), ("Z", s["rms_z"]),
            (i18n.t("report.axis.xy"), s["rms_xy"]),
            (i18n.t("report.axis.total"), s["rms_total"])]
    if all(v is None for _l, v in rows):
        _empty_note(cv, x, y + 10, w, h - 10)
        return
    peak = max(v for _l, v in rows if v is not None) or 1.0
    top, _step = _nice_top(peak, 2)
    left = x + 14 + 30
    right = x + w - 14 - 46
    row_h = 18.0
    for i, (label, value) in enumerate(rows):
        cy = y + 50 + i * row_h
        cv.text(x + 14, cy + 2.6, label, 7.8, INK_2)
        if value is None:
            cv.text(right + 4, cy + 2.6, EM_DASH, 7.4, FAINT)
            continue
        bw = max(1.2, value / top * (right - left))
        bar = i >= 3
        cv.rect(left, cy - 3.6, bw, 7.2, fill=GREEN_DEEP if bar else GREEN, radius=2)
        cv.text(left + bw + 4, cy + 2.6, fmt_num(value), 7.4, MUTED)


def _rms_note(s):
    """First line of the note: what the RMS values are made of (check points, control points, or all markers)."""
    if not (s["n_control"] or s["n_check"]):
        return i18n.t("report.note.errors_all")
    if s["check_n"]:
        return i18n.t("report.note.errors_check")
    if s["control_n"]:
        return i18n.t("report.note.errors_no_check", xy=fmt_m(s["control_rms_xy"]), z=fmt_m(s["control_rms_z"]))
    return i18n.t("report.note.errors_all")


def _note_card(cv, x, y, w, h, data):
    s = data["stats"]
    cv.card(x, y, w, h)
    if s["n_with_error"] == 0:
        lines = [i18n.t("report.note.none_1"), i18n.t("report.note.none_2")]
    else:
        if s["threshold_total"]:
            second = i18n.t("report.note.threshold", value=fmt_m(s["threshold_total"]))
        else:
            second = i18n.t("report.note.no_threshold")
        lines = [_rms_note(s), second]
    cv.text(x + 14, y + 20, i18n.t("report.note.title").upper(), 6.8, MUTED, "body", 2,
            spacing=1.0)
    for i, line in enumerate(lines):
        cv.text(x + 14, y + 34 + i * 11.5, line, 8, INK_2)


def _page_summary(cv, W, H, data, page, total):
    _page_background(cv, W, H)
    _draw_band(cv, W, data)
    inner = W - 2 * MARGIN
    y = 150.0
    y += _draw_kpis(cv, MARGIN, y, inner, data) + 14.0
    right_w = 208.0
    gap = 10.0
    left_w = inner - right_w - gap
    scatter_h = right_w + 52.0
    axis_h = 148.0
    block_h = scatter_h + gap + axis_h
    _bar_chart(cv, MARGIN, y, left_w, block_h, data)
    _scatter_chart(cv, MARGIN + left_w + gap, y, right_w, scatter_h, data)
    _axis_rms_chart(cv, MARGIN + left_w + gap, y + scatter_h + gap, right_w, axis_h, data)
    y += block_h + gap
    _note_card(cv, MARGIN, y, inner, 52.0, data)
    _footer(cv, W, H, page, total)


# --------------------------------------------------------------------------
# Table
# --------------------------------------------------------------------------

HIST_H = 176.0


def _table_plan(n_rows, H):
    """Distribution of the rows over the pages: (rows_per_page, histogram_with_last).

    `rows_per_page` is a list of row counts. The histogram goes under the last
    table if there is room, otherwise it gets its own page.
    """
    bottom = H - 46.0
    per_page = int((bottom - TABLE_TOP - TABLE_HEAD_H - 8.0) // ROW_H)
    # Under the table: 22 pt of legend, then the histogram, before the footer rule.
    with_hist = int((H - 48.0 - TABLE_TOP - TABLE_HEAD_H - 8.0 - 22.0 - HIST_H) // ROW_H)
    n_rows = max(n_rows, 0)
    chunks = []
    left = n_rows
    while left > per_page:
        chunks.append(per_page)
        left -= per_page
    if left > with_hist:
        chunks.append(left)
        return chunks, False
    chunks.append(left)
    return chunks, True


def _draw_table(cv, x, y, w, rows, stats, first_index):
    n = len(rows)
    height = TABLE_HEAD_H + n * ROW_H + 8.0
    cv.card(x, y, w, height)
    pad = 14.0
    right = x + w - pad
    col_w = [66.0, 66.0, 66.0, 70.0, 44.0]          # X, Y, Z, Total, Proj.
    cols = []
    edge = right
    for width in reversed(col_w):
        cols.append(edge)
        edge -= width
    cols.reverse()                                    # right edges of the 5 columns
    heads = [i18n.t(key) for key in ("report.table.x", "report.table.y", "report.table.z",
                                     "report.table.total", "report.table.proj")]
    cv.rect(x + 0.3, y + 0.3, w - 0.6, TABLE_HEAD_H, fill="#ecece8", radius=6.5)
    cv.rect(x + 0.3, y + TABLE_HEAD_H - 8, w - 0.6, 8.6, fill="#ecece8")
    cv.text(x + pad, y + 15.5, i18n.t("report.table.id"), 6.9, MUTED, "body", 2, spacing=0.8)
    for edge, head in zip(cols, heads):
        cv.text(edge, y + 15.5, head, 7.0, MUTED, "body", 2, align="r")
    thresholds = (stats["threshold_x"], stats["threshold_y"], stats["threshold_z"],
                  stats["threshold_total"])
    id_right = (cols[0] - col_w[0]) - 8.0
    id_w = id_right - (x + pad) - 40.0                  # room for the "check" / "control" tag at the end of the cell
    for i, m in enumerate(rows):
        ry = y + TABLE_HEAD_H + i * ROW_H
        if (first_index + i) % 2 == 1:
            cv.rect(x + 0.3, ry, w - 0.6, ROW_H, fill=PAPER)
        base = ry + ROW_H - 4.6
        muted = not m["pointed"]
        cv.text(x + pad, base, cv.fit(m["label"], 8.2, id_w), 8.2, FAINT if muted else INK, "body", 1)
        tag = {True: "report.table.control", False: "report.table.check"}.get(m.get("enabled"))
        if tag:
            cv.text(id_right, base, i18n.t(tag), 6.8, MUTED, align="r")
        values = (m["x"], m["y"], m["z"], m["total"])
        for edge, value, thr in zip(cols[:4], values, thresholds):
            text = _typo(fmt_num(value))
            if over_threshold(value, thr):
                tw = cv.width(text, 8.2)
                cv.rect(edge - tw - 4, ry + 1.8, tw + 8, ROW_H - 3.6, fill=WARN_BG, radius=3)
                cv.text(edge, base, text, 8.2, WARN_TEXT, "body", 2, align="r")
            else:
                cv.text(edge, base, text, 8.2, FAINT if value is None else INK_2, align="r")
        cv.text(cols[4], base, str(m["projections"]), 8.2,
                WARN_TEXT if muted else INK_2, "body", 2 if muted else 1, align="r")
    return height


def _histogram_card(cv, x, y, w, h, data):
    s = data["stats"]
    counts = [m["projections"] for m in data["markers"]]
    cv.card(x, y, w, h)
    _card_title(cv, x, y, i18n.t("report.hist.title"), i18n.t("report.hist.sub"))
    bins = projection_bins(counts)
    if not bins:
        _empty_note(cv, x, y, w, h)
        return
    chart_w = w - 14 - 14 - 160.0
    left, base = x + 14 + 8, y + h - 30.0
    top_y = y + 56.0
    peak = max(n for _l, n, _z in bins) or 1
    top, step = _nice_top(float(peak), 3)
    top = max(top, 1.0)
    step = max(1.0, round(step))
    unit = (base - top_y) / top
    n_ticks = int(top // step)
    for i in range(n_ticks + 1):
        gy = base - i * step * unit
        cv.line(left - 4, gy, left + chart_w, gy, GRID if i else LINE, 0.5)
        cv.text(left - 7, gy + 2.2, str(int(i * step)), 6.4, FAINT, align="r")
    slot = chart_w / float(len(bins))
    bar_w = min(26.0, slot * 0.62)
    for i, (label, n, zero) in enumerate(bins):
        cx = left + slot * (i + 0.5)
        bh = n * unit
        if n:
            cv.rect(cx - bar_w / 2.0, base - bh, bar_w, bh, fill=ORANGE if zero else GREEN,
                    radius=min(2.0, bar_w / 3.0))
            cv.text(cx, base - bh - 3.5, str(n), 7.2, MUTED, align="c")
        cv.text(cx, base + 11, label, 7.2, MUTED, align="c")
    cv.text(left + chart_w / 2.0, base + 23, i18n.t("report.hist.axis"), 7.0, FAINT, align="c")
    sx = x + w - 14 - 140.0
    cv.line(sx - 12, y + 56, sx - 12, y + h - 22, LINE, 0.6)
    items = [
        (i18n.t("report.hist.mean"), fmt_num(s["mean_projections"], 1)),
        (i18n.t("report.hist.median"), fmt_num(s["median_projections"], 1)),
        (i18n.t("report.hist.min"), str(s["min_projections"])),
        (i18n.t("report.hist.max"), str(s["max_projections"])),
        (i18n.t("report.hist.low"), str(s["n_low_projections"])),
        (i18n.t("report.hist.none"), str(s["n_markers"] - s["n_pointed"])),
    ]
    for i, (label, value) in enumerate(items):
        ry = y + 66 + i * 17.0
        cv.text(sx, ry, label, 7.8, MUTED)
        cv.text(sx + 140, ry, value, 8.2, INK, "body", 2, align="r")
        if i < len(items) - 1:
            cv.line(sx, ry + 6.5, sx + 140, ry + 6.5, GRID, 0.5)


def _draw_table_pages(draw_page, cv, W, H, data, first_page_no, total):
    """Paints the table pages. `draw_page()` opens a new page."""
    plan, hist_inline = _table_plan(len(data["markers"]), H)
    inner = W - 2 * MARGIN
    markers = data["markers"]
    index = 0
    page_no = first_page_no
    last_index = len(plan) - 1
    for number, n in enumerate(plan):
        if number or page_no > first_page_no:
            draw_page()
        _page_background(cv, W, H)
        if number == 0:
            subtitle = i18n.t("report.table.sub", n=len(markers))
        elif len(plan) > 1:
            subtitle = i18n.t("report.table.sub_page", page=number + 1, pages=len(plan))
        else:
            subtitle = i18n.t("report.table.sub_next")
        _page_title(cv, W, data, i18n.t("report.table.title"), subtitle)
        rows = markers[index:index + n]
        height = _draw_table(cv, MARGIN, TABLE_TOP, inner, rows, data["stats"], index)
        index += n
        if number == last_index:
            if rows:
                cv.text(MARGIN, TABLE_TOP + height + 11, _table_legend(data["stats"]), 7.2, MUTED)
            if hist_inline:
                top = max(TABLE_TOP + height + 22.0, TABLE_TOP + TABLE_HEAD_H + 8.0)
                _histogram_card(cv, MARGIN, top, inner, HIST_H, data)
        _footer(cv, W, H, page_no, total)
        page_no += 1
    if not hist_inline:
        draw_page()
        _page_background(cv, W, H)
        _page_title(cv, W, data, i18n.t("report.hist.page_title"))
        _histogram_card(cv, MARGIN, TABLE_TOP, inner, HIST_H, data)
        _footer(cv, W, H, page_no, total)
        page_no += 1
    return page_no


def _table_legend(stats):
    if stats["threshold_total"]:
        return i18n.t("report.table.legend")
    return i18n.t("report.table.legend_plain")


def _table_pages_count(n_rows, H):
    plan, hist_inline = _table_plan(n_rows, H)
    return len(plan) + (0 if hist_inline else 1)


# --------------------------------------------------------------------------
# Screenshots
# --------------------------------------------------------------------------

def _fit_box(iw, ih, bw, bh):
    if iw <= 0 or ih <= 0 or bw <= 0 or bh <= 0:
        return 0.0, 0.0
    scale = min(bw / float(iw), bh / float(ih))
    return iw * scale, ih * scale


CARD_PAD = 8.0
CAPTION_LINE = 10.5


def _caption_height(lines):
    return len(lines) * CAPTION_LINE + 8.0 if lines else 0.0


def _capture_height(image, width, lines, max_h):
    """Natural height of a screenshot card of width `width`, capped at `max_h`."""
    caption_h = _caption_height(lines)
    _iw, ih = _fit_box(image.width(), image.height(), width - 2 * CARD_PAD,
                       max_h - 2 * CARD_PAD - caption_h)
    return ih + 2 * CARD_PAD + caption_h


def _capture_card(cv, x, y, w, h, image, lines):
    """Card with the image centred at the top and its caption below. Returns h."""
    cv.card(x, y, w, h)
    caption_h = _caption_height(lines)
    iw, ih = _fit_box(image.width(), image.height(), w - 2 * CARD_PAD,
                      h - 2 * CARD_PAD - caption_h)
    ix = x + (w - iw) / 2.0
    iy = y + CARD_PAD
    cv.image(image, ix, iy, iw, ih)
    cv.rect(ix, iy, iw, ih, line=LINE, line_w=0.5)
    base = y + CARD_PAD + ih + 12.0
    for i, line in enumerate(lines):
        cv.text(x + CARD_PAD, base + i * CAPTION_LINE, line, 7.6,
                INK_2 if i == 0 else MUTED, "body", 2 if i == 0 else 1)
    return h


def _caption_lines(cv, text, width, max_lines=3):
    if isinstance(text, (list, tuple)):
        return [cv.fit(str(t), 7.6, width) for t in text][:max_lines]
    return cv.wrap(text, 7.6, width, max_lines=max_lines)


def _has_images(images):
    if not images:
        return False
    return bool(images.get("photo") is not None or images.get("crops")
                or images.get("panel") is not None)


def _page_captures(cv, W, H, data, images, page, total):
    crops = [c for c in (images.get("crops") or []) if c and c.get("image") is not None][:2]
    photo = images.get("photo")
    panel = images.get("panel")
    subtitle = i18n.t("report.shots.sub")
    if crops:
        subtitle = i18n.t("report.shots.sub_crops")
    _page_background(cv, W, H)
    _page_title(cv, W, data, i18n.t("report.shots.title"), subtitle)
    inner = W - 2 * MARGIN
    gap = 12.0
    y = TABLE_TOP + 4.0
    bottom = H - 50.0

    if photo is not None:
        lines = _caption_lines(cv, images.get("photo_caption") or i18n.t("report.shots.photo"),
                               inner - 2 * CARD_PAD, 2)
        reserve = (gap + 250.0) if (crops or panel is not None) else 0.0
        max_h = min(300.0, bottom - y - reserve)
        h = _capture_height(photo, inner, lines, max_h)
        _capture_card(cv, MARGIN, y, inner, h, photo, lines)
        y += h + gap

    remaining = bottom - y
    if remaining < 80 or (not crops and panel is None):
        _footer(cv, W, H, page, total)
        return

    panel_w = 0.0
    panel_lines = []
    if panel is not None:
        panel_lines = _caption_lines(cv, images.get("panel_caption") or i18n.t("report.shots.panel"),
                                     190.0, 1)
        box_h = remaining - 2 * CARD_PAD - _caption_height(panel_lines)
        panel_w = min(210.0, box_h * panel.width() / float(panel.height()) + 2 * CARD_PAD)
        if not crops:
            panel_h = min(remaining, 470.0)
            box_h = panel_h - 2 * CARD_PAD - _caption_height(panel_lines)
            panel_w = min(inner, box_h * panel.width() / float(panel.height()) + 2 * CARD_PAD)
            _capture_card(cv, MARGIN + (inner - panel_w) / 2.0, y, panel_w, panel_h, panel,
                          panel_lines)
            _footer(cv, W, H, page, total)
            return
        panel_w = max(panel_w, 120.0)

    col_w = inner - (panel_w + gap if panel is not None else 0.0)
    per_crop = (remaining - gap * (len(crops) - 1)) / float(len(crops))
    cy = y
    for crop in crops:
        lines = _caption_lines(cv, crop.get("caption") or "", col_w - 2 * CARD_PAD, 3)
        h = _capture_height(crop["image"], col_w, lines, per_crop)
        _capture_card(cv, MARGIN, cy, col_w, h, crop["image"], lines)
        cy += h + gap
    if panel is not None:
        _capture_card(cv, MARGIN + col_w + gap, y, panel_w, remaining, panel, panel_lines)
    _footer(cv, W, H, page, total)


# --------------------------------------------------------------------------
# Photos picked by the AI: one thumbnail per photo, red cross and confidence value
# --------------------------------------------------------------------------

AI_CELL = 58.0                      # side of a thumbnail, in points
AI_GAP = 7.0
AI_CAPTION_H = 11.0
AI_HEAD_H = 42.0                    # marker title + two lines of information
AI_THUMB_PX = 160                   # side of the painted thumbnail, in pixels
AI_FALLBACK_PX = 96                 # region read from the photo when the crop no longer exists
AI_RED = "#ff4a2e"


def _ai_columns(width):
    return max(1, int((width - 2 * MARGIN + AI_GAP) // (AI_CELL + AI_GAP)))


def _ai_plan(sections, width=A4_W, height=A4_H):
    """Pages of the photos picked by the AI, without drawing anything.

    `sections`: [{"label": str, "items": [...]}]. Returns a list of pages; a page is a
    list of ("head", section, continued) or ("row", [items]). A marker that does not fit on
    the page continues on the next one, with its title repeated.
    """
    cols = _ai_columns(width)
    row_h = AI_CELL + AI_CAPTION_H + AI_GAP
    top = TABLE_TOP + 4.0
    bottom = height - 50.0
    pages, y = [[]], top
    for section in sections:
        items, i, continued = section["items"], 0, False
        while i < len(items):
            if y + AI_HEAD_H + row_h > bottom and pages[-1]:
                pages.append([])
                y = top
            pages[-1].append(("head", section, continued))
            y += AI_HEAD_H
            while i < len(items) and y + row_h <= bottom:
                pages[-1].append(("row", items[i:i + cols]))
                y += row_h
                i += cols
            if i < len(items):
                pages.append([])
                y = top
                continued = True
            else:
                y += 8.0
    return pages if pages != [[]] else []


def _page_ai(cv, W, H, data, plan, page, total):
    _page_background(cv, W, H)
    _page_title(cv, W, data, i18n.t("report.ai.title"), i18n.t("report.ai.sub"))
    y = TABLE_TOP + 4.0
    for entry in plan:
        if entry[0] == "head":
            section, continued = entry[1], entry[2]
            title = i18n.t("report.ai.marker", label=section["label"], n=len(section["items"]))
            if continued:
                title = "{} {}".format(title, i18n.t("report.ai.cont"))
            cv.text(MARGIN, y + 12.0, title, 10.5, INK, "body", 2)
            cv.line(MARGIN, y + 17.0, W - MARGIN, y + 17.0, LINE, 0.6)
            lines = [] if continued else cv.wrap(" {} ".format(DOT).join(ai_info_parts(section.get("info") or {})),
                                                 7.6, W - 2 * MARGIN, max_lines=2)
            for k, line in enumerate(lines):
                cv.text(MARGIN, y + 28.0 + k * CAPTION_LINE, line, 7.6, MUTED)
            y += AI_HEAD_H
            continue
        for k, item in enumerate(entry[1]):
            x = MARGIN + k * (AI_CELL + AI_GAP)
            cv.image(item["image"], x, y, AI_CELL, AI_CELL)
            cv.rect(x, y, AI_CELL, AI_CELL, line=item["ring"], line_w=0.7)
            cv.text(x + AI_CELL / 2.0, y + AI_CELL + 9.0, item["caption"], 7.2, item["color"],
                    "body", 1, "c")
        y += AI_CELL + AI_CAPTION_H + AI_GAP
    _footer(cv, W, H, page, total)


def _median_of(values):
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2.0


def ai_info_parts(info):
    """Short phrases describing a marker in the report: error, pointing, confidence, GCP.

    `info`: dict from ai_sections (missing or None keys are not mentioned). No Qt.
    """
    parts = []
    if info.get("total") is not None:
        parts.append(i18n.t("report.ai.info_error", total=fmt_num(info["total"], 3),
                            x=fmt_num(info.get("x"), 3), y=fmt_num(info.get("y"), 3),
                            z=fmt_num(info.get("z"), 3)))
    if info.get("median_px") is not None:
        parts.append(i18n.t("report.ai.info_pointing", median=fmt_num(info["median_px"], 1),
                            max=fmt_num(info["max_px"], 1)))
    if info.get("conf_mean") is not None:
        parts.append(i18n.t("report.ai.info_conf", mean=fmt_num(info["conf_mean"], 2),
                            min=fmt_num(info["conf_min"], 2)))
    if info.get("projections"):
        parts.append(i18n.t("report.ai.info_projections", n=info["projections"]))
    if info.get("excluded"):
        parts.append(i18n.t("report.ai.info_excluded", n=info["excluded"]))
    if info.get("gcp") is True:
        text = i18n.t("report.ai.info_gcp_yes")
    elif info.get("gcp") is False:
        text = i18n.t("report.ai.info_gcp_no")
    else:
        text = None
    if text:
        if info.get("accuracy") is not None:
            text = "{} ({})".format(text, i18n.t("report.ai.info_accuracy", acc=fmt_num(info["accuracy"], 3)))
        parts.append(text)
    return parts


def _ai_mark(image, cx, cy, color):
    """Four-armed cross (empty centre, white halo) on a thumbnail of AI_THUMB_PX."""
    marked = image.convertToFormat(QtGui.QImage.Format_RGB32)
    painter = QtGui.QPainter(marked)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        arm, gap = marked.width() / 5.0, marked.width() / 28.0
        for tone, width in (("#ffffff", 4.2), (color, 2.2)):
            painter.setPen(QtGui.QPen(QtGui.QColor(tone), width))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                painter.drawLine(QtCore.QPointF(cx + dx * gap, cy + dy * gap),
                                 QtCore.QPointF(cx + dx * arm, cy + dy * arm))
    finally:
        painter.end()
    return marked


def _ai_excluded(image):
    """Thumbnail of an excluded photo: darkened, with a large red cross from corner to corner."""
    marked = image.convertToFormat(QtGui.QImage.Format_RGB32)
    painter = QtGui.QPainter(marked)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.fillRect(marked.rect(), QtGui.QColor(0, 0, 0, 110))
        w, h = marked.width(), marked.height()
        inset = w * 0.12
        for tone, width in (("#ffffff", w * 0.07), (AI_RED, w * 0.04)):
            painter.setPen(QtGui.QPen(QtGui.QColor(tone), width, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            painter.drawLine(QtCore.QPointF(inset, inset), QtCore.QPointF(w - inset, h - inset))
            painter.drawLine(QtCore.QPointF(w - inset, inset), QtCore.QPointF(inset, h - inset))
    finally:
        painter.end()
    return marked


def _ai_thumb(entry, excluded=False):
    """Square thumbnail of a proposal, cross on its point; None if the photo is unreadable.

    The crop written by the detection is reused when it still exists; otherwise the region
    is read from the original photo. An `excluded` photo carries a large red cross.
    """
    x, y = entry["x"], entry["y"]
    if review.has_crop(entry) and os.path.isfile(entry["big"]):
        source = QtGui.QImage(entry["big"])
        if not source.isNull():
            left, top, side = review.source_rect(entry, 1.0, source.width())
            rect = QtCore.QRect(int(round(left)), int(round(top)), int(round(side)), int(round(side)))
            image = source.copy(rect).scaled(AI_THUMB_PX, AI_THUMB_PX, QtCore.Qt.IgnoreAspectRatio,
                                             QtCore.Qt.SmoothTransformation)
            if excluded:
                return _ai_excluded(image)
            cx, cy = review.to_view(entry, x, y, AI_THUMB_PX, 1.0)
            return _ai_mark(image, cx, cy, AI_RED)
    region = _read_region(entry["image_path"], x, y, AI_FALLBACK_PX, AI_FALLBACK_PX,
                          msapi.camera_size(entry["camera"]))
    if region is None:
        return None
    image, lx, ly, _scale = region
    factor = AI_THUMB_PX / float(image.width())
    image = image.scaled(AI_THUMB_PX, int(round(image.height() * factor)), QtCore.Qt.IgnoreAspectRatio,
                         QtCore.Qt.SmoothTransformation)
    if excluded:
        return _ai_excluded(image)
    return _ai_mark(image, lx * factor, ly * factor, AI_RED)


def _natural(label):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(label))]


def _ai_still_there(entry):
    """Is the proposal still in the project, in the state the journal gives it?

    Kept: projection not placed. Accepted or refined: projection placed by hand (a later
    Ctrl+Z or "Delete" takes it out of the report).
    """
    projection = msapi.get_projection(entry["marker"], entry["camera"])
    if projection is None:
        return False
    return not projection[2] if entry["state"] == review.KEPT else bool(projection[2])


def _ai_info(entries, data):
    """Information about a marker for the header of its section (see ai_info_parts).

    The error and the number of projections come from `data` (collect_data); the pointing
    accuracy is the gap, in pixels, between each retained point and the reprojection of the
    3D point of the marker; the confidence is the AI one on the retained points.
    """
    marker = entries[0]["marker"]
    info = {}
    row = next((m for m in (data or {}).get("markers", []) if m["label"] == entries[0]["label"]), None)
    if row is not None:
        info.update(total=row["total"], x=row["x"], y=row["y"], z=row["z"], projections=row["projections"])
    chk = msapi.chunk()
    gaps = []
    for entry in entries:
        estimate = msapi.marker_estimate(chk, marker, entry["camera"])
        if estimate is not None:
            gaps.append(math.hypot(entry["x"] - estimate[0], entry["y"] - estimate[1]))
    if gaps:
        info.update(median_px=_median_of(gaps), max_px=max(gaps))
    confs = [e["conf"] for e in entries if e["state"] != review.REFINED]
    if confs:
        info.update(conf_mean=sum(confs) / len(confs), conf_min=min(confs))
    info["gcp"], info["accuracy"] = msapi.marker_reference_info(marker, chk)
    return info


def ai_sections(state, data=None):
    """Thumbnails of the photos picked by the AI, per marker: [{"label", "items": [...], "info": {...}}].

    Kept, accepted or refined proposals count if their projection still exists in the
    project; excluded photos appear too, with a large red cross, without entering the
    statistics of the marker. An unreadable photo is simply absent.
    """
    grouped, kept, excluded = {}, {}, {}
    for entry in getattr(state, "aigcp_log", None) or []:
        if entry["state"] not in (review.KEPT, review.ACCEPTED, review.REFINED, review.REMOVED):
            continue
        out = entry["state"] == review.REMOVED
        try:
            if not out and not _ai_still_there(entry):
                continue
            image = _ai_thumb(entry, excluded=out)
        except Exception as exc:
            journal.write("Report: AI thumbnail skipped: {!r}".format(exc))
            continue
        if image is None:
            continue
        if out:
            excluded[entry["label"]] = excluded.get(entry["label"], 0) + 1
            grouped.setdefault(entry["label"], []).append({
                "image": image, "camera": entry["camera_label"],
                "caption": i18n.t("report.ai.excluded"), "color": AI_RED, "ring": AI_RED,
            })
            continue
        manual = entry["state"] == review.REFINED
        kept.setdefault(entry["label"], []).append(entry)
        grouped.setdefault(entry["label"], []).append({
            "image": image, "camera": entry["camera_label"],
            "caption": i18n.t("report.ai.manual") if manual else fmt_num(entry["conf"], 2),
            "color": GREEN_DEEP if manual else MUTED,
            "ring": GREEN if manual else LINE,
        })
    sections = []
    for label in sorted(grouped, key=_natural):
        try:
            info = _ai_info(kept[label], data) if label in kept else {}
        except Exception as exc:
            journal.write("Report: information for {} skipped: {!r}".format(label, exc))
            info = {}
        if excluded.get(label):
            info["excluded"] = excluded[label]
        sections.append({"label": label, "info": info,
                         "items": sorted(grouped[label], key=lambda it: it["camera"])})
    return sections


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

def _new_writer(path, title):
    return pdfkit.new_writer(path, title, "AeroTools Aero-GCP (HELIX AERO)")


def page_count(data, images=None):
    """Number of pages that build_pdf will produce (A4: 842 pt high)."""
    ai = len(_ai_plan((images or {}).get("ai_picks") or []))
    return 1 + _table_pages_count(len(data["markers"]), 841.89) + (1 if _has_images(images) else 0) + ai


def build_pdf(data, path, images=None):
    """Paints the report into `path` and returns the number of pages.

    `images` (all optional): {"photo": QImage, "photo_caption": str,
    "crops": [{"image": QImage, "caption": str}], "panel": QImage,
    "panel_caption": str, "ai_picks": [{"label": str, "items": [...]}]} (see ai_sections).
    Raises an exception if the file cannot be written: the caller (export_pdf) turns it into
    a message.
    """
    if QtWidgets.QApplication.instance() is None:
        raise RuntimeError(i18n.t("err.no_qt"))
    images = images or {}
    total = page_count(data, images)
    title = i18n.t("report.doc_title", chunk=data.get("chunk") or "").strip()
    tmp = path + ".part"
    writer = _new_writer(tmp, title)
    painter = QtGui.QPainter(writer)
    if not painter.isActive():
        _remove(tmp)
        raise IOError(i18n.t("err.cannot_write", path=path))
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.scale(PT_TO_DEVICE, PT_TO_DEVICE)
        W = writer.width() / PT_TO_DEVICE
        H = writer.height() / PT_TO_DEVICE
        cv = _Canvas(painter, _Style())
        _page_summary(cv, W, H, data, 1, total)
        writer.newPage()
        next_no = _draw_table_pages(writer.newPage, cv, W, H, data, 2, total)
        pages = next_no - 1
        if _has_images(images):
            writer.newPage()
            _page_captures(cv, W, H, data, images, pages + 1, total)
            pages += 1
        for plan in _ai_plan(images.get("ai_picks") or [], W, H):
            writer.newPage()
            _page_ai(cv, W, H, data, plan, pages + 1, total)
            pages += 1
    except Exception:
        painter.end()
        _remove(tmp)
        raise
    painter.end()
    try:
        os.replace(tmp, path)
    except OSError:
        _remove(tmp)
        raise
    return pages


# --------------------------------------------------------------------------
# Screenshots (Qt, inside Metashape)
# --------------------------------------------------------------------------

def _to_image(obj):
    """QImage from a QPixmap or a QImage, None if empty."""
    try:
        image = obj.toImage() if hasattr(obj, "toImage") else obj
        return None if image is None or image.isNull() else image
    except Exception:
        return None


def looks_blank(image, grid=48):
    """True if the image is empty or has only one or two colours (failed GL render)."""
    try:
        if image is None or image.isNull():
            return True
        rgb = image.convertToFormat(QtGui.QImage.Format_RGB32)
        w, h = rgb.width(), rgb.height()
        if w < 8 or h < 8:
            return True
        seen = set()
        for j in range(grid):
            for i in range(grid):
                seen.add(int(rgb.pixel(int((i + 0.5) * w / grid), int((j + 0.5) * h / grid))))
                if len(seen) > 2:
                    return False
        return True
    except Exception:
        return True


def _grab_widget(widget):
    try:
        return _to_image(widget.grab())
    except Exception:
        return None


def _grab_screen_region(widget):
    """Plan B: pixels actually displayed on screen for this widget (GL rendering)."""
    try:
        screen = QtGui.QGuiApplication.primaryScreen()
        window = widget.window()
        origin = widget.mapTo(window, QtCore.QPoint(0, 0))
        pixmap = screen.grabWindow(int(window.winId()), origin.x(), origin.y(),
                                   widget.width(), widget.height())
        return _to_image(pixmap)
    except Exception:
        return None


def _grab_photo_view():
    """Screenshot of the open photo view, or None."""
    for widget in msapi.photo_view_widgets():
        for grabber in (_grab_widget, _grab_screen_region):
            image = grabber(widget)
            if image is not None and not looks_blank(image):
                return image
    return None


def _read_region(path, cx, cy, width, height, sensor):
    """Region `width` x `height` (pixels) centred on (cx, cy) in the image file.

    (cx, cy) is in Metashape sensor pixels. Returns (QImage, local_cx,
    local_cy, file/sensor scale) or None if the file is unreadable or does
    not have the same aspect ratio as the sensor.
    """
    if not path or not os.path.isfile(path):
        return None
    reader = QtGui.QImageReader(path)
    size = reader.size()
    if not size.isValid():
        full = QtGui.QImage(path)
        if full.isNull():
            return None
        size = full.size()
    iw, ih = size.width(), size.height()
    sw, sh = sensor
    scale = 1.0
    if sw and sh:
        if abs(iw / float(ih) - sw / float(sh)) > 0.02:
            return None
        scale = iw / float(sw)
    fx, fy = cx * scale, cy * scale
    rw, rh = int(min(width, iw)), int(min(height, ih))
    x0 = int(max(0, min(iw - rw, round(fx - rw / 2.0))))
    y0 = int(max(0, min(ih - rh, round(fy - rh / 2.0))))
    rect = QtCore.QRect(x0, y0, rw, rh)
    reader = QtGui.QImageReader(path)
    reader.setClipRect(rect)
    image = reader.read()
    if image.isNull() or image.width() != rw or image.height() != rh:
        image = QtGui.QImage(path).copy(rect)
    if image.isNull():
        return None
    return image, fx - x0, fy - y0, scale


def _annotate(image, zoom, posed, estimated):
    """Enlarges the thumbnail, draws the placed point (circle) and the estimated one (cross)."""
    if zoom != 1:
        image = image.scaled(image.width() * zoom, image.height() * zoom,
                             QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    big = image.convertToFormat(QtGui.QImage.Format_RGB32)
    painter = QtGui.QPainter(big)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        unit = max(1.0, big.width() / 150.0)
        px, py = posed[0] * zoom, posed[1] * zoom
        if estimated is not None:
            ex, ey = estimated[0] * zoom, estimated[1] * zoom
            if math.hypot(ex - px, ey - py) > 4 * unit:
                for color, width in (("#ffffff", 1.8 * unit), (ORANGE, 0.8 * unit)):
                    painter.setPen(QtGui.QPen(QtGui.QColor(color), width))
                    painter.drawLine(QtCore.QPointF(px, py), QtCore.QPointF(ex, ey))
            arm = 4.2 * unit
            for color, width in (("#ffffff", 2.0 * unit), (ORANGE, 0.9 * unit)):
                painter.setPen(QtGui.QPen(QtGui.QColor(color), width))
                painter.drawLine(QtCore.QPointF(ex - arm, ey - arm), QtCore.QPointF(ex + arm, ey + arm))
                painter.drawLine(QtCore.QPointF(ex - arm, ey + arm), QtCore.QPointF(ex + arm, ey - arm))
        painter.setBrush(QtCore.Qt.NoBrush)
        for color, width in (("#ffffff", 2.0 * unit), (GREEN, 1.0 * unit)):
            painter.setPen(QtGui.QPen(QtGui.QColor(color), width))
            painter.drawEllipse(QtCore.QPointF(px, py), 6.5 * unit, 6.5 * unit)
    finally:
        painter.end()
    return big


def _vec_xy(vec):
    try:
        return float(vec.x), float(vec.y)
    except AttributeError:
        return float(vec[0]), float(vec[1])


def _marker_views(marker):
    """[(gap_px or None, camera, placed (u, v), estimated (u, v) or None)], gap descending."""
    views = []
    try:
        position = marker.position
    except Exception:
        position = None
    for cam in msapi.projection_cameras(marker, without_proposals=True):
        try:
            posed = _vec_xy(marker.projections[cam].coord)
        except Exception:
            continue
        estimated, gap = None, None
        if position is not None:
            try:
                uv = cam.project(position)
                if uv is not None:
                    estimated = _vec_xy(uv)
                    gap = math.hypot(estimated[0] - posed[0], estimated[1] - posed[1])
            except Exception:
                estimated, gap = None, None
        views.append((gap, cam, posed, estimated))
    views.sort(key=lambda v: -(v[0] if v[0] is not None else -1.0))
    return views


def _crop_for_marker(marker, entry):
    """Annotated thumbnail and its caption for a marker, or None."""
    for gap, cam, posed, estimated in _marker_views(marker):
        region = _read_region(msapi.camera_image_path(cam), posed[0], posed[1],
                              CROP_W, CROP_H, msapi.camera_size(cam))
        if region is None:
            continue
        image, lx, ly, scale = region
        est_local = None
        if estimated is not None:
            est_local = (lx + (estimated[0] - posed[0]) * scale,
                         ly + (estimated[1] - posed[1]) * scale)
        big = _annotate(image, CROP_ZOOM, (lx, ly), est_local)
        first = entry["label"]
        if entry["total"] is not None:
            first = i18n.t("report.shots.crop_error", label=entry["label"],
                           value=fmt_m(entry["total"]))
        second = msapi.camera_label(cam)
        if gap is not None:
            second = i18n.t("report.shots.crop_gap", camera=second, gap=fmt_num(gap, 1))
        return {"image": big, "caption": [first, second]}
    return None


def _pick_crops(chk, data, limit=2):
    """Thumbnails of the markers with the largest error, otherwise the least pointed ones."""
    by_label = {}
    for marker in msapi.markers(chk):
        by_label.setdefault(msapi.marker_label(marker), marker)
    pointed = [m for m in data["markers"] if m["pointed"]]
    with_error = [m for m in pointed if m["total"] is not None]
    if with_error:
        ranked = sorted(with_error, key=lambda m: -m["total"])
    else:
        ranked = sorted(pointed, key=lambda m: m["projections"])
    crops = []
    for entry in ranked:
        marker = by_label.get(entry["label"])
        if marker is None:
            continue
        try:
            crop = _crop_for_marker(marker, entry)
        except Exception as exc:
            journal.write("Report: thumbnail of {} skipped: {!r}".format(entry["label"], exc))
            continue
        if crop is not None:
            crops.append(crop)
        if len(crops) >= limit:
            break
    return crops


def _photo_fallback(state):
    """Plan B for the photo view: wide crop of the open image around the current marker."""
    marker = getattr(state, "active_marker", None)
    cam = msapi.current_camera()
    if marker is None or cam is None:
        return None, ""
    try:
        posed = _vec_xy(marker.projections[cam].coord)
    except Exception:
        return None, ""
    region = _read_region(msapi.camera_image_path(cam), posed[0], posed[1], 1500, 1000,
                          msapi.camera_size(cam))
    if region is None:
        return None, ""
    image, lx, ly, _scale = region
    big = _annotate(image, 1, (lx, ly), None)
    return big, i18n.t("report.shots.fallback", camera=msapi.camera_label(cam),
                       marker=msapi.marker_label(marker))


def collect_images(state, data):
    """Screenshots for page 3. Each failed screenshot is simply absent."""
    images = {}
    chk = msapi.chunk()
    try:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.processEvents()
    except Exception:
        pass

    try:
        photo = _grab_photo_view()
        caption = ""
        if photo is not None:
            cam = msapi.current_camera()
            if cam is not None:
                caption = i18n.t("report.shots.photo_cam", camera=msapi.camera_label(cam))
            else:
                caption = i18n.t("report.shots.photo")
            marker = getattr(state, "active_marker", None)
            if marker is not None:
                try:
                    caption = i18n.t("report.shots.photo_marker", caption=caption,
                                     marker=msapi.marker_label(marker))
                except Exception:
                    pass
        else:
            photo, caption = _photo_fallback(state)
        if photo is not None:
            images["photo"], images["photo_caption"] = photo, caption
    except Exception as exc:
        journal.write("Report: photo view screenshot skipped: {!r}".format(exc))

    try:
        crops = _pick_crops(chk, data)
        if crops:
            images["crops"] = crops
    except Exception as exc:
        journal.write("Report: thumbnails skipped: {!r}".format(exc))

    try:
        picks = ai_sections(state, data)
        if picks:
            images["ai_picks"] = picks
    except Exception as exc:
        journal.write("Report: AI-picked photos skipped: {!r}".format(exc))

    try:
        panel = getattr(state, "panel", None)
        if panel is not None:
            image = _grab_widget(panel)
            if image is not None and not looks_blank(image):
                images["panel"] = image
                images["panel_caption"] = i18n.t("report.shots.panel")
    except Exception as exc:
        journal.write("Report: panel screenshot skipped: {!r}".format(exc))
    return images


# --------------------------------------------------------------------------
# Dialogs (isolated so that tests can replace them)
# --------------------------------------------------------------------------

def ask_save_path(parent, caption, default_path, name_filter):
    """Path chosen by the user, or an empty string."""
    result = QtWidgets.QFileDialog.getSaveFileName(parent, caption, default_path, name_filter)
    path = result[0] if isinstance(result, (tuple, list)) else result
    return str(path or "")


def _exec(box):
    runner = getattr(box, "exec_", None) or getattr(box, "exec")
    return runner()


def show_message(parent, title, text, warning=False):
    try:
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Warning if warning else QtWidgets.QMessageBox.Information,
            title, text, QtWidgets.QMessageBox.Ok, parent)
        _exec(box)
    except Exception:
        msapi.message_box("{}: {}".format(title, text))


def offer_open(parent, title, path):
    """Small box with a button to open the file. Returns True if opened."""
    try:
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Information, title,
                                    i18n.t("dlg.saved", path=path),
                                    QtWidgets.QMessageBox.NoButton, parent)
        open_button = box.addButton(i18n.t("dlg.open"), QtWidgets.QMessageBox.AcceptRole)
        box.addButton(i18n.t("dlg.close"), QtWidgets.QMessageBox.RejectRole)
        _exec(box)
        if box.clickedButton() is open_button:
            return open_file(path)
    except Exception as exc:
        journal.write("Report: could not offer to open the file: {!r}".format(exc))
    return False


def open_file(path):
    try:
        return bool(QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path)))
    except Exception:
        return False


def _busy(on):
    try:
        if on:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        else:
            QtWidgets.QApplication.restoreOverrideCursor()
    except Exception:
        pass


def _status(state, text):
    panel = getattr(state, "panel", None)
    setter = getattr(panel, "set_status", None)
    if setter is not None:
        try:
            setter(text)
        except Exception:
            pass


def _explain(exc):
    if isinstance(exc, PermissionError):
        return i18n.t("err.denied")
    return str(exc) or exc.__class__.__name__


def _fail(state, parent, kind, exc):
    """Failure of an export (`kind`: "pdf" or "csv"): journal, status, message."""
    journal.write("Report: {} export failed: {!r}".format(str(kind).upper(), exc))
    what = i18n.t("err." + kind)
    _status(state, what)
    show_message(parent, what, _explain(exc), warning=True)


def _prepare(state, parent, ext, caption, name_filter, kind):
    """Chunk, data and chosen path. Returns (data, path) or (None, None)."""
    chk = msapi.chunk()
    if chk is None:
        show_message(parent, i18n.t("dlg.title"), i18n.t("dlg.no_chunk"), warning=True)
        return None, None
    data = collect_data(chk)
    if not data["markers"]:
        journal.write("Report: no marker in chunk {}".format(data["chunk"]))
        show_message(parent, i18n.t("dlg.title"), i18n.t("dlg.no_markers"), warning=True)
        return None, None
    default = os.path.join(default_directory(), suggested_filename(data["chunk"], ext, kind=kind))
    path = ask_save_path(parent, caption, default, name_filter)
    if not path:
        return None, None
    if not path.lower().endswith(ext):
        path += ext
    return data, path


# --------------------------------------------------------------------------
# Entry points of the panel
# --------------------------------------------------------------------------

def export_pdf(state, parent=None):
    """Asks for the file, writes the PDF report. Returns the path, or None.

    Never raises: an error goes to the journal and into a readable message.
    """
    try:
        data, path = _prepare(state, parent, ".pdf", i18n.t("dlg.save_pdf"),
                              i18n.t("dlg.filter_pdf"), i18n.t("file.report"))
        if data is None:
            return None
        _busy(True)
        try:
            images = collect_images(state, data)
            pages = build_pdf(data, path, images)
        finally:
            _busy(False)
        journal.write("PDF report written: {} ({} pages, {} markers)".format(
            path, pages, data["stats"]["n_markers"]))
        done = i18n.t("dlg.pdf_done")
        _status(state, done)
        offer_open(parent, done, path)
        return path
    except Exception as exc:
        _fail(state, parent, "pdf", exc)
        return None


def export_csv(state, parent=None):
    """Same logic for the CSV of the table (Excel). Returns the path, or None."""
    try:
        data, path = _prepare(state, parent, ".csv", i18n.t("dlg.save_csv"),
                              i18n.t("dlg.filter_csv"), i18n.t("file.table"))
        if data is None:
            return None
        write_csv(data, path)
        journal.write("CSV table written: {} ({} markers)".format(
            path, data["stats"]["n_markers"]))
        done = i18n.t("dlg.csv_done")
        _status(state, done)
        offer_open(parent, done, path)
        return path
    except Exception as exc:
        _fail(state, parent, "csv", exc)
        return None
