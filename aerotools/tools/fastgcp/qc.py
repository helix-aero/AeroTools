"""Progression du pointage et marqueurs a surveiller (sans Qt, sans Metashape).

Travaille sur les lignes du tableau du panneau : des dicts avec `label`, `proj`
(nombre de projections) et `x`, `y`, `z` (erreurs en metres, ou None).

Un marqueur est « pointe » quand il a au moins MIN_PHOTOS projections : c'est le
minimum pour une triangulation solide. A surveiller :
  - `few`      : moins de MIN_PHOTOS projections ;
  - `residual` : erreur totale au-dessus du seuil. Le seuil est le plus grand de
                 RESIDUAL_FLOOR_M et RESIDUAL_FACTOR fois la mediane basse des
                 erreurs du chunk, pour qu'un chunk globalement bruite ne signale
                 pas tout.
"""

import math

MIN_PHOTOS = 3
RESIDUAL_FLOOR_M = 0.03
RESIDUAL_FACTOR = 3.0


def total_error(row):
    """Erreur totale en metres, ou None si une composante manque."""
    try:
        values = [row.get(key) for key in ("x", "y", "z")]
        if any(v is None for v in values):
            return None
        total = math.sqrt(sum(float(v) ** 2 for v in values))
    except (TypeError, ValueError):
        return None
    return total if math.isfinite(total) else None


def _low_median(values):
    """Mediane basse : avec peu de marqueurs, un gros ecart ne doit pas relever
    le seuil qui le signale (la mediane moyenne de 2 valeurs, elle, le ferait)."""
    values = sorted(values)
    return values[(len(values) - 1) // 2] if values else 0.0


def residual_limit(rows):
    """Seuil d'erreur totale (m) au-dela duquel un marqueur est signale."""
    totals = [t for t in (total_error(row) for row in rows) if t is not None]
    return max(RESIDUAL_FLOOR_M, RESIDUAL_FACTOR * _low_median(totals))


def progress(rows, min_photos=MIN_PHOTOS):
    """(marqueurs pointes, marqueurs au total)."""
    rows = list(rows)
    done = sum(1 for row in rows if int(row.get("proj") or 0) >= min_photos)
    return done, len(rows)


def is_few(row, min_photos=MIN_PHOTOS):
    return int(row.get("proj") or 0) < min_photos


def watchlist(rows, min_photos=MIN_PHOTOS):
    """Marqueurs a surveiller.

    Renvoie {"limit": seuil, "residual": [...], "few": [...]}. Chaque element
    est {"label", "proj", "total"} ; `residual` va du plus grand ecart au plus
    petit, `few` du moins pointe au plus pointe (a egalite, ordre du projet).
    """
    rows = list(rows)
    limit = residual_limit(rows)
    residual = []
    few = []
    for row in rows:
        total = total_error(row)
        item = {"label": row.get("label", ""), "proj": int(row.get("proj") or 0),
                "total": total}
        if total is not None and total > limit:
            residual.append(item)
        if is_few(row, min_photos):
            few.append(item)
    residual.sort(key=lambda item: -item["total"])
    few.sort(key=lambda item: item["proj"])
    return {"limit": limit, "residual": residual, "few": few}
