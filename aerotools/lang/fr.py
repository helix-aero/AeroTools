"""Catalogue francais : langue de reference et de repli d'AeroTools.

Toute cle ajoutee ici doit exister dans les autres langues avec les memes
champs `{nom}` (un test le verifie). Les textes des boites de dialogue de
Metashape (menu, A propos, outil indisponible) restent sans accents, comme
avant : ils passent par l'API de Metashape.
"""

FORMATS = {
    "decimal": ",",
    "date": "%d/%m/%Y",
    "csv_delimiter": ";",
}

TEXTES = {
    # -- Menu Aero et boites de Metashape (bootstrap.py, tool.py) ----------
    "menu.reload": "Recharger AeroTools",
    "menu.about": "A propos",
    "menu.soon": "(a venir)",
    "boot.unknown_tool": "AeroTools : outil inconnu « {tool} ».",
    "boot.not_ready": "{title} n'est pas encore disponible.",
    "boot.load_failed": "AeroTools : impossible de charger {tool}.\n\n{error}",
    "boot.start_failed": (
        "AeroTools : {tool} s'est charge mais n'a pas demarre.\n\n{error}"
    ),
    "boot.about": (
        "AeroTools {version}\n"
        "Suite d'outils {vendor} pour Agisoft Metashape.\n\n"
        "Outils :\n{tools}\n\n"
        "Code partage : {root}\n"
        "Reglages du poste : {settings}\n\n"
        "Le menu Aero est pose a chaque demarrage par le lanceur installe "
        "dans le dossier scripts de Metashape. « Recharger AeroTools » relit "
        "le code du serveur sans redemarrer Metashape."
    ),
    "tool.fastgcp.subtitle": "Pointage GCP assisté",
    "tool.aigcp.subtitle": "Détection automatique des cibles",
    "tool.no_window": (
        "FastGCP a besoin de l'interface de Metashape : "
        "la fenetre principale n'a pas ete trouvee."
    ),
    "tool.chunk_unnamed": "sans nom",

    # -- Panneau (panel.py, marking.py) ------------------------------------
    "panel.state_on": "Tool actif",
    "panel.state_off": "Tool inactif",
    "panel.active_marker": "Marqueur actif",
    "panel.search": "Rechercher",
    "panel.col.id": "ID",
    "panel.col.x": "Erreur X",
    "panel.col.y": "Erreur Y",
    "panel.col.z": "Erreur Z",
    "panel.col.proj": "Proj.",
    "panel.tip_pointed": "Photos où ce marqueur est pointé",
    "panel.tip_seen": "Photos qui voient ce marqueur",
    "panel.save_picks": "Enregistrer les pointages",
    "panel.log": "Journal",
    "panel.export": "Exporter",
    "panel.export.pdf": "Rapport PDF",
    "panel.export.csv": "Tableau CSV",
    "panel.status.reread_failed": "Relecture impossible",
    "panel.status.list_reread": "Liste relue",
    "panel.status.export_unavailable": "Export indisponible",
    "panel.status.export_failed": "Export impossible",
    "panel.status.progress": "{done}/{total} pointés",
    "panel.status.progress_chunk": "{done}/{total} pointés · {name}",
    "panel.qc": "Contrôle des résidus",
    "panel.aigcp": "Détection IA",

    # -- Contrôle des résidus et marqueurs à surveiller (qc.py, panel.py) ----
    "qc.title_residual": "Résidu élevé (au-delà de {limit} m)",
    "qc.title_few": "Moins de {n} photos pointées",
    "qc.item_residual": "{label} · {total} m · pire photo {px} px",
    "qc.item_residual_plain": "{label} · {total} m",
    "qc.item_few": "{label} · {n} photo(s)",
    "qc.none": "Aucun marqueur à surveiller",
    "qc.opened": "{label} : pire photo ouverte",

    # -- Détection IA : propositions de pointage (aigcp.py, panel.py) --------
    "aigcp.running": "IA ({kind}) : {n} photo(s) à tester pour {label}…",
    "aigcp.done": "IA : {n} proposition(s) sur {m} photo(s) pour {label}, à valider d'un clic",
    "aigcp.none": "IA : aucune cible retrouvée sur {m} photo(s) pour {label}",
    "aigcp.no_marker": "IA : choisis d'abord un marqueur",
    "aigcp.no_photos": "IA : aucune photo à tester pour {label}",
    "aigcp.busy": "IA : une détection est déjà en cours",
    "aigcp.failed": "IA : la détection a échoué (voir le journal)",
    "aigcp.chunk_changed": "IA : le chunk a changé, résultats ignorés",
    "aigcp.cleared": "IA : {n} proposition(s) effacée(s)",
    "aigcp.bad_type": "IA : type de cible inconnu ({kind})",
    "aigcp.no_python": "IA : Python d'AIGCP introuvable ({path})",
    "aigcp.no_weights": "IA : poids {kind} introuvables ({path})",
    "aigcp.no_runner": "IA : exécuteur introuvable ({path})",
    "aigcp.type_damier": "Damier",
    "aigcp.type_bombe": "Bombe",
    "aigcp.scope_marker": "Marqueurs sélectionnés",
    "aigcp.scope_all": "Tous les marqueurs",
    "aigcp.ignore_both": "Ignore vert + bleu",
    "aigcp.ignore_green": "Ignore vert",
    "aigcp.ignore_blue": "Ignore bleu",
    "aigcp.ignore_none": "Ignore rien",
    "aigcp.review": "Réviser",
    "aigcp.auto_review": "Ouvrir le Review panel automatiquement",
    "review.title": "Review panel",
    "review.view": "Affichage",
    "review.view_note": "Réglages pour mieux voir : les photos ne sont pas modifiées.",
    "review.no_numpy": "Réglages indisponibles (numpy absent).",
    "review.zoom": "Zoom",
    "review.exposure": "Exposition",
    "review.contrast": "Contraste",
    "review.saturation": "Saturation",
    "review.sharpness": "Netteté",
    "review.reset": "Réinitialiser",
    "review.status_kept": "gardée",
    "review.status_removed": "exclue",
    "review.status_refined": "affinée",
    "review.status_accepted": "validée",
    "review.refine_photo": "photo {n}/{total}",
    "review.refine_progress": "{done}/{total} pointées",
    "review.refine_next": "Suivant",
    "review.refine_validate_exit": "Valider et quitter",
    "review.hint": "Clique les vignettes à écarter, puis Exclure (ou clic droit). Maj + molette : zoom dans les vignettes. Ctrl + molette : taille des vignettes.",
    "review.exclude": "Exclure",
    "review.restore": "Remettre",
    "review.refine": "Affiner…",
    "review.accept": "Valider",
    "review.summary": "{kept} gardés · {removed} exclus · {refined} affinés · {accepted} validés",
    "review.no_crop": "Pas de vignette",
    "review.empty": "IA : aucune proposition à réviser",
    "review.refine_title": "Affiner le marqueur {label} sur {camera}",
    "review.refine_hint": "Clique (ou glisse) sur le centre de la cible. Molette : zoom. Bouton du milieu : déplacer. Entrée : photo suivante. La croix rouge est la proposition de l'IA.",
    "review.refine_validate": "Valider",
    "review.refine_cancel": "Annuler",
    "review.confirm_accept": "Poser {n} point(s) en pointage manuel ?",
    "review.accepted_status": "IA : {n} point(s) validé(s)",
    "aigcp.progress": "IA ({kind}) : {done}/{total} photo(s)…",
    "aigcp.run": "Lancer",
    "aigcp.clear": "Effacer les propositions",
    "aigcp.running_all": "IA ({kind}) : {n} photo(s) à tester sur {k} marqueur(s)…",
    "aigcp.done_all": "IA : {n} proposition(s) sur {k} marqueur(s), {m} photo(s) testées , {g} écartée(s) par le garde-fou",
    "aigcp.none_all": "IA : aucune cible retrouvée ({m} photo(s), {k} marqueur(s)) , {g} écartée(s) par le garde-fou",
    "aigcp.no_markers": "IA : aucun marqueur avec une position 3D",
    "aigcp.done_gate": "IA : {n} proposition(s) sur {m} photo(s) pour {label} , {g} écartée(s) par le garde-fou",
    "aigcp.none_gate": "IA : aucune proposition retenue pour {label} , {g} écartée(s) par le garde-fou",

    # -- Annuler (marking.py) ------------------------------------------------
    "undo.nothing": "Rien à annuler",
    "undo.failed": "Annulation impossible",
    "undo.done": "Annulé : {label}",

    # -- Rapport PDF : page 1 ----------------------------------------------
    "report.title": "Rapport de pointage GCP",
    "report.no_project": "Projet non enregistré",
    "report.band.project": "Projet",
    "report.band.chunk": "Chunk",
    "report.band.date": "Date",
    "report.band.metashape": "Metashape",
    "report.kpi.markers": "Marqueurs",
    "report.kpi.markers_sub": "dans le chunk",
    "report.kpi.pointed": "Pointés",
    "report.kpi.pointed_sub": "{pct} % des marqueurs",
    "report.kpi.projections": "Projections",
    "report.kpi.projections_sub": "{mean} par marqueur",
    "report.kpi.rms_xy": "RMS plan",
    "report.kpi.rms_z": "RMS Z",
    "report.kpi.rms_z_sub": "RMS total {value}",
    "report.kpi.worst": "Pire écart",
    "report.empty": "Pas de mesure disponible",
    "report.bar.title": "Erreur totale par marqueur",
    "report.bar.sub": "Écart total entre position estimée et référence",
    "report.bar.sub_top": "Les {shown} plus grands écarts sur {total} marqueurs",
    "report.bar.legend_total": "Erreur totale",
    "report.bar.legend_over": "Au-delà de 2 × RMS",
    "report.bar.legend_rms": "RMS {value}",
    "report.scatter.title": "Dispersion en plan",
    "report.scatter.sub": "Erreur X en abscisse, erreur Y en ordonnée",
    "report.scatter.legend_rms": "RMS plan {value}",
    "report.scatter.unit": "en mètres",
    "report.axis.title": "RMS par axe",
    "report.axis.xy": "Plan",
    "report.axis.total": "Total",
    "report.note.title": "Lecture",
    "report.note.none_1": "Aucune erreur disponible.",
    "report.note.none_2": (
        "Les marqueurs n'ont pas de référence ou ne sont pas encore triangulés."
    ),
    "report.note.errors": (
        "Erreurs en mètres : écart entre la position estimée de chaque "
        "marqueur et sa référence."
    ),
    "report.note.threshold": (
        "En orange : écart supérieur à 2 × RMS ({value} pour l'erreur totale)."
    ),
    "report.note.no_threshold": (
        "Moins de 3 marqueurs avec erreur : aucun seuil de mise en évidence."
    ),

    # -- Rapport PDF : tableau et histogramme -------------------------------
    "report.table.title": "Détail par marqueur",
    "report.table.sub": "{n} marqueurs",
    "report.table.sub_next": "suite",
    "report.table.sub_page": "suite, {page} sur {pages}",
    "report.table.id": "ID",
    "report.table.x": "Erreur X (m)",
    "report.table.y": "Erreur Y (m)",
    "report.table.z": "Erreur Z (m)",
    "report.table.total": "Total (m)",
    "report.table.proj": "Proj.",
    "report.table.legend": (
        "En orange : valeur au-delà de 2 × RMS de la colonne. "
        "Marqueurs sans projection : tiret."
    ),
    "report.table.legend_plain": "Marqueurs sans projection : tiret.",
    "report.hist.page_title": "Projections par marqueur",
    "report.hist.title": "Répartition des projections",
    "report.hist.sub": "Nombre de marqueurs selon leur nombre de projections",
    "report.hist.axis": "Projections par marqueur",
    "report.hist.mean": "Moyenne",
    "report.hist.median": "Médiane",
    "report.hist.min": "Minimum",
    "report.hist.max": "Maximum",
    "report.hist.low": "Moins de 3 projections",
    "report.hist.none": "Sans projection",

    # -- Rapport PDF : captures ---------------------------------------------
    "report.ai.title": "Photos pointées par l'IA",
    "report.ai.sub": "Review panel : croix rouge = point retenu, grande croix rouge = photo exclue, confiance sous chaque vignette",
    "report.ai.marker": "{label} · {n} photo(s)",
    "report.ai.cont": "(suite)",
    "report.ai.manual": "manuel",
    "report.ai.info_error": "Erreur 3D {total} m (X {x}, Y {y}, Z {z})",
    "report.ai.info_pointing": "Pointage : écart médian {median} px, maximum {max} px",
    "report.ai.info_conf": "Confiance IA : moyenne {mean}, minimum {min}",
    "report.ai.info_projections": "{n} projection(s)",
    "report.ai.info_gcp_yes": "Utilisé comme GCP",
    "report.ai.info_gcp_no": "Point de contrôle, non utilisé comme GCP",
    "report.ai.info_accuracy": "précision de référence {acc} m",
    "report.shots.title": "Captures",
    "report.shots.sub": "Vue photo, panneau FastGCP",
    "report.shots.sub_crops": (
        "Vue photo, vignettes des marqueurs les plus écartés "
        "(cercle vert : point posé, croix orange : position estimée), "
        "panneau FastGCP"
    ),
    "report.shots.photo": "Vue photo",
    "report.shots.photo_cam": "Vue photo {camera}",
    "report.shots.photo_marker": "{caption} · marqueur courant {marker}",
    "report.shots.fallback": "{camera} · marqueur {marker}",
    "report.shots.crop_error": "{label} · erreur {value}",
    "report.shots.crop_gap": "{camera} · écart image {gap} px",
    "report.shots.panel": "Panneau FastGCP",
    "report.footer.page": "Page {page} / {total}",
    "report.doc_title": "Rapport de pointage GCP {chunk}",

    # -- Export : fichiers, dialogues et messages ----------------------------
    "file.report": "Rapport_GCP",
    "file.table": "Tableau_GCP",
    "dlg.title": "Rapport GCP",
    "dlg.no_chunk": "Aucun chunk ouvert dans Metashape.",
    "dlg.no_markers": "Ce chunk ne contient aucun marqueur.",
    "dlg.save_pdf": "Exporter le rapport PDF",
    "dlg.save_csv": "Exporter le tableau en CSV",
    "dlg.filter_pdf": "Fichiers PDF (*.pdf)",
    "dlg.filter_csv": "Fichiers CSV (*.csv)",
    "dlg.saved": "Fichier enregistré :\n{path}",
    "dlg.open": "Ouvrir",
    "dlg.close": "Fermer",
    "dlg.pdf_done": "Rapport PDF exporté",
    "dlg.csv_done": "Tableau CSV exporté",
    "err.pdf": "Export PDF impossible",
    "err.csv": "Export CSV impossible",
    "err.denied": (
        "Écriture refusée. Le fichier est peut-être ouvert dans un autre "
        "programme ou le dossier est protégé."
    ),
    "err.no_qt": "Qt n'est pas initialisé",
    "err.cannot_write": "écriture impossible : {path}",

    # -- Export CSV ----------------------------------------------------------
    "csv.id": "ID",
    "csv.x": "Erreur X (m)",
    "csv.y": "Erreur Y (m)",
    "csv.z": "Erreur Z (m)",
    "csv.total": "Erreur totale (m)",
    "csv.projections": "Projections",
    "csv.ref_x": "X référence",
    "csv.ref_y": "Y référence",
    "csv.ref_z": "Z référence",

    # -- Parametres, suppression, credit -----------------------------------
    "review.slider_tip": "Double-clic : remettre à zéro",
    "review.busy_title": "Application en cours",
    "review.busy_note": "Un instant, ne touche pas à Metashape pendant ce temps.",
    "review.busy_validate": "Pose de {n} proposition(s) dans le projet…",
    "review.busy_refresh": "Mise à jour du tableau des marqueurs…",
    "review.busy_refine": "Pose de {n} correction(s) dans le projet…",
    "panel.remove_projections": "Supprimer les projections ({n})",
    "panel.remove_markers": "Supprimer les marqueurs ({n})",
    "panel.remove_title": "Suppression",
    "panel.remove_projections_confirm": "Supprimer toutes les projections de {n} marqueur(s) : {names} ? Les marqueurs restent dans le projet.",
    "panel.remove_markers_confirm": "Supprimer {n} marqueur(s) du projet : {names} ? FastGCP ne pourra pas les rétablir.",
    "panel.removed_projections": "{n} projection(s) supprimée(s) sur {k} marqueur(s)",
    "panel.removed_markers": "{n} marqueur(s) supprimé(s)",
    "panel.credit": "Outil créé par {site}",
    "panel.github_tip": "Code source sur GitHub",
    "settings.title": "Paramètres",
    "settings.general": "Général",
    "settings.ai_models": "Modèles IA",
    "settings.use_refine": "Utiliser mes affinages manuels pour construire le jeu d'entraînement",
    "settings.storage": "Dossier de stockage",
    "settings.browse": "Parcourir…",
    "settings.storage_pick": "Dossier de stockage (dataset, models, training dedans)",
    "settings.data_counts": "{p} point(s) corrigé(s) ou validé(s) et {r} proposition(s) rejetée(s) enregistré(s) (damier : {damier}, bombe : {bombe})",
    "settings.data_none": "Aucune annotation pour l'instant : corrige ou exclus des propositions dans le Review panel.",
    "settings.model": "Modèle IA",
    "settings.add": "Ajouter…",
    "settings.add_soon": "Bientôt disponible",
    "settings.manage": "Gérer…",
    "settings.model_base": "Modèle d'origine, toujours disponible.",
    "settings.model_local": "Modèle local entraîné sur tes données ({types}).",
    "settings.refine_model": "Affiner mon modèle avec mes données",
    "settings.revert": "Revenir à {name}",
    "settings.reverted": "Modèle d'origine rétabli.",
    "settings.train_title": "Affiner le modèle IA",
    "settings.train_confirm": "Tu vas relancer l'entraînement du modèle de détection sur tes propres annotations ({p} point(s) corrigé(s) ou validé(s), {r} rejeté(s)).\n\nLe modèle peut mieux fonctionner sur tes cibles, mais peut-être moins bien sur d'autres. Ce que tu as classé en spray sera appris comme du spray, ce que tu as classé en damier comme du damier : si tes annotations contiennent des erreurs, le résultat sera moins bon.\n\nL'entraînement utilise la carte graphique de façon intensive pendant plusieurs minutes. Ne touche pas à l'ordinateur pendant ce temps.\n\nTon modèle d'origine n'est jamais modifié : tu peux revenir en arrière à tout moment avec le bouton « Revenir à ». Lancer l'entraînement ?",
    "settings.train_cancel": "Annuler",
    "settings.train_cancelling": "Annulation en cours…",
    "settings.training": "Entraînement en cours…",
    "settings.train_done": "Entraînement terminé. Le nouveau modèle « {name} » est maintenant sélectionné : tu peux le tester sur ta prochaine détection. Le bouton « Revenir à » rétablit le modèle d'origine.",
    "settings.train_failed": "Entraînement impossible : {message}",
    "settings.train_cancelled": "Entraînement annulé : rien n'a été modifié.",
    "settings.train_busy_detect": "Une détection est en cours : attends qu'elle se termine.",
    "settings.train_running_close": "Un entraînement est en cours : annule-le avant de fermer cette fenêtre.",
    "settings.manage_title": "Gérer les modèles",
    "settings.delete": "Supprimer",
    "settings.delete_confirm": "Supprimer le modèle {name} ? Cette action est définitive.",
    "settings.base_locked": "Le modèle d'origine ne peut pas être supprimé. L'étoile marque le modèle utilisé.",
    "settings.close": "Fermer",

    # -- Rapport : photos exclues ------------------------------------------
    "report.ai.excluded": "exclue",
    "report.ai.info_excluded": "{n} photo(s) exclue(s)",

    # -- Reentrainement : erreurs ------------------------------------------
    "settings.err_busy": "Un autre entraînement est déjà en cours.",
    "settings.err_no_python": "L'environnement Python de l'IA est introuvable : {detail}",
    "settings.err_no_script": "Le script d'entraînement est introuvable : {detail}",
    "settings.err_no_data": "Pas assez d'annotations pour entraîner : il faut au moins {n} points corrigés ou validés pour un type de cible.",
    "settings.train_warning": "Attention : sur tes données de contrôle, le nouveau modèle fait moins bien que le modèle d'origine. Tu peux revenir au modèle d'origine.",

    # -- Boites de dialogue ------------------------------------------------
    "dialogs.yes": "Oui",
    "dialogs.no": "Non",
    "dialogs.ok": "OK",
    "panel.busy_projections": "Suppression des projections de {n} marqueur(s)…",

    # -- Retablir, bandeau -------------------------------------------------
    "redo.nothing": "Rien à rétablir",
    "redo.failed": "Rétablissement impossible",
    "redo.done": "Rétabli : {label}",
    "panel.ad": "Complex flights made easy",

    # -- Dossier de stockage -----------------------------------------------
    "settings.open_folder": "Ouvrir",
    "settings.storage_content": "Contient : annotations (dataset) : {a} ligne(s) · modèles locaux (models) : {m} · détections gardées (aigcp) : {d} · essais d'entraînement (training) : {t}",
    "settings.copy_confirm": "Ce dossier ne contient encore ni annotation ni modèle. Copier dans ce dossier les {a} annotation(s) et les {m} modèle(s) local(aux) du dossier actuel ? Le dossier actuel n'est pas modifié.",
    "settings.copy_failed": "La copie a échoué : {message}",
}
