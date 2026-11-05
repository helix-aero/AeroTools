"""Quels marqueurs prendre comme points de controle ? (sans Qt, sans Metashape)

Idee : la position estimee d'un marqueur (triangulee dans les photos) ne depend que des cameras et de
ses pointages. Choisir quels marqueurs servent de controle ne change que la transformation vers les
coordonnees terrain. On peut donc, sans relancer aucun calcul de Metashape, essayer des milliers de
jeux de marqueurs : pour chacun on ajuste la transformation sur le jeu, puis on mesure l'ecart sur
les marqueurs laisses de cote (les points de verification).

Deux modeles de transformation :
  similarity  7 parametres (echelle, rotation, translation) : le projet est georeference par les
              marqueurs seuls. Il faut au moins 3 marqueurs qui ne sont pas alignes ;
  shift       3 parametres (translation) : les cameras ont deja une bonne position (PPK / RTK), les
              marqueurs ne corrigent qu'un decalage constant. Un seul marqueur suffit.

Ce que ce calcul ne voit pas : « Optimize cameras » corrige aussi les deformations souples (focale,
distorsion). L'erreur reelle apres optimisation peut donc etre meilleure ; ce calcul dit quels
marqueurs portent l'information et lesquels sont suspects, pas le chiffre final.

Un point est un dict : `label`, `est` et `ref` (x, y, z en metres, meme repere local est/nord/haut :
position estimee et position de reference), `n` (nombre de photos pointees).
"""

import itertools
import math

SIMILARITY = "similarity"
SHIFT = "shift"
MODELS = (SIMILARITY, SHIFT)

MIN_PHOTOS = 3
MIN_CONTROL = {SIMILARITY: 3, SHIFT: 1}
MAX_CONTROL = {SIMILARITY: 6, SHIFT: 4}          # tailles essayees
REDUNDANT = {SIMILARITY: 4, SHIFT: 2}            # taille conseillee au minimum : un point de trop pour voir un mauvais pointage
BUDGET = 40000                                   # ajustements au plus par recherche
TOLERANCE_REL = 0.15                             # « presque aussi bon que le meilleur » : +15 %...
TOLERANCE_ABS = 0.005                            # ... ou +5 mm (precision d'un marqueur)
SUSPECT_FLOOR = 0.03                             # un marqueur est suspect au-dela de 3 cm (et 4 x la mediane)
SUSPECT_FACTOR = 4.0
COLLINEAR = 0.05
SPREAD_GOOD, SPREAD_MID = 0.6, 0.3


# --------------------------------------------------------------------------
# Algebre minimale (pas de numpy dans le Python de Metashape)
# --------------------------------------------------------------------------

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _norm(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _mean(points):
    n = float(len(points))
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n, sum(p[2] for p in points) / n)


def _eigen_top(matrix):
    """Vecteur propre de la plus grande valeur propre d'une matrice 4 x 4 symetrique (Jacobi)."""
    n = 4
    a = [list(row) for row in matrix]
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _sweep in range(60):
        off = sum(a[i][j] * a[i][j] for i in range(n) for j in range(i + 1, n))
        diag = sum(a[i][i] * a[i][i] for i in range(n))
        if off <= 1e-24 * (diag + off) or off == 0.0:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                if a[p][q] == 0.0:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = (1.0 if theta >= 0.0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
                for k in range(n):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p] = c * vkp - s * vkq
                    v[k][q] = s * vkp + c * vkq
    best = max(range(n), key=lambda i: a[i][i])
    return [v[k][best] for k in range(n)]


def _rotation(q):
    w, x, y, z = q
    return ((w * w + x * x - y * y - z * z, 2 * (x * y - w * z), 2 * (x * z + w * y)),
            (2 * (x * y + w * z), w * w - x * x + y * y - z * z, 2 * (y * z - w * x)),
            (2 * (x * z - w * y), 2 * (y * z + w * x), w * w - x * x - y * y + z * z))


def _apply_rotation(r, p):
    return (r[0][0] * p[0] + r[0][1] * p[1] + r[0][2] * p[2],
            r[1][0] * p[0] + r[1][1] * p[1] + r[1][2] * p[2],
            r[2][0] * p[0] + r[2][1] * p[1] + r[2][2] * p[2])


def _is_collinear(centered):
    """Vrai si les points (centres) sont (quasi) alignes : une similitude n'y est pas definie."""
    far = max(centered, key=_norm)
    length = _norm(far)
    if length < 1e-9:
        return True
    axis = (far[0] / length, far[1] / length, far[2] / length)
    height = 0.0
    for p in centered:
        off = _norm(_cross(p, axis))
        if off > height:
            height = off
    return height < COLLINEAR * length


# --------------------------------------------------------------------------
# Transformations
# --------------------------------------------------------------------------

def fit_shift(points):
    """Translation moyenne qui amene `est` sur `ref`. Renvoie une fonction, ou None sans point."""
    if not points:
        return None
    n = float(len(points))
    t = (sum(p["ref"][0] - p["est"][0] for p in points) / n,
         sum(p["ref"][1] - p["est"][1] for p in points) / n,
         sum(p["ref"][2] - p["est"][2] for p in points) / n)
    return lambda e: (e[0] + t[0], e[1] + t[1], e[2] + t[2])


def fit_similarity(points):
    """Similitude (echelle, rotation, translation) de `est` vers `ref` (methode de Horn).

    Renvoie une fonction, ou None si moins de 3 points ou points alignes."""
    if len(points) < 3:
        return None
    pc = _mean([p["est"] for p in points])
    qc = _mean([p["ref"] for p in points])
    centered_p = [_sub(p["est"], pc) for p in points]
    centered_q = [_sub(p["ref"], qc) for p in points]
    if _is_collinear(centered_p) or _is_collinear(centered_q):
        return None
    sxx = sxy = sxz = syx = syy = syz = szx = szy = szz = 0.0
    for a, b in zip(centered_p, centered_q):
        sxx += a[0] * b[0]
        sxy += a[0] * b[1]
        sxz += a[0] * b[2]
        syx += a[1] * b[0]
        syy += a[1] * b[1]
        syz += a[1] * b[2]
        szx += a[2] * b[0]
        szy += a[2] * b[1]
        szz += a[2] * b[2]
    n_matrix = (
        (sxx + syy + szz, syz - szy, szx - sxz, sxy - syx),
        (syz - szy, sxx - syy - szz, sxy + syx, szx + sxz),
        (szx - sxz, sxy + syx, -sxx + syy - szz, syz + szy),
        (sxy - syx, szx + sxz, syz + szy, -sxx - syy + szz),
    )
    q = _eigen_top(n_matrix)
    length = math.sqrt(sum(c * c for c in q))
    if length == 0.0:
        return None
    rot = _rotation([c / length for c in q])
    num = sum(sum(rb * qb for rb, qb in zip(_apply_rotation(rot, a), b)) for a, b in zip(centered_p, centered_q))
    den = sum(a[0] * a[0] + a[1] * a[1] + a[2] * a[2] for a in centered_p)
    if den <= 0.0:
        return None
    scale = num / den
    moved = _apply_rotation(rot, pc)
    t = (qc[0] - scale * moved[0], qc[1] - scale * moved[1], qc[2] - scale * moved[2])

    def transform(e):
        r = _apply_rotation(rot, e)
        return (scale * r[0] + t[0], scale * r[1] + t[1], scale * r[2] + t[2])

    transform.scale = scale
    return transform


def fit(model, points):
    return fit_similarity(points) if model == SIMILARITY else fit_shift(points)


def errors(transform, points):
    """Ecarts (dx, dy, dz) = position transformee - reference, pour chaque point."""
    out = []
    for p in points:
        moved = transform(p["est"])
        out.append((moved[0] - p["ref"][0], moved[1] - p["ref"][1], moved[2] - p["ref"][2]))
    return out


def stats(errs):
    """Racine de la moyenne des carres (RMSE) par axe, en plan, en 3D, et ecart maximal. None si vide."""
    n = len(errs)
    if not n:
        return None
    sx = sum(e[0] * e[0] for e in errs) / n
    sy = sum(e[1] * e[1] for e in errs) / n
    sz = sum(e[2] * e[2] for e in errs) / n
    return {"n": n, "x": math.sqrt(sx), "y": math.sqrt(sy), "z": math.sqrt(sz),
            "xy": math.sqrt(sx + sy), "total": math.sqrt(sx + sy + sz), "max": max(_norm(e) for e in errs)}


def spread(chosen, pool):
    """0 a 1 : etalement du jeu choisi par rapport a l'ensemble (petit axe de l'ellipse en plan)."""
    def minor(points):
        xs = [p["est"][0] for p in points]
        ys = [p["est"][1] for p in points]
        n = float(len(points))
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs) / n
        syy = sum((y - my) ** 2 for y in ys) / n
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
        half = (sxx + syy) / 2.0
        gap = math.sqrt(max(0.0, ((sxx - syy) / 2.0) ** 2 + sxy * sxy))
        return math.sqrt(max(0.0, half - gap))
    whole = minor(pool)
    if whole <= 1e-9 or len(chosen) < 3:
        return 1.0
    return max(0.0, min(1.0, minor(chosen) / whole))


def spread_label(value):
    return "good" if value >= SPREAD_GOOD else "mid" if value >= SPREAD_MID else "low"


# --------------------------------------------------------------------------
# Recherche
# --------------------------------------------------------------------------

def _score(model, pool, chosen):
    """RMSE 3D sur les points laisses de cote quand le jeu `chosen` (indices) sert au calage ; inf si impossible."""
    control = [pool[i] for i in chosen]
    transform = fit(model, control)
    if transform is None:
        return float("inf")
    chosen_set = set(chosen)
    rest = [pool[i] for i in range(len(pool)) if i not in chosen_set]
    if not rest:
        return float("inf")
    return stats(errors(transform, rest))["total"]


def _exhaustive(model, pool, k):
    best, ties = float("inf"), []
    for combo in itertools.combinations(range(len(pool)), k):
        score = _score(model, pool, combo)
        if score < best - 1e-4:
            best, ties = score, [combo]
        elif score <= best + 1e-4:
            ties.append(combo)
    if not ties:
        return None, float("inf")
    ties.sort(key=lambda combo: -spread([pool[i] for i in combo], pool))
    return ties[0], _score(model, pool, ties[0])


def _wide_triangle(pool):
    """Trois indices bien ecartes (le plus eloigne du centre, puis du premier, puis le plus loin de leur droite)."""
    center = _mean([p["est"] for p in pool])
    first = max(range(len(pool)), key=lambda i: _norm(_sub(pool[i]["est"], center)))
    second = max((i for i in range(len(pool)) if i != first),
                 key=lambda i: _norm(_sub(pool[i]["est"], pool[first]["est"])))
    base = _sub(pool[second]["est"], pool[first]["est"])
    third = max((i for i in range(len(pool)) if i not in (first, second)),
                key=lambda i: _norm(_cross(base, _sub(pool[i]["est"], pool[first]["est"]))))
    return [first, second, third]


def _greedy(model, pool, k, seed):
    """Jeu de taille k : on part de `seed`, on ajoute le meilleur point, puis un passage d'echanges."""
    chosen = list(seed)
    if model == SIMILARITY and len(chosen) < MIN_CONTROL[SIMILARITY]:
        chosen = _wide_triangle(pool)
    while len(chosen) < k:
        best, pick = float("inf"), None
        for i in range(len(pool)):
            if i in chosen:
                continue
            score = _score(model, pool, chosen + [i])
            if score < best:
                best, pick = score, i
        if pick is None:
            return None, float("inf")
        chosen.append(pick)
    best = _score(model, pool, chosen)
    improved = True
    while improved:
        improved = False
        for position in range(len(chosen)):
            for i in range(len(pool)):
                if i in chosen:
                    continue
                trial = chosen[:position] + [i] + chosen[position + 1:]
                score = _score(model, pool, trial)
                if score < best - 1e-6:
                    chosen, best, improved = trial, score, True
    return tuple(sorted(chosen)), best


def _loo(model, control):
    """RMSE 3D, sur chaque point du jeu, quand on le retire du calage : mesure la redondance du jeu."""
    if len(control) <= MIN_CONTROL[model]:
        return None
    errs = []
    for i, held in enumerate(control):
        transform = fit(model, control[:i] + control[i + 1:])
        if transform is None:
            return None
        errs.extend(errors(transform, [held]))
    return stats(errs)["total"]


def suspects(model, pool):
    """Marqueurs dont le pointage ou les coordonnees sont suspects, du pire au moins pire.

    On retire le pire (ecart quand on l'enleve du calage) tant qu'il depasse
    max(3 cm, 4 x la mediane) et qu'il reste de quoi caler. Renvoie ([(label, ecart)], points restants)."""
    remaining = list(pool)
    found = []
    floor = MIN_CONTROL[model] + 2
    while len(remaining) > floor:
        loo = []
        for i, held in enumerate(remaining):
            transform = fit(model, remaining[:i] + remaining[i + 1:])
            if transform is None:
                loo = []
                break
            loo.append(_norm(errors(transform, [held])[0]))
        if not loo:
            break
        median = sorted(loo)[(len(loo) - 1) // 2]
        worst = max(range(len(loo)), key=lambda i: loo[i])
        if loo[worst] <= max(SUSPECT_FLOOR, SUSPECT_FACTOR * median):
            break
        found.append((remaining[worst]["label"], loo[worst]))
        del remaining[worst]
    return found, remaining


def current_stats(points, enabled):
    """Ecarts tels que Metashape les affiche : (controle, verification), chacun un stats() ou None."""
    control = [p for p in points if enabled.get(p["label"])]
    check = [p for p in points if not enabled.get(p["label"], False)]
    zero = lambda p: (p["est"][0] - p["ref"][0], p["est"][1] - p["ref"][1], p["est"][2] - p["ref"][2])
    return stats([zero(p) for p in control]), stats([zero(p) for p in check])


def analyze(points, enabled=None, model=SIMILARITY, min_photos=MIN_PHOTOS, budget=BUDGET):
    """Courbe « nombre de marqueurs de controle -> erreur sur les autres », avec le jeu conseille.

    Renvoie {"model", "count", "usable", "few": [labels], "suspects": [{"label", "error"}],
    "curve": [{"k", "labels", "rmse", "x", "y", "z", "xy", "max", "checks", "loo", "spread"}],
    "recommended": indice dans curve ou None, "current": (controle, verification), "reason": ""}.
    """
    enabled = enabled or {}
    result = {"model": model, "count": len(points), "usable": 0, "few": [], "suspects": [], "curve": [],
              "recommended": None, "current": current_stats(points, enabled), "reason": ""}
    pool = []
    for p in points:
        if int(p.get("n") or 0) < min_photos:
            result["few"].append(p["label"])
        else:
            pool.append(p)
    result["usable"] = len(pool)
    need = MIN_CONTROL[model] + 1
    if len(pool) < need:
        result["reason"] = "too_few"
        return result

    found, pool = suspects(model, pool)
    result["suspects"] = [{"label": label, "error": error} for label, error in found]
    if len(pool) < need:
        result["reason"] = "too_few"
        return result

    left = budget
    previous = ()
    top = min(MAX_CONTROL[model], len(pool) - 1)
    for k in range(MIN_CONTROL[model], top + 1):
        combos = math.comb(len(pool), k) if hasattr(math, "comb") else _comb(len(pool), k)
        if combos <= left:
            chosen, score = _exhaustive(model, pool, k)
            left -= combos
        else:
            seed = previous if len(previous) == k - 1 else ()
            chosen, score = _greedy(model, pool, k, seed)
            left = max(0, left - len(pool) * k * 3)
        if chosen is None or score == float("inf"):
            continue
        previous = chosen
        control = [pool[i] for i in chosen]
        check = [pool[i] for i in range(len(pool)) if i not in chosen]
        transform = fit(model, control)
        measured = stats(errors(transform, check))
        result["curve"].append({
            "k": k, "labels": [p["label"] for p in control], "rmse": measured["total"], "x": measured["x"],
            "y": measured["y"], "z": measured["z"], "xy": measured["xy"], "max": measured["max"],
            "checks": measured["n"], "loo": _loo(model, control), "spread": spread(control, pool)})

    curve = result["curve"]
    if curve:
        best = min(item["rmse"] for item in curve)
        limit = best * (1.0 + TOLERANCE_REL) + TOLERANCE_ABS
        wanted = min(REDUNDANT[model], curve[-1]["k"])
        for index, item in enumerate(curve):
            if item["k"] >= wanted and item["rmse"] <= limit:
                result["recommended"] = index
                break
        if result["recommended"] is None:
            result["recommended"] = len(curve) - 1
    return result


def _comb(n, k):
    value = 1
    for i in range(1, k + 1):
        value = value * (n - k + i) // i
    return value
