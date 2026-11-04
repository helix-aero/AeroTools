"""English catalog. Same keys and same {fields} as fr.py (a test checks it).

Texts shown in Metashape message boxes stay in plain ASCII, like the French ones.
"""

FORMATS = {
    "decimal": ".",
    "date": "%Y-%m-%d",
    "csv_delimiter": ",",
}

TEXTES = {
    # -- Aero menu and Metashape boxes (bootstrap.py, tool.py) -------------
    "menu.reload": "Reload AeroTools",
    "menu.about": "About",
    "menu.soon": "(coming soon)",
    "boot.unknown_tool": "AeroTools: unknown tool \"{tool}\".",
    "boot.not_ready": "{title} is not available yet.",
    "boot.load_failed": "AeroTools: could not load {tool}.\n\n{error}",
    "boot.start_failed": "AeroTools: {tool} was loaded but did not start.\n\n{error}",
    "boot.about": (
        "AeroTools {version}\n"
        "{vendor} tool suite for Agisoft Metashape.\n\n"
        "Tools:\n{tools}\n\n"
        "Shared code: {root}\n"
        "Workstation settings: {settings}\n\n"
        "The Aero menu is added at each start by the launcher installed in "
        "Metashape's scripts folder. \"Reload AeroTools\" re-reads the code "
        "from the server without restarting Metashape."
    ),
    "tool.fastgcp.subtitle": "Assisted GCP marking",
    "tool.aigcp.subtitle": "Automatic target detection",
    "tool.no_window": (
        "FastGCP needs the Metashape interface: the main window was not found."
    ),
    "tool.chunk_unnamed": "unnamed",

    # -- Panel (panel.py, marking.py) --------------------------------------
    "panel.state_on": "Tool active",
    "panel.state_off": "Tool inactive",
    "panel.active_marker": "Active marker",
    "panel.search": "Search",
    "panel.col.id": "ID",
    "panel.col.x": "Error X",
    "panel.col.y": "Error Y",
    "panel.col.z": "Error Z",
    "panel.col.proj": "Proj.",
    "panel.tip_pointed": "Photos where this marker is placed",
    "panel.tip_seen": "Photos that see this marker",
    "panel.save_picks": "Save marker picks",
    "panel.log": "Log",
    "panel.export": "Export",
    "panel.export.pdf": "PDF report",
    "panel.export.csv": "CSV table",
    "panel.status.reread_failed": "Could not read markers",
    "panel.status.list_reread": "List refreshed",
    "panel.status.export_unavailable": "Export unavailable",
    "panel.status.export_failed": "Export failed",
    "panel.status.progress": "{done}/{total} marked",
    "panel.status.progress_chunk": "{done}/{total} marked · {name}",
    "panel.qc": "Residual check",
    "panel.aigcp": "AI detection",
    "panel.import": "Import GCPs…",

    # -- Residual check and markers to watch (qc.py, panel.py) ---------------
    "qc.title_residual": "High residual (above {limit} m)",
    "qc.title_few": "Fewer than {n} marked photos",
    "qc.item_residual": "{label} · {total} m · worst photo {px} px",
    "qc.item_residual_plain": "{label} · {total} m",
    "qc.item_few": "{label} · {n} photo(s)",
    "qc.none": "No marker to watch",
    "qc.opened": "{label}: worst photo opened",

    # -- AI detection: pick proposals (aigcp.py, panel.py) -------------------
    "aigcp.menu_title": "Propose marker {label} on the other photos",
    "aigcp.menu_damier": "Checkerboard target",
    "aigcp.menu_bombe": "Spray or chalk mark",
    "aigcp.menu_clear": "Clear the AI proposals",
    "aigcp.menu_no_marker": "Pick a marker first",
    "aigcp.running": "AI ({kind}): {n} photo(s) to test for {label}…",
    "aigcp.done": "AI: {n} proposal(s) on {m} photo(s) for {label}, click to validate",
    "aigcp.none": "AI: no target found on {m} photo(s) for {label}",
    "aigcp.no_marker": "AI: pick a marker first",
    "aigcp.no_photos": "AI: no photo to test for {label}",
    "aigcp.busy": "AI: a detection is already running",
    "aigcp.failed": "AI: detection failed (see the journal)",
    "aigcp.chunk_changed": "AI: the chunk changed, results ignored",
    "aigcp.cleared": "AI: {n} proposal(s) cleared",
    "aigcp.bad_type": "AI: unknown target type ({kind})",
    "aigcp.no_python": "AI: AIGCP Python not found ({path})",
    "aigcp.no_weights": "AI: {kind} weights not found ({path})",
    "aigcp.no_runner": "AI: runner not found ({path})",

    # -- GCP import from a text file (gcp_import.py) --------------------------
    "imp.title": "Import GCPs",
    "imp.choose_file": "Choose a file…",
    "imp.dlg_open": "Choose the GCP file",
    "imp.filter_text": "Text files",
    "imp.filter_all": "All files",
    "imp.read_failed": "Could not read the file: {error}",
    "imp.delimiter": "Separator",
    "imp.delim.auto": "Automatic",
    "imp.delim.semicolon": "Semicolon",
    "imp.delim.tab": "Tab",
    "imp.delim.comma": "Comma",
    "imp.delim.space": "Space",
    "imp.header": "First row is a header",
    "imp.field.name": "Name",
    "imp.field.x": "X (east, longitude)",
    "imp.field.y": "Y (north, latitude)",
    "imp.field.z": "Z (height)",
    "imp.none": "— none —",
    "imp.crs": "File coordinate system: {name}",
    "imp.crs_same": "same as the chunk markers",
    "imp.crs_auto": "{name} (suggested: values in degrees)",
    "imp.crs_unset": "not set",
    "imp.crs_choose": "Choose…",
    "imp.crs_caption": "Coordinate system of the GCP file",
    "imp.chunk_crs": "Chunk markers: {name}",
    "imp.chunk_crs_none": "Chunk markers: no coordinate system set, the file's will be used",
    "imp.summary": "{n} points read: {new} new, {upd} updated",
    "imp.rejected": "{n} row(s) skipped, for example row {line} ({reason})",
    "imp.reason.x": "unreadable X",
    "imp.reason.y": "unreadable Y",
    "imp.reason.z": "unreadable Z",
    "imp.reason.name": "empty name",
    "imp.reason.columns": "pick the X and Y columns",
    "imp.warn_overwrite": "{n} existing marker(s) will have their reference replaced.",
    "imp.warn_no_z": "No Z column: height will not be used.",
    "imp.warn_converted": "Coordinates will be converted to the chunk markers' coordinate system.",
    "imp.warn_crs_mismatch": "The values do not match the chosen coordinate system: please check it.",
    "imp.warn_no_crs": "Choose the file's coordinate system.",
    "imp.ok": "Import",
    "imp.cancel": "Cancel",
    "imp.done": "{new} created, {upd} updated",
    "imp.done_errors": ", {n} failed",
    "imp.failed": "Import failed",
    "undo.nothing": "Nothing to undo",
    "undo.failed": "Undo failed",
    "undo.done": "Undone: {label}",

    # -- PDF report: page 1 ------------------------------------------------
    "report.title": "GCP Marking Report",
    "report.no_project": "Unsaved project",
    "report.band.project": "Project",
    "report.band.chunk": "Chunk",
    "report.band.date": "Date",
    "report.band.metashape": "Metashape",
    "report.kpi.markers": "Markers",
    "report.kpi.markers_sub": "in the chunk",
    "report.kpi.pointed": "Placed",
    "report.kpi.pointed_sub": "{pct}% of markers",
    "report.kpi.projections": "Projections",
    "report.kpi.projections_sub": "{mean} per marker",
    "report.kpi.rms_xy": "RMS XY",
    "report.kpi.rms_z": "RMS Z",
    "report.kpi.rms_z_sub": "Total RMS {value}",
    "report.kpi.worst": "Largest error",
    "report.empty": "No measurement available",
    "report.bar.title": "Total error per marker",
    "report.bar.sub": "Distance between estimated and reference position",
    "report.bar.sub_top": "Top {shown} errors out of {total} markers",
    "report.bar.legend_total": "Total error",
    "report.bar.legend_over": "Beyond 2 × RMS",
    "report.bar.legend_rms": "RMS {value}",
    "report.scatter.title": "Horizontal scatter",
    "report.scatter.sub": "X error on the horizontal axis, Y error on the vertical axis",
    "report.scatter.legend_rms": "RMS XY {value}",
    "report.scatter.unit": "in meters",
    "report.axis.title": "RMS by axis",
    "report.axis.xy": "XY",
    "report.axis.total": "Total",
    "report.note.title": "How to read",
    "report.note.none_1": "No error available.",
    "report.note.none_2": (
        "Markers have no reference or have not been triangulated yet."
    ),
    "report.note.errors": (
        "Errors in meters: offset between each marker's estimated position "
        "and its reference."
    ),
    "report.note.threshold": (
        "In orange: error above 2 × RMS ({value} for the total error)."
    ),
    "report.note.no_threshold": (
        "Fewer than 3 markers with an error: no highlight threshold."
    ),

    # -- PDF report: table and histogram -----------------------------------
    "report.table.title": "Marker details",
    "report.table.sub": "{n} markers",
    "report.table.sub_next": "continued",
    "report.table.sub_page": "continued, {page} of {pages}",
    "report.table.id": "ID",
    "report.table.x": "Error X (m)",
    "report.table.y": "Error Y (m)",
    "report.table.z": "Error Z (m)",
    "report.table.total": "Total (m)",
    "report.table.proj": "Proj.",
    "report.table.legend": (
        "In orange: value beyond 2 × the column RMS. "
        "Markers without projections: dash."
    ),
    "report.table.legend_plain": "Markers without projections: dash.",
    "report.hist.page_title": "Projections per marker",
    "report.hist.title": "Projection distribution",
    "report.hist.sub": "Number of markers by projection count",
    "report.hist.axis": "Projections per marker",
    "report.hist.mean": "Mean",
    "report.hist.median": "Median",
    "report.hist.min": "Minimum",
    "report.hist.max": "Maximum",
    "report.hist.low": "Fewer than 3 projections",
    "report.hist.none": "No projection",

    # -- PDF report: screenshots -------------------------------------------
    "report.shots.title": "Screenshots",
    "report.shots.sub": "Photo view, FastGCP panel",
    "report.shots.sub_crops": (
        "Photo view, thumbnails of the markers with the largest errors "
        "(green circle: placed point, orange cross: estimated position), "
        "FastGCP panel"
    ),
    "report.shots.photo": "Photo view",
    "report.shots.photo_cam": "Photo view {camera}",
    "report.shots.photo_marker": "{caption} · current marker {marker}",
    "report.shots.fallback": "{camera} · marker {marker}",
    "report.shots.crop_error": "{label} · error {value}",
    "report.shots.crop_gap": "{camera} · image offset {gap} px",
    "report.shots.panel": "FastGCP panel",
    "report.footer.page": "Page {page} of {total}",
    "report.doc_title": "GCP Marking Report {chunk}",

    # -- Export: files, dialogs and messages -------------------------------
    "file.report": "GCP_report",
    "file.table": "GCP_table",
    "dlg.title": "GCP report",
    "dlg.no_chunk": "No chunk is open in Metashape.",
    "dlg.no_markers": "This chunk has no markers.",
    "dlg.save_pdf": "Export the PDF report",
    "dlg.save_csv": "Export the table as CSV",
    "dlg.filter_pdf": "PDF files (*.pdf)",
    "dlg.filter_csv": "CSV files (*.csv)",
    "dlg.saved": "File saved:\n{path}",
    "dlg.open": "Open",
    "dlg.close": "Close",
    "dlg.pdf_done": "PDF report exported",
    "dlg.csv_done": "CSV table exported",
    "err.pdf": "PDF export failed",
    "err.csv": "CSV export failed",
    "err.denied": (
        "Write denied. The file may be open in another program "
        "or the folder is protected."
    ),
    "err.no_qt": "Qt is not initialized",
    "err.cannot_write": "cannot write: {path}",

    # -- CSV export --------------------------------------------------------
    "csv.id": "ID",
    "csv.x": "Error X (m)",
    "csv.y": "Error Y (m)",
    "csv.z": "Error Z (m)",
    "csv.total": "Total error (m)",
    "csv.projections": "Projections",
    "csv.ref_x": "Reference X",
    "csv.ref_y": "Reference Y",
    "csv.ref_z": "Reference Z",
}
