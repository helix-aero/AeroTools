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
    "tool.fastgcp.subtitle": "GCP marking, by hand or with AI",
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

    # -- Residual check and markers to watch (qc.py, panel.py) ---------------
    "qc.title_residual": "High residual (above {limit} m)",
    "qc.title_few": "Fewer than {n} marked photos",
    "qc.item_residual": "{label} · {total} m · worst photo {px} px",
    "qc.item_residual_plain": "{label} · {total} m",
    "qc.item_few": "{label} · {n} photo(s)",
    "qc.none": "No marker to watch",
    "qc.opened": "{label}: worst photo opened",

    # -- AI detection: pick proposals (aigcp.py, panel.py) -------------------
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
    "aigcp.type_damier": "Checker",
    "aigcp.type_bombe": "Spray",
    "aigcp.scope_marker": "Selected markers",
    "aigcp.scope_all": "All markers",
    "aigcp.ignore_both": "Ignore green + blue",
    "aigcp.ignore_green": "Ignore green",
    "aigcp.ignore_blue": "Ignore blue",
    "aigcp.ignore_none": "Ignore none",
    "aigcp.review": "Review",
    "aigcp.auto_review": "Open the review panel automatically",
    "review.title": "Review panel",
    "review.view": "View",
    "review.view_note": "Settings to see better: the photos are not modified.",
    "review.no_numpy": "Settings unavailable (numpy missing).",
    "review.zoom": "Zoom",
    "review.exposure": "Exposure",
    "review.contrast": "Contrast",
    "review.saturation": "Saturation",
    "review.sharpness": "Sharpness",
    "review.reset": "Reset",
    "review.status_kept": "kept",
    "review.status_removed": "excluded",
    "review.status_refined": "refined",
    "review.status_accepted": "validated",
    "review.refine_photo": "photo {n}/{total}",
    "review.refine_progress": "{done}/{total} picked",
    "review.refine_next": "Next",
    "review.refine_validate_exit": "Validate and exit",
    "review.hint": "Click a thumbnail to see it above, then click in that photo to place a new cross. Double-click a thumbnail to open it full size. Ctrl or Shift + click: pick several, then Exclude (or right-click). Shift + wheel: zoom. Ctrl + wheel: thumbnail size.",
    "review.exclude": "Exclude",
    "review.restore": "Put back",
    "review.refine": "Refine…",
    "review.accept": "Validate",
    "review.summary": "{kept} kept · {removed} excluded · {refined} refined · {accepted} validated",
    "review.no_crop": "No thumbnail",
    "review.empty": "AI: no proposal to review",
    "review.refine_title": "Refine marker {label} on {camera}",
    "review.refine_hint": "Click (or drag) on the centre of the target. Wheel: zoom. Middle button: pan. Enter: next photo. The red cross is the AI proposal.",
    "review.refine_validate": "Validate",
    "review.refine_cancel": "Cancel",
    "review.confirm_accept": "Set {n} point(s) as manual picks?",
    "review.accepted_status": "AI: {n} point(s) validated",
    "aigcp.progress": "AI ({kind}): {done}/{total} photo(s)…",
    "aigcp.run": "Run",
    "aigcp.clear": "Clear proposals",
    "aigcp.running_all": "AI ({kind}): {n} photo(s) to test on {k} marker(s)…",
    "aigcp.done_all": "AI: {n} proposal(s) on {k} marker(s), {m} photo(s) tested, {g} rejected by the guard",
    "aigcp.none_all": "AI: no target found ({m} photo(s), {k} marker(s)), {g} rejected by the guard",
    "aigcp.no_markers": "AI: no marker with a 3D position",
    "aigcp.done_gate": "AI: {n} proposal(s) on {m} photo(s) for {label}, {g} rejected by the guard",
    "aigcp.none_gate": "AI: no proposal kept for {label}, {g} rejected by the guard",

    # -- Undo (marking.py) ---------------------------------------------------
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
    "report.ai.title": "Photos picked by the AI",
    "report.ai.sub": "Review panel: red cross = kept point, large red cross = excluded photo, confidence under each thumbnail",
    "report.ai.marker": "{label} · {n} photo(s)",
    "report.ai.cont": "(continued)",
    "report.ai.manual": "manual",
    "report.ai.info_error": "3D error {total} m (X {x}, Y {y}, Z {z})",
    "report.ai.info_pointing": "Picking: median offset {median} px, maximum {max} px",
    "report.ai.info_conf": "AI confidence: mean {mean}, minimum {min}",
    "report.ai.info_projections": "{n} projection(s)",
    "report.ai.info_gcp_yes": "Used as GCP",
    "report.ai.info_gcp_no": "Check point, not used as GCP",
    "report.ai.info_accuracy": "reference accuracy {acc} m",
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

    # -- Parametres, suppression, credit -----------------------------------
    "review.slider_tip": "Double-click to reset",
    "review.busy_title": "Applying changes",
    "review.busy_note": "One moment, please don't touch Metashape meanwhile.",
    "review.busy_validate": "Applying {n} proposal(s) to the project…",
    "review.busy_refresh": "Updating the marker table…",
    "review.busy_refine": "Applying {n} correction(s) to the project…",
    "panel.remove_projections": "Remove projections ({n})",
    "panel.remove_markers": "Remove markers ({n})",
    "panel.remove_title": "Remove",
    "panel.remove_projections_confirm": "Remove all projections of {n} marker(s): {names}? The markers stay in the project.",
    "panel.remove_markers_confirm": "Delete {n} marker(s) from the project: {names}? FastGCP cannot bring them back.",
    "panel.removed_projections": "{n} projection(s) removed on {k} marker(s)",
    "panel.removed_markers": "{n} marker(s) deleted",
    "panel.credit": "Tool made by {site}",
    "panel.github_tip": "Source code on GitHub",
    "settings.title": "Settings",
    "settings.general": "General",
    "settings.ai_models": "AI models",
    "settings.use_refine": "Use manual refinements to build the training dataset",
    "settings.storage": "Storage location",
    "settings.browse": "Browse…",
    "settings.storage_pick": "Storage folder (dataset, models, training inside)",
    "settings.data_counts": "{p} corrected or validated point(s) and {r} rejected proposal(s) kept (checkerboard: {damier}, spray: {bombe})",
    "settings.data_none": "No annotation yet: correct or exclude proposals in the Review panel.",
    "settings.model": "AI model",
    "settings.add": "Add…",
    "settings.add_soon": "Coming soon",
    "settings.manage": "Manage…",
    "settings.model_base": "Original model, always available.",
    "settings.model_local": "Local model trained on your data ({types}).",
    "settings.refine_model": "Refine my model using my data",
    "settings.revert": "Revert to {name}",
    "settings.reverted": "Original model restored.",
    "settings.train_title": "Refine the AI model",
    "settings.train_confirm": "You are about to retrain the detection model on your own annotations ({p} corrected or validated point(s), {r} rejected).\n\nThe model may perform better on your targets, but possibly worse on others. What you marked as spray will be learned as spray, what you marked as checkerboard as checkerboard: if your annotations contain mistakes, the result will be worse.\n\nTraining uses the graphics card intensively for several minutes. Do not use the computer meanwhile.\n\nYour original model is never modified: you can go back at any time with the Revert button. Start the training?",
    "settings.train_cancel": "Cancel",
    "settings.train_cancelling": "Cancelling…",
    "settings.training": "Training in progress…",
    "settings.train_done": "Training finished. The new model \"{name}\" is now selected: you can test it on your next detection. The Revert button restores the original model.",
    "settings.train_failed": "Training failed: {message}",
    "settings.train_cancelled": "Training cancelled: nothing was changed.",
    "settings.train_busy_detect": "A detection is running: wait for it to finish.",
    "settings.train_running_close": "A training is running: cancel it before closing this window.",
    "settings.manage_title": "Manage models",
    "settings.delete": "Delete",
    "settings.delete_confirm": "Delete the model {name}? This cannot be undone.",
    "settings.base_locked": "The original model cannot be deleted. The star marks the model in use.",
    "settings.close": "Close",

    # -- Rapport : photos exclues ------------------------------------------
    "report.ai.excluded": "excluded",
    "report.ai.info_excluded": "{n} photo(s) excluded",

    # -- Reentrainement : erreurs ------------------------------------------
    "settings.err_busy": "Another training is already running.",
    "settings.err_no_python": "The AI Python environment was not found: {detail}",
    "settings.err_no_script": "The training script was not found: {detail}",
    "settings.err_no_data": "Not enough annotations to train: at least {n} corrected or validated points are needed for one target type.",
    "settings.train_warning": "Warning: on your check data, the new model scores lower than the original one. You can revert to the original model.",

    # -- Boites de dialogue ------------------------------------------------
    "dialogs.yes": "Yes",
    "dialogs.no": "No",
    "dialogs.ok": "OK",
    "panel.busy_projections": "Removing the projections of {n} marker(s)…",

    # -- Retablir, bandeau -------------------------------------------------
    "redo.nothing": "Nothing to redo",
    "redo.failed": "Redo failed",
    "redo.done": "Redone: {label}",
    "panel.ad": "Complex flights made easy",

    # -- Dossier de stockage -----------------------------------------------
    "settings.open_folder": "Open",
    "settings.storage_content": "Contains: annotations (dataset): {a} line(s) · local models (models): {m} · kept detections (aigcp): {d} · training runs (training): {t}",
    "settings.copy_confirm": "This folder holds no annotation or model yet. Copy the {a} annotation(s) and the {m} local model(s) of the current folder into it? The current folder is left untouched.",
    "settings.copy_failed": "The copy failed: {message}",

    # -- Barre de detection IA ---------------------------------------------
    "aigcp.cancel": "Cancel",
    "aigcp.cancelled": "AI: detection cancelled",
    "aigcp.bar": "AI ({kind}) · {done}/{total} photos",
    "aigcp.bar_start": "AI ({kind}) · starting…",

    # -- Exclure apres validation ------------------------------------------
    "review.confirm_exclude_done": "Remove {n} validated or refined point(s) from the project? The photos go back to how they were before the detection, and Restore puts them back.",

    # -- Photo choisie -----------------------------------------------------
    "review.preview": "Selected photo",
    "review.preview_none": "Click a thumbnail to see it here, larger.",
}
