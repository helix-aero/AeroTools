"""Fast Export : ce qui se passe quand on clique « Export » (sans Qt : la fenetre lui donne ses rappels).

Pour chaque fichier de la liste (plan.Job) :
  1. Metashape ecrit un GeoTIFF (exportRaster), en un seul morceau ou dalle par dalle ;
  2. si le format voulu est JPEG 2000 ou ECW, GDAL ou Global Mapper convertit ce GeoTIFF ;
  3. en mode CAD, un DXF (TOPO.dxf) pose toutes les images au bon endroit, puis AutoCAD en fait un DWG et verifie que
     les images se rechargent.

Rien n'est suppose de la machine : les outils viennent de `ctx.tools` (voir external.find_*). Une dalle sans donnee
n'ecrit aucun fichier : elle est simplement sautee.
"""

import os
import shutil
import tempfile

from . import dxfout, external, geotiff, naming, plan, tiling

TMP_NAME = ".fastexport_tmp"


class Failure(Exception):
    """Echec lisible : le message s'affiche tel quel."""


class Context(object):
    """Tout ce dont un export a besoin ; la fenetre le remplit, les tests le remplacent par des faux."""

    def __init__(self, chunk, info, folder, site, date, preset, export, tools=None, crs=None, tile_m=None, cad=None,
                 lang="fr", date_style="short", snap=None, transform=None, tick=None):
        self.chunk, self.info, self.folder = chunk, info, folder
        self.site, self.date, self.preset, self.cad = site, date, preset, cad or {}
        self.export = export                              # msapi.export_raster ou un faux
        self.tools = tools or {}                          # {"gm": chemin, "acad": chemin accoreconsole, "gdal": dossier}
        self.crs, self.tile_m = crs, tile_m
        self.lang, self.date_style = lang, date_style
        self.snap, self.transform = snap, transform
        self.tick = tick or (lambda: None)


class Result(object):
    def __init__(self):
        self.files = []                                   # (chemin, octets) de tout ce qui a ete ecrit
        self.warnings = []
        self.dxf = self.dwg = None
        self.check = None                                 # {"images": n, "failed": [...], "valid": bool} d'AutoCAD
        self.tiles = 0


# --------------------------------------------------------------------------------------------------
# Un fichier
# --------------------------------------------------------------------------------------------------

def encoder(ctx):
    """Qui ecrit le JPEG 2000 : "gdal", "gm", ou "metashape" (son JPEG 2000 maison, plus lourd, sans outil externe)."""
    if ctx.tools.get("gdal"):
        return "gdal"
    if ctx.tools.get("gm"):
        return "gm"
    return "metashape"


def _convert(ctx, post, src, dst):
    """GeoTIFF -> JPEG 2000 / ECW. Leve Failure."""
    if post == "ecw":
        if not ctx.tools.get("gm"):
            raise Failure("ECW : Global Mapper est introuvable")
        ok, message = external.to_ecw(ctx.tools["gm"], src, dst, tick=ctx.tick)
    else:
        which = encoder(ctx)
        if which == "gdal":
            ok, message = external.to_jp2_gdal(ctx.tools["gdal"], src, dst, tick=ctx.tick)
            if not ok and ctx.tools.get("gm"):
                ok, message = external.to_jp2_gm(ctx.tools["gm"], src, dst, tick=ctx.tick)
        elif which == "gm":
            ok, message = external.to_jp2_gm(ctx.tools["gm"], src, dst, tick=ctx.tick)
        else:
            raise Failure("JPEG 2000 : aucun encodeur externe")
    for path in (src, dst):                               # GDAL laisse parfois un .aux.xml (statistiques) : inutile ici
        _drop_sidecar(path)
    if not ok:
        raise Failure(message)


def _drop_sidecar(path):
    try:
        if os.path.isfile(path + ".aux.xml"):
            os.remove(path + ".aux.xml")
    except OSError:
        pass


def pixel_of(ctx, job):
    """Taille de pixel utilisee quand l'export impose une grille (changement de SCR, dalles). Le pixel natif de Metashape
    (1.0008 cm) est alors arrondi au dixieme de millimetre (1 cm) : sans cela les dalles ne tombent pas sur des pixels entiers."""
    return round(float(job.pixel_m), 4) if (ctx.crs is not None or ctx.tile_m) and job.native else float(job.pixel_m)


def _grid(ctx, job):
    """(pixel, emprise) a donner a Metashape pour ce fichier ; (None, None) = sa grille native."""
    kind = job.kind
    native = job.native and ctx.crs is None and not ctx.tile_m
    box = ctx.info.get("box_" + kind)
    if native or not box:
        return None, None
    if ctx.crs is not None:
        moved = ctx.transform(box, ctx.info.get("crs_object"), ctx.crs)
        if moved is None:
            raise Failure("Impossible de convertir l'emprise vers le SCR choisi")
        box = moved
    pixel = pixel_of(ctx, job)
    return pixel, ctx.snap(box, pixel)


def _tiles(ctx, job, box):
    """Liste de (jeton de nom ou None, emprise flottante) : une seule dalle si le decoupage n'est pas demande."""
    if not ctx.tile_m or not box:
        return [(None, box)]
    pixel = pixel_of(ctx, job)
    size, _exact = tiling.snap_tile_size(ctx.tile_m, [pixel])
    tiles = tiling.tile_grid(box, size, pixel_sizes=(pixel,), edge="crop")
    rows = max(t.row for t in tiles)
    cols = max(t.col for t in tiles)
    return [(tiling.tile_name(t, "rc", rows, cols), tuple(float(v) for v in t.bounds)) for t in tiles]


def _named(ctx, job, kind, ext, token):
    return naming.build_name(ctx.site, ctx.date, job.pixel_m, kind, ext, lang=ctx.lang, version=job.version, tile=token,
                             date_style=ctx.date_style)


def _big(ctx, job):
    found = ctx.info.get(job.kind)
    if not found:
        return False
    return plan.needs_bigtiff(found[1], found[2], 4 if job.kind == plan.ORTHO else 1, 1 if job.kind == plan.ORTHO else 4)


def _record(ctx, raw, layer, final):
    """Placement d'une image dans le DXF, lu dans le GeoTIFF brut."""
    info = geotiff.read_geotiff(raw)
    if info is None:
        raise Failure("Georeferencement illisible : %s" % os.path.basename(raw))
    left, bottom, right, top = geotiff.extent(info)
    return {"layer": layer, "path": final, "size_px": (info["width"], info["height"]), "insert": (left, bottom),
            "pixel": (info["pixel"][0], info["pixel"][1])}


def run_job(ctx, job, work, report, result, images):
    """Ecrit un fichier de la liste (ou toutes ses dalles). `report(fraction)` : avancement de ce fichier."""
    spec = job.spec
    post = spec.get("post")
    is_cad = ctx.preset == plan.CAD
    pixel, box = _grid(ctx, job)
    tiles = _tiles(ctx, job, box)
    folder = os.path.dirname(job.path)
    os.makedirs(folder, exist_ok=True)
    extra = job.extra or {}
    if is_cad and job.kind == plan.DEM:
        os.makedirs(os.path.dirname(extra["view_path"]), exist_ok=True)
    date_text = naming.format_date(ctx.date, ctx.date_style)
    pixel_text = "%g" % round(float(job.pixel_m), 4)
    for index, (token, tile_box) in enumerate(tiles):
        def sub(percent, index=index):
            report((index + max(0.0, min(100.0, float(percent))) / 100.0) / len(tiles))

        direct_ext = spec["ext"] if not post else None
        raw_spec = dict(spec, ext="tif") if post else dict(spec)
        if is_cad and job.kind == plan.DEM:
            final = os.path.join(folder, _named(ctx, job, job.kind, "tif", token))
            raw = final
        elif post:
            final = os.path.join(folder, _named(ctx, job, job.kind, spec["ext"], token))
            raw = os.path.join(work, os.path.splitext(os.path.basename(final))[0] + ".tif")
        else:
            final = os.path.join(folder, _named(ctx, job, job.kind, direct_ext, token))
            raw = final
        metashape_jp2 = post in ("jp2", "cad") and spec["ext"] == "jp2" and encoder(ctx) == "metashape"
        if metashape_jp2:
            raw_spec = dict(spec, image="jp2", ext="jp2")
            raw = final
        ok, error = ctx.export(ctx.chunk, job.kind, raw_spec, raw, pixel, tile_box, big=_big(ctx, job), progress=sub,
                               crs=ctx.crs)
        if not ok:
            raise Failure(error or "Metashape n'a pas ecrit le fichier")
        if not os.path.isfile(raw):
            if len(tiles) > 1:
                continue                                  # dalle sans donnee : rien a ecrire
            raise Failure("Metashape n'a pas ecrit le fichier")
        result.tiles += 1
        if post and not metashape_jp2:
            _convert(ctx, spec["post"] if post != "cad" else spec["ext"], raw, final)
        result.files.append((final, os.path.getsize(final)))
        for name in plan.aux_names(os.path.basename(final), spec):
            side = os.path.join(folder, name)
            if os.path.isfile(side):
                result.files.append((side, os.path.getsize(side)))
        if is_cad and job.kind == plan.ORTHO:
            source = raw if raw != final else None
            if source is None:
                raise Failure("Image sans GeoTIFF source : placement impossible")
            images.append(_record(ctx, source, dxfout.layer_name(date_text, pixel_text, "ortho"), final))
        if is_cad and job.kind == plan.DEM:
            _dem_image(ctx, job, raw, token, work, date_text, pixel_text, result, images)
        if post and raw != final and os.path.isfile(raw):
            os.remove(raw)


def _dem_image(ctx, job, dem_tif, token, work, date_text, pixel_text, result, images):
    """Image de visualisation du MNT (relief colore) pour le DXF."""
    extra = job.extra
    if not ctx.tools.get("gdal"):
        if "dem_image" not in result.warnings:
            result.warnings.append("dem_image")
        return
    view_tif = os.path.join(work, os.path.splitext(os.path.basename(dem_tif))[0] + "_view.tif")
    ok, message = external.dem_view(ctx.tools["gdal"], dem_tif, view_tif, tick=ctx.tick)
    _drop_sidecar(dem_tif)
    if not ok:
        raise Failure(message)
    view_final = os.path.join(os.path.dirname(extra["view_path"]), _named(ctx, job, plan.DEM, extra["view_ext"], token))
    # le relief colore est un GeoTIFF RVB : il suit le meme chemin que l'ortho (GDAL en priorite ou Global Mapper)
    _convert(ctx, extra["view_ext"], view_tif, view_final)
    result.files.append((view_final, os.path.getsize(view_final)))
    images.append(_record(ctx, view_tif, dxfout.layer_name(date_text, pixel_text, "dem"), view_final))
    os.remove(view_tif)


# --------------------------------------------------------------------------------------------------
# L'export entier
# --------------------------------------------------------------------------------------------------

def run(ctx, jobs, progress):
    """Ecrit tout. `progress(fraction 0..1, texte)`. Renvoie un Result ; leve Failure."""
    result = Result()
    if not jobs:
        return result
    os.makedirs(ctx.folder, exist_ok=True)
    work = tempfile.mkdtemp(prefix=TMP_NAME, dir=ctx.folder)
    images = []
    total = len(jobs)
    try:
        for index, job in enumerate(jobs):
            progress(index / float(total), job.name)
            run_job(ctx, job, work, lambda part, index=index: progress((index + part) / float(total), job.name), result,
                    images)
        progress(1.0, "")
        if ctx.preset == plan.CAD:
            _write_cad(ctx, jobs, images, result)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return result


def _write_cad(ctx, jobs, images, result):
    """TOPO.dxf (et TOPO.dwg) : toutes les images a leur place, chemins relatifs."""
    if not images:
        raise Failure("Aucune image a placer dans le dessin")
    version = jobs[0].version
    dxf = os.path.join(ctx.folder, plan.cad_dxf_name(version, "dxf"))
    entries = []
    for item in images:
        entries.append({"layer": item["layer"], "file": dxfout.relative_path(item["path"], dxf), "size_px": item["size_px"],
                        "insert": item["insert"], "pixel": item["pixel"]})
    dxfout.write_dxf(dxf, entries)
    result.dxf = dxf
    result.files.append((dxf, os.path.getsize(dxf)))
    core = ctx.tools.get("acad")
    if core:
        result.check = external.check_dxf(core, dxf, tick=ctx.tick)
    if ctx.cad.get("format") == "dwg":
        if not core:
            raise Failure("DWG : AutoCAD est introuvable")
        dwg = os.path.join(ctx.folder, plan.cad_dxf_name(version, "dwg"))
        ok, message = external.dxf_to_dwg(core, dxf, dwg, tick=ctx.tick)
        if not ok:
            raise Failure(message)
        result.dwg = dwg
        result.files.append((dwg, os.path.getsize(dwg)))
