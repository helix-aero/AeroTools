"""Aero-Facade: the Metashape adapter. The ONLY module of the tool that imports `Metashape`; it implements `backend.Backend`.

How a vertical-plane orthophoto gets OUT of Metashape (measured on Metashape 2.2.1, see docs/AERO_FACADE_SPEC.md, "Adapter notes"):
`exportRaster` writes nothing for a vertical planar projection, but it exports a *horizontal* raster without trouble. So every façade
gets its own throw-away work chunk (`AeroFacade_<site>_<letter>`, a copy of the user's chunk) in which the *coordinate system is
rotated* so that the façade plane becomes the horizontal plane: `chunk.crs` = a local metric system, `chunk.transform.matrix` = chunk ->
(plane frame: x along the wall, y up, z toward the viewer, origin = bottom-left corner of the wall). In that chunk a perfectly ordinary
`buildDem` / `buildOrthomosaic` / `exportRaster` produces the façade picture, with exact pixel size and extent. The user's chunk is
never modified.

Threading model (for the UI): every call into Metashape that processes data (buildDem, buildOrthomosaic, exportRaster, copy) runs
on the MAIN thread and blocks it (Metashape shows its own progress dialog). Nothing here touches Qt. `progress(fraction, text)` may be
called from inside a Metashape callback: it must only store plain state. Between Metashape calls (and during the pure-numpy part of
`detect`) the adapter calls `self.pump()` (set it to `QApplication.processEvents`) so the window stays alive. Cancelling: the flag
`cancelled()` is checked between steps and inside Metashape callbacks (raising KeyboardInterrupt there aborts the job cleanly, no
dialog); Metashape's own Cancel button also ends up as `Cancelled`.
"""

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time

import numpy as np

import Metashape

from ... import journal
from . import detect as detect_module
from . import geometry
from .backend import Backend, Cancelled, Progress
from .model import Method, margins_for

TAG = "AeroFacade"
WORK_PREFIX = "AeroFacade_"
PREVIEW_LABEL = WORK_PREFIX + "preview"
META_SIG = "aerofacade.sig"
META_STAGE = "aerofacade.stage"            # "" -> "dem" -> "ortho"
META_PIXEL = "aerofacade.pixel"
META_LETTER = "aerofacade.letter"
LOCAL_WKT = 'LOCAL_CS["AeroFacade plane",LOCAL_DATUM["Local Datum",0],UNIT["metre",1]]'
MIN_TIE_POINTS = 100                       # fewer tie points than this: no detection
MIN_POINTS_IN_REGION = 30                  # a tie-point DEM needs this many points inside the façade box
MAX_CAMERAS_DEFAULT = 250                  # cameras kept enabled in a work chunk (the best placed ones)
PREVIEW_MAX_CAMERAS = 120
EXT = {"png": "png", "jpg": "jpg", "jpeg": "jpg", "tif": "tif", "tiff": "tif"}


def log(message):
    journal.write("Aero-Facade: " + message, tag=TAG)


# --------------------------------------------------------------------------------------------------
# Pure helpers (numpy only: tested without Metashape)
# --------------------------------------------------------------------------------------------------

def plane_matrix(facade):
    """4 x 4 numpy matrix ENU -> plane frame (x along the wall to the right, y up, z toward the cameras, origin = bottom-left corner)."""
    u, v, n = geometry.plane_axes(facade)
    o = np.array(facade.origin, dtype=np.float64)
    A = np.eye(4)
    A[0, :3], A[1, :3], A[2, :3] = u, v, n
    A[:3, 3] = -np.array([u @ o, v @ o, n @ o])
    return A


def plane_box(facade, margins):
    """(s0, t0, s1, t1): the picture area in plane coordinates (the wall rectangle grown by the margins)."""
    m = margins_for(margins, facade)
    return -m.left, -m.bottom, facade.length + m.right, facade.height + m.top


def region_in_chunk(N, facade, margins):
    """Bounding region of the façade for a work chunk whose transform is `N` (chunk -> plane metres, 4 x 4 numpy).

    Returns (center, size, rot): chunk coordinates of the centre, size in chunk units, 3 x 3 rotation (columns = box axes in chunk axes)."""
    m = margins_for(margins, facade)
    s0, t0, s1, t1 = plane_box(facade, m)
    Ni = np.linalg.inv(N)
    centre = Ni @ np.array([(s0 + s1) / 2.0, (t0 + t1) / 2.0, 0.0, 1.0])
    cols = Ni[:3, :3]
    k = float(np.linalg.norm(cols[:, 0]))                   # chunk units per metre
    rot = cols / k
    size = np.array([s1 - s0, t1 - t0, 2.0 * m.depth]) * k
    return centre[:3], size, rot


def signature(facade, margins, method, enu):
    """Short hash of everything that makes a work chunk reusable: the plane frame, the margins, the method and the ENU frame in use."""
    m = margins_for(margins, facade)
    method = Method.from_key(method)
    parts = [facade.letter, [round(c, 3) for c in facade.origin], [round(c, 5) for c in facade.u], [round(c, 5) for c in facade.n],
             round(facade.length, 2), round(facade.height, 2), [round(m.left, 2), round(m.right, 2), round(m.top, 2), round(m.bottom, 2), round(m.depth, 2)],
             method.key, [round(float(c), 1) for c in np.asarray(enu)[:3, 3]], [round(float(c), 4) for c in np.asarray(enu)[:3, :3].ravel()]]
    return hashlib.sha1(json.dumps(parts).encode("ascii")).hexdigest()[:16]


def work_label(site, letter):
    return "%s%s_%s" % (WORK_PREFIX, site, letter)


def rank_cameras(centres, forwards, visible, plane_z, max_cameras=MAX_CAMERAS_DEFAULT, min_front=0.17):
    """Which cameras to keep for a façade. All arrays are numpy: `centres` (N, 3) in plane coordinates, `forwards` (N, 3) unit viewing
    directions in plane coordinates, `visible` (N,) fraction of the sample points of the façade seen in the picture (0..1).

    A camera is kept when it stands on the viewer's side (z > 0), looks toward the wall (angle to the plane normal < 80 degrees) and
    sees part of it; the best `max_cameras` by (visible fraction, how frontal) are returned as sorted indices. If nothing is on the
    viewer's side (a façade whose normal points the wrong way) every camera that sees it is kept instead."""
    centres, forwards, visible = np.asarray(centres, float), np.asarray(forwards, float), np.asarray(visible, float)
    front = -forwards[:, 2]
    ok = (plane_z > 0.0) & (front > min_front) & (visible > 0)
    if ok.sum() < 3:
        ok = visible > 0
        front = np.abs(forwards[:, 2])
    idx = np.nonzero(ok)[0]
    score = visible[idx] + 0.5 * front[idx]
    order = idx[np.argsort(-score, kind="stable")][:max_cameras]
    return np.sort(order)


def spread_cameras(centres, k):
    """Indices of at most `k` of the cameras, spread out in space (farthest-point sampling on the centres, start: the camera nearest to
    their mean). Depth maps cost about the same per photo whatever its neighbours are, so fewer, well spread photos are much faster."""
    c = np.asarray(centres, dtype=np.float64).reshape(-1, 3)
    n = len(c)
    if n <= k:
        return list(range(n))
    first = int(np.argmin(((c - c.mean(axis=0)) ** 2).sum(axis=1)))
    chosen = [first]
    dist = ((c - c[first]) ** 2).sum(axis=1)
    while len(chosen) < k:
        nxt = int(np.argmax(dist))
        chosen.append(nxt)
        dist = np.minimum(dist, ((c - c[nxt]) ** 2).sum(axis=1))
    return sorted(chosen)


def sample_points(box, n=3):
    """n x n sample points (s, t) over the picture area, edges included."""
    s0, t0, s1, t1 = box
    return [(s0 + (s1 - s0) * i / (n - 1.0), t0 + (t1 - t0) * j / (n - 1.0)) for j in range(n) for i in range(n)]


def finished(prog, text):
    """Last progress report of a step that is DONE: a cancel that arrives now must not throw the finished work away (it is noticed at the
    start of the next step)."""
    try:
        prog(1.0, text)
    except Cancelled:
        pass


def explain(exc):
    """Plain-English reason for a failure."""
    text = str(exc) or exc.__class__.__name__
    low = text.lower()
    if "empty raster" in low or "no data" in low:
        return "Metashape found nothing to draw inside this area (no data in the box)."
    if "out of memory" in low or "bad_alloc" in low or "memory" in low:
        return "Metashape ran out of memory: use bigger pixels or smaller margins."
    if "gpu" in low or "opencl" in low:
        return "The graphics card refused the computation (%s)." % text
    if "depth" in low and "not" in low:
        return "No depth maps are available for this area (%s)." % text
    return text


class _Meter(object):
    """Turns Metashape's 0..100 callbacks into Progress fractions. Plain Python only. When the operator cancelled, raises
    KeyboardInterrupt (the one exception that makes Metashape abort a job cleanly, without its "Retry?" dialog)."""

    def __init__(self, prog, lo, hi, text):
        self.prog, self.lo, self.hi, self.text = prog, lo, hi, text
        self._last = 0.0

    def __call__(self, pct):
        if self.prog.is_cancelled():
            raise KeyboardInterrupt("cancelled")
        now = time.time()
        if now - self._last < 0.2 and pct < 99.9:
            return
        self._last = now
        frac = self.prog.lo + (self.prog.hi - self.prog.lo) * (self.lo + (self.hi - self.lo) * min(100.0, max(0.0, pct)) / 100.0)
        self.prog.fraction, self.prog.text = frac, self.text
        if self.prog.callback is not None:
            self.prog.callback(frac, self.text)


# --------------------------------------------------------------------------------------------------
# Metashape <-> numpy glue
# --------------------------------------------------------------------------------------------------

def _np(m):
    return np.array([[m[i, j] for j in range(4)] for i in range(4)], dtype=np.float64)


def _mat(a):
    return Metashape.Matrix([[float(a[i][j]) for j in range(4)] for i in range(4)])


def _vec(a):
    return Metashape.Vector([float(x) for x in a])


def _meta_get(chunk, key):
    try:
        return chunk.meta[key]
    except Exception:
        return None


def _meta_set(chunk, key, value):
    try:
        chunk.meta[key] = str(value)
    except Exception:
        pass


def enu_matrix(chunk):
    """4 x 4 numpy matrix chunk coordinates -> local east/north/up metres around the middle of the chunk region.
    A chunk without georeference keeps its own axes (the transform, if it has one)."""
    T = chunk.transform.matrix
    if T is None:
        return np.eye(4)
    if chunk.crs is not None:
        LF = chunk.crs.localframe(T.mulp(chunk.region.center))
        return _np(LF * T)
    return _np(T)


class MetashapeBackend(Backend):
    """The Backend that works on a Metashape chunk (pinned when the object is created). See the module docstring for the model."""

    threaded = False                 # the wizard must call every method on the main thread (see the module docstring)

    def __init__(self, chunk=None, pump=None, max_cameras=MAX_CAMERAS_DEFAULT, depth_downscale=4, depth_max_cameras=40, keep_work_chunks=False):
        self.doc = Metashape.app.document
        chunk = chunk if chunk is not None else self.doc.chunk
        if chunk is None:
            raise RuntimeError("No chunk is open in Metashape.")
        src = _meta_get(chunk, "aerofacade.source")          # the active chunk is one of our work chunks: go back to its source
        if src:
            for other in self.doc.chunks:
                if str(other.key) == src:
                    chunk = other
        self._chunk = chunk
        self._key = chunk.key
        self.psx = self.doc.path or ""
        self.pump = pump or (lambda: None)
        self.max_cameras = int(max_cameras)
        self.depth_downscale = int(depth_downscale)
        self.depth_max_cameras = int(depth_max_cameras)       # photos used for the depth maps of one façade (spread out)
        self.keep_work_chunks = bool(keep_work_chunks)
        self.enu = enu_matrix(chunk)                        # chunk -> ENU, frozen now: every façade frame is expressed in it
        self._points = None                                 # (N, 3) ENU tie points, read once
        self._cameras = None                                # (M, 3) ENU camera centres
        self._tracks = None                                 # (N,) track id of each tie point of `_points`
        self._cam_tracks = None                             # per aligned camera: array of the track ids it sees
        self._thumbs = {}
        self._ortho_of = {}                                 # façade letter -> key of the work chunk that holds its orthophoto
        self._tmp = None
        self._site = None
        self._date = None

    # ---- small things -------------------------------------------------------------------------------
    def _src(self):
        """The pinned chunk (looked up by key: a Metashape chunk object is invalid after another chunk is removed)."""
        try:
            self._chunk.label
            return self._chunk
        except Exception:
            found = self.doc.findChunk(self._key) if hasattr(self.doc, "findChunk") else None
            if found is None:
                raise RuntimeError("The chunk this window works on is not in the project any more.")
            self._chunk = found
            return found

    def tmp(self):
        if self._tmp is None or not os.path.isdir(self._tmp):
            self._tmp = tempfile.mkdtemp(prefix="aerofacade_")
        return self._tmp

    def _check(self, prog):
        self.pump()
        prog.check()

    def _call(self, prog, lo, hi, text, func, *args, **kwargs):
        """Run one Metashape processing call with its progress mapped to [lo, hi] of `prog`; a cancel becomes `Cancelled`."""
        self.pump()
        prog.check()
        kwargs["progress"] = _Meter(prog, lo, hi, text)
        try:
            return func(*args, **kwargs)
        except KeyboardInterrupt:
            raise Cancelled()

    # ---- Backend: project_info / availability ----------------------------------------------------------
    def project_info(self):
        chunk = self._src()
        from ..aeroexports import naming
        if self._site is None:
            self._site = naming.guess_site(self.psx) if self.psx else "SITE"
        if self._date is None:
            dates = []
            for cam in chunk.cameras[:60]:
                try:
                    dates.append(cam.photo.meta["Exif/DateTimeOriginal"])
                except Exception:
                    pass
            day, _source = naming.flight_date(dates)
            self._date = naming.format_date(day, "short")
        outputs = naming.outputs_folder(self.psx) if self.psx else os.path.join(os.path.expanduser("~"), "Outputs")
        return {"chunk": chunk.label, "site": self._site, "date": self._date, "outputs": outputs, "crs": "Local east/north (m)"}

    def availability(self):
        chunk = self._src()
        out = {}
        tp = chunk.tie_points
        n = len(tp.points) if tp is not None else 0
        out["tie_points"] = (True, "") if n >= MIN_TIE_POINTS else (False, "This chunk has no usable tie points (align the photos first).")
        dm = chunk.depth_maps
        aligned = sum(1 for c in chunk.cameras if c.transform is not None)
        if dm is not None and len(dm.keys()) > 0:
            out["depth_maps"] = (True, "")
        elif aligned >= 3:
            out["depth_maps"] = (True, "")           # not computed yet: they are computed for the façade area only (a few minutes)
        else:
            out["depth_maps"] = (False, "There are not enough aligned photos to compute depth maps.")
        pc = chunk.point_cloud
        if pc is not None and getattr(pc, "point_count", 0) > 0:
            out["dense_cloud"] = (True, "")
        else:
            out["dense_cloud"] = (False, "No dense cloud in this chunk.")
        return out

    def depth_maps_exist(self):
        dm = self._src().depth_maps
        return dm is not None and len(dm.keys()) > 0

    # ---- tie points ---------------------------------------------------------------------------------------
    def read_points(self, prog=None):
        """(N, 3) tie points in ENU metres (cached) and (M, 3) camera centres. ~1.3 s for a million points."""
        if self._points is None:
            prog = Progress.of(prog)
            chunk = self._src()
            prog(0.0, "Reading the tie points")
            t0 = time.time()
            pts = chunk.tie_points.points
            valid = [p for p in pts if p.valid]
            raw = np.array([(p.coord[0], p.coord[1], p.coord[2]) for p in valid], dtype=np.float64).reshape(-1, 3)
            self._tracks = np.array([p.track_id for p in valid], dtype=np.int64)
            self._points = raw @ self.enu[:3, :3].T + self.enu[:3, 3]
            cams = np.array([tuple(c.center)[:3] for c in chunk.cameras if c.transform is not None], dtype=np.float64).reshape(-1, 3)
            self._cameras = cams @ self.enu[:3, :3].T + self.enu[:3, 3]
            log("read %d tie points and %d cameras in %.1f s" % (len(self._points), len(self._cameras), time.time() - t0))
            prog(1.0, "%d tie points read" % len(self._points))
        return self._points, self._cameras

    # ---- Backend: detect ----------------------------------------------------------------------------------
    def detect(self, polygon, progress, cancelled):
        """Façades inside `polygon` (ENU east/north metres, the frame of the preview). Reading the tie points is done on the main
        thread (about 1 s per million points); the detector (numpy) runs in a worker thread while this call pumps `self.pump`."""
        prog = Progress.of(progress, cancelled)
        t0 = time.time()
        points, cams = self.read_points(prog.sub(0.0, 0.25))
        if len(points) < MIN_TIE_POINTS:
            raise RuntimeError("This chunk has no usable tie points.")
        self._check(prog)
        state = {"fraction": 0.0, "text": "", "result": None, "error": None}

        def store(fraction, text):
            state["fraction"], state["text"] = fraction, text

        class _Flag(object):
            def __call__(inner):
                return prog.is_cancelled()

        def work():
            try:
                state["result"] = detect_module.detect_in_polygon(points, polygon, progress=store, cancelled=_Flag(), cameras=cams)
            except BaseException as exc:             # Cancelled, MemoryError ...
                state["error"] = exc

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        while thread.is_alive():
            thread.join(0.05)
            prog(0.25 + 0.75 * state["fraction"], state["text"] or "Looking for façades")
            self.pump()
        if state["error"] is not None:
            raise state["error"]
        facades = state["result"] or []
        try:
            self._orient(facades, prog)
        except Cancelled:
            raise
        except Exception as exc:                                  # the engine's own orientation stays
            log("could not check the viewing side of the façades (%s)" % exc)
        log("detection: %d façade(s) in %.1f s (%d tie points in total)" % (len(facades), time.time() - t0, len(points)))
        prog(1.0, "%d façade(s) found" % len(facades))
        return facades

    def _camera_tracks(self):
        """For every aligned camera: (ENU centre, array of the track ids of the tie points it sees). Cached; about 1 s per million."""
        if self._cam_tracks is None:
            chunk = self._src()
            tp = chunk.tie_points
            rows = []
            for cam in chunk.cameras:
                if cam.transform is None:
                    continue
                try:
                    ids = np.array([pr.track_id for pr in tp.projections[cam]], dtype=np.int64)
                except Exception:
                    ids = np.zeros(0, dtype=np.int64)
                rows.append(ids)
            self._cam_tracks = rows
        return self._cam_tracks

    def _orient(self, facades, prog):
        """Check, with the tie points themselves, on which side of each wall the photos that SEE it were taken (a camera on the
        far side of a building cannot see its other wall, a plain 'mean camera side' is wrong when the flight goes all around). A
        façade whose normal points to the wrong side is turned round (origin at the other end, u and n reversed): the picture is
        then the outside view, not mirrored."""
        if not facades or self._tracks is None:
            return
        rows = self._camera_tracks()
        cams = self._cameras
        for f in facades:
            band = geometry.enu_to_plane(f, self._points)
            sel = (np.abs(band[:, 2]) < 0.35) & (band[:, 0] >= 0) & (band[:, 0] <= f.length) & (band[:, 1] >= 0) & (band[:, 1] <= f.height)
            ids = np.unique(self._tracks[sel])
            if len(ids) < 10:
                continue
            side = geometry.distance_to_plane(f, cams)
            pos = neg = 0
            for k, tr in enumerate(rows):
                if len(tr) == 0:
                    continue
                seen = int(np.isin(tr, ids, assume_unique=False).sum())
                if seen < 8:
                    continue
                if side[k] > 0:
                    pos += seen
                else:
                    neg += seen
            f.views = (pos, neg)                              # matches on the viewer's side / on the other side (informative)
            if neg > 2 * pos and neg > 50:
                self._turn_round(f)
                log("façade %s turned round: its photos stand on the other side (%d vs %d matches)" % (f.letter, neg, pos))

    @staticmethod
    def _turn_round(f):
        end = f.end
        f.x0, f.y0, f.z0 = end
        f.ux, f.uy, f.uz = -f.ux, -f.uy, -f.uz
        f.nx, f.ny, f.nz = -f.nx, -f.ny, -f.nz
        f.azimuth = (f.azimuth + 180.0) % 360.0

    # ---- Backend: ortho_preview -----------------------------------------------------------------------------
    def ortho_preview(self, max_px=1600):
        """(RGB uint8 array, extent (left, bottom, right, top)) in ENU east/north metres. Uses a quick low-resolution orthomosaic built in
        a work chunk from the tie-point surface and the near-vertical photos (the chunk's own orthomosaic, when it has one, is
        resampled to ENU axes instead: see `_preview_from_ortho`). Falls back to a density picture of the tie points."""
        points, _cams = self.read_points()
        chunk = self._src()
        t0 = time.time()
        extent = self._preview_extent(points)
        try:
            if chunk.orthomosaic is not None:
                result = self._preview_from_ortho(chunk, extent, max_px)
                log("preview from the chunk orthomosaic in %.1f s" % (time.time() - t0))
                return result
        except Exception as exc:
            log("preview from the orthomosaic failed (%s), building one" % exc)
        try:
            result = self._preview_built(extent, max_px)
            log("preview built from the tie-point surface in %.1f s" % (time.time() - t0))
            return result
        except Exception as exc:
            log("preview build failed (%s), drawing the tie point density" % exc)
        return self._preview_density(points, extent, max_px)

    @staticmethod
    def _preview_extent(points):
        lo = np.percentile(points[:, :2], 0.5, axis=0)
        hi = np.percentile(points[:, :2], 99.5, axis=0)
        pad = 0.04 * (hi - lo)
        lo, hi = lo - pad, hi + pad
        side = hi - lo
        if side.max() > 600.0:                               # very large chunk: keep the densest 600 m
            c = np.median(points[:, :2], axis=0)
            lo, hi = c - 300.0, c + 300.0
        return float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])

    def _preview_density(self, points, extent, max_px):
        left, bottom, right, top = extent
        w, h = right - left, top - bottom
        scale = max_px / max(w, h)
        W, H = max(2, int(w * scale)), max(2, int(h * scale))
        ix = np.clip(((points[:, 0] - left) * scale).astype(int), 0, W - 1)
        iy = np.clip(((top - points[:, 1]) * scale).astype(int), 0, H - 1)
        img = np.zeros((H, W), dtype=np.float64)
        np.add.at(img, (iy, ix), 1.0)
        img = np.log1p(img)
        img = (255.0 * img / max(img.max(), 1e-9)).astype(np.uint8)
        return np.dstack([img, img, img]), extent

    def _new_work(self, label, items=None):
        chunk = self._src()
        work = self._copy(chunk, items or [Metashape.DataSource.TiePointsData])
        work.label = label
        _meta_set(work, "aerofacade.source", chunk.key)
        return work

    def _copy(self, chunk, items):
        """`chunk.copy`, which makes the copy the ACTIVE chunk of the project: put the user's active chunk back."""
        active = self.doc.chunk
        work = chunk.copy(items=items, keypoints=False)
        try:
            if active is not None:
                self.doc.chunk = active
        except Exception:
            pass
        return work

    def _remove(self, work):
        if self.keep_work_chunks:
            return
        try:
            self.doc.remove(work)
        except Exception:
            pass

    def _preview_built(self, extent, max_px):
        left, bottom, right, top = extent
        self._drop_label(PREVIEW_LABEL)
        work = self._new_work(PREVIEW_LABEL)
        try:
            work.crs = Metashape.CoordinateSystem(LOCAL_WKT)
            work.transform.matrix = _mat(self.enu)
            Ni = np.linalg.inv(self.enu)
            zs = self._points[:, 2]
            zlo, zhi = float(np.percentile(zs, 0.5)) - 5.0, float(np.percentile(zs, 99.5)) + 5.0
            centre = Ni @ np.array([(left + right) / 2.0, (bottom + top) / 2.0, (zlo + zhi) / 2.0, 1.0])
            k = float(np.linalg.norm(Ni[:3, 0]))
            reg = Metashape.Region()
            reg.center = _vec(centre[:3])
            reg.size = _vec(np.array([right - left, top - bottom, zhi - zlo]) * k)
            reg.rot = Metashape.Matrix([[float(Ni[i, j] / k) for j in range(3)] for i in range(3)])
            work.region = reg
            self._limit_nadir(work, PREVIEW_MAX_CAMERAS)
            box = Metashape.BBox(Metashape.Vector((left, bottom)), Metashape.Vector((right, top)))
            work.buildDem(source_data=Metashape.DataSource.TiePointsData, interpolation=Metashape.EnabledInterpolation, region=box,
                          replace_asset=True)
            res = max(right - left, top - bottom) / float(max_px)
            work.buildOrthomosaic(surface_data=Metashape.DataSource.ElevationData, blending_mode=Metashape.MosaicBlending, fill_holes=True,
                                  region=box, resolution=res, replace_asset=True)
            ortho = work.orthomosaic
            path = os.path.join(self.tmp(), "preview.png")
            work.exportRaster(path, source_data=Metashape.DataSource.OrthomosaicData, image_format=Metashape.ImageFormatPNG, save_alpha=False,
                              white_background=True)
            rgb = self._read_image(path)
            return rgb, (float(ortho.left), float(ortho.bottom), float(ortho.right), float(ortho.top))
        finally:
            self._remove(work)

    def _preview_from_ortho(self, chunk, extent, max_px):
        """The chunk's own orthomosaic, exported small and resampled onto ENU east/north axes."""
        ortho = chunk.orthomosaic
        path = os.path.join(self.tmp(), "preview_src.png")
        factor = max(1.0, max(ortho.width, ortho.height) / float(max_px))
        chunk.exportRaster(path, source_data=Metashape.DataSource.OrthomosaicData, image_format=Metashape.ImageFormatPNG,
                           resolution=float(ortho.resolution) * factor, save_alpha=False, white_background=True, clip_to_boundary=False)
        src = self._read_image(path)
        sl, sb, sr, st = float(ortho.left), float(ortho.bottom), float(ortho.right), float(ortho.top)
        crs = ortho.projection.crs if hasattr(ortho, "projection") and ortho.projection is not None else chunk.crs
        T = chunk.transform.matrix
        Ei = np.linalg.inv(self.enu)
        left, bottom, right, top = extent
        res = max(right - left, top - bottom) / float(max_px)
        W, H = max(2, int(round((right - left) / res))), max(2, int(round((top - bottom) / res)))
        z0 = float(np.median(self._points[:, 2]))
        gx, gy = 17, 17                                      # control nodes: the ENU -> orthomosaic mapping is smooth
        mapx, mapy = np.zeros((gy, gx)), np.zeros((gy, gx))
        for j in range(gy):
            for i in range(gx):
                e = left + (right - left) * i / (gx - 1.0)
                n = top - (top - bottom) * j / (gy - 1.0)
                pc = Ei @ np.array([e, n, z0, 1.0])
                world = T.mulp(_vec(pc[:3]))
                p = crs.project(world)
                mapx[j, i], mapy[j, i] = (p[0] - sl) / (sr - sl) * src.shape[1], (st - p[1]) / (st - sb) * src.shape[0]
        jj, ii = np.meshgrid(np.linspace(0, gy - 1, H), np.linspace(0, gx - 1, W), indexing="ij")
        px = self._bilinear(mapx, jj, ii)
        py = self._bilinear(mapy, jj, ii)
        out = np.zeros((H, W, 3), dtype=np.uint8)
        x0, y0 = np.clip(np.floor(px).astype(int), 0, src.shape[1] - 2), np.clip(np.floor(py).astype(int), 0, src.shape[0] - 2)
        fx, fy = (px - x0)[..., None], (py - y0)[..., None]
        a = src[y0, x0] * (1 - fx) + src[y0, x0 + 1] * fx
        b = src[y0 + 1, x0] * (1 - fx) + src[y0 + 1, x0 + 1] * fx
        out[:] = np.clip(a * (1 - fy) + b * fy, 0, 255).astype(np.uint8)
        outside = (px < 0) | (py < 0) | (px > src.shape[1] - 1) | (py > src.shape[0] - 1)
        out[outside] = 255
        return out, extent

    @staticmethod
    def _bilinear(grid, jj, ii):
        j0 = np.clip(np.floor(jj).astype(int), 0, grid.shape[0] - 2)
        i0 = np.clip(np.floor(ii).astype(int), 0, grid.shape[1] - 2)
        fj, fi = jj - j0, ii - i0
        return (grid[j0, i0] * (1 - fj) * (1 - fi) + grid[j0, i0 + 1] * (1 - fj) * fi + grid[j0 + 1, i0] * fj * (1 - fi)
                + grid[j0 + 1, i0 + 1] * fj * fi)

    @staticmethod
    def _read_image(path):
        """RGB uint8 array from a PNG written by Metashape (Metashape.Image: PIL is not always there)."""
        img = Metashape.Image.open(path)
        data = np.frombuffer(img.tostring(), dtype=np.uint8)
        arr = data.reshape(img.height, img.width, data.size // (img.height * img.width))
        if arr.shape[2] >= 3:
            return np.ascontiguousarray(arr[:, :, :3])
        return np.ascontiguousarray(np.repeat(arr[:, :, :1], 3, axis=2))

    def _drop_label(self, label):
        for other in list(self.doc.chunks):
            try:
                if other.label == label:
                    self.doc.remove(other)
            except Exception:
                pass

    def _limit_nadir(self, work, max_cameras):
        """Keep only the near-vertical photos (at most `max_cameras`, spread over the flight) enabled in a preview chunk."""
        cams, down = [], []
        R = self.enu[:3, :3]
        for cam in work.cameras:
            if cam.transform is None:
                continue
            fwd = R @ np.array(list(cam.transform.mulv(Metashape.Vector((0, 0, 1)))))
            cams.append(cam)
            down.append(-fwd[2] / np.linalg.norm(fwd))
        down = np.array(down)
        good = [c for c, s in zip(cams, down) if s > 0.8]
        if len(good) < 8:
            good = [c for c, s in zip(cams, down) if s > 0.5] or cams
        step = max(1, int(math.ceil(len(good) / float(max_cameras))))
        keep = set(c.key for c in good[::step])
        for cam in work.cameras:
            cam.enabled = bool(cam.transform is not None and cam.key in keep)
        return len(keep)

    # ---- work chunks (one per façade) ---------------------------------------------------------------------
    def _find_work(self, sig):
        for other in self.doc.chunks:
            try:
                if _meta_get(other, META_SIG) == sig:
                    return other
            except Exception:
                continue
        return None

    def _stage_of(self, work):
        return _meta_get(work, META_STAGE) or ""

    def _make_work(self, facade, margins, method, sig, prog):
        """A copy of the source chunk with the façade plane as its horizontal plane, the bounding region set and only the useful cameras
        enabled. Returns the new chunk."""
        method = Method.from_key(method)
        src = self._src()
        items = [Metashape.DataSource.TiePointsData]
        if method == Method.DENSE_CLOUD and src.point_cloud is not None:
            items.append(Metashape.DataSource.PointCloudData)
        if method == Method.DEPTH_MAPS and self.depth_maps_exist():
            items.append(Metashape.DataSource.DepthMapsData)
        t0 = time.time()
        work = self._copy(src, items)
        work.label = work_label(self._site or self.project_info()["site"], facade.letter)
        _meta_set(work, "aerofacade.source", src.key)
        _meta_set(work, META_SIG, sig)
        _meta_set(work, META_LETTER, facade.letter)
        _meta_set(work, META_STAGE, "")
        _meta_set(work, "aerofacade.margins", json.dumps(margins_for(margins, facade).to_dict()))
        N = plane_matrix(facade) @ self.enu
        work.crs = Metashape.CoordinateSystem(LOCAL_WKT)
        work.transform.matrix = _mat(N)
        centre, size, rot = region_in_chunk(N, facade, margins)
        reg = Metashape.Region()
        reg.center = _vec(centre)
        reg.size = _vec(size)
        reg.rot = Metashape.Matrix([[float(rot[i, j]) for j in range(3)] for i in range(3)])
        work.region = reg
        kept = self._limit_cameras(work, N, facade, margins)
        log("work chunk %s made in %.1f s (%d cameras kept)" % (work.label, time.time() - t0, kept))
        return work

    def _limit_cameras(self, work, N, facade, margins):
        """Enable only the cameras that look at the façade from the viewer's side (at most self.max_cameras). Returns how many."""
        box = plane_box(facade, margins)
        Ni = np.linalg.inv(N)
        pts = [Metashape.Vector(tuple((Ni @ np.array([s, t, 0.0, 1.0]))[:3])) for s, t in sample_points(box)]
        cams, centres, forwards, vis = [], [], [], []
        for cam in work.cameras:
            if cam.transform is None:
                continue
            cams.append(cam)
            c = N @ np.array(list(cam.center) + [1.0])
            centres.append(c[:3])
            f = N[:3, :3] @ np.array(list(cam.transform.mulv(Metashape.Vector((0, 0, 1)))))
            forwards.append(f / np.linalg.norm(f))
            inv = cam.transform.inv()
            seen = 0
            for P in pts:
                if inv.mulp(P).z <= 0:
                    continue
                p = cam.project(P)
                if p is not None and 0 <= p.x < cam.sensor.width and 0 <= p.y < cam.sensor.height:
                    seen += 1
            vis.append(seen / float(len(pts)))
        if not cams:
            return 0
        centres = np.array(centres)
        keep = set(rank_cameras(centres, np.array(forwards), np.array(vis), centres[:, 2], self.max_cameras).tolist())
        for i, cam in enumerate(cams):
            cam.enabled = i in keep
        return len(keep)

    def _tie_points_in_box(self, facade, margins):
        points, _ = self.read_points()
        region = geometry.bounding_region(facade, margins)
        return int(geometry.points_in_region(points, region).sum())

    # ---- Backend: existing_dems / build_dems ---------------------------------------------------------------
    def existing_dems(self, facades, margins, method):
        out = {}
        for f in facades:
            work = self._find_work(signature(f, margins, method, self.enu))
            out[f.letter] = bool(work is not None and self._stage_of(work) in ("dem", "ortho") and work.elevation is not None)
        return out

    def build_dems(self, facades, margins, method, progress, cancelled):
        prog = Progress.of(progress, cancelled)
        method = Method.from_key(method)
        todo = [f for f in facades if not f.removed]
        results = {}
        for i, f in enumerate(todo):
            lo, hi = i / float(len(todo)), (i + 1) / float(len(todo))
            sub = prog.sub(lo, hi)
            sig = signature(f, margins, method, self.enu)
            try:
                work = self._find_work(sig)
                if work is not None and self._stage_of(work) in ("dem", "ortho") and work.elevation is not None:
                    results[f.letter] = (True, "Already built.")
                    sub(1.0, "Façade %s: already built" % f.letter)
                    continue
                if work is not None:
                    self._remove_chunk(work)
                results[f.letter] = self._build_dem(f, margins, method, sig, sub)
            except Cancelled:
                log("cancelled during the DEM of façade %s" % f.letter)
                break
            except Exception as exc:
                log("DEM of façade %s failed: %s" % (f.letter, exc))
                results[f.letter] = (False, explain(exc))
        return results

    def _remove_chunk(self, work):
        try:
            self.doc.remove(work)
        except Exception:
            pass

    def _build_dem(self, f, margins, method, sig, prog):
        t0 = time.time()
        m = margins_for(margins, f)
        prog(0.0, "Façade %s: preparing" % f.letter)
        if method == Method.TIE_POINTS and self._tie_points_in_box(f, m) < MIN_POINTS_IN_REGION:
            return False, "Too few tie points on this façade to build a surface (try the depth maps method)."
        work = self._make_work(f, m, method, sig, prog)
        try:
            box = Metashape.BBox(Metashape.Vector(plane_box(f, m)[:2]), Metashape.Vector(plane_box(f, m)[2:]))
            name = {Method.TIE_POINTS: "tie points", Method.DEPTH_MAPS: "depth maps", Method.DENSE_CLOUD: "dense cloud"}[method]
            if method == Method.TIE_POINTS:
                source = Metashape.DataSource.TiePointsData
                lo = 0.1
            elif method == Method.DEPTH_MAPS:
                source = Metashape.DataSource.DepthMapsData
                lo = 0.1
                if work.depth_maps is None or len(work.depth_maps.keys()) == 0:
                    enabled = [c for c in work.cameras if c.enabled and c.transform is not None]
                    if not enabled:
                        raise RuntimeError("No photo looks at this façade.")
                    pick = spread_cameras([list(c.center) for c in enabled], self.depth_max_cameras)
                    keys = [enabled[i].key for i in pick]
                    self._call(prog, 0.1, 0.85, "Façade %s: computing depth maps for %d photos" % (f.letter, len(keys)), work.buildDepthMaps,
                               downscale=self.depth_downscale, filter_mode=Metashape.MildFiltering, cameras=keys, reuse_depth=False,
                               max_neighbors=16)
                    lo = 0.85
            else:
                source = Metashape.DataSource.PointCloudData
                lo = 0.1
            self._call(prog, lo, 1.0, "Façade %s: building the surface from the %s" % (f.letter, name), work.buildDem, source_data=source,
                       interpolation=Metashape.EnabledInterpolation, region=box, replace_asset=True)
            dem = work.elevation
            if dem is None:
                raise RuntimeError("Metashape built no surface for this façade.")
            _meta_set(work, META_STAGE, "dem")
            _meta_set(work, "aerofacade.method", method.key)
            log("DEM of façade %s (%s): %dx%d px at %.3f m, heights %.2f..%.2f m, %.1f s" % (f.letter, name, dem.width, dem.height, dem.resolution,
                                                                                           dem.min, dem.max, time.time() - t0))
            finished(prog, "Façade %s: surface ready" % f.letter)
            return True, "Surface built from the %s (%d x %d, %.1f cm per pixel)." % (name, dem.width, dem.height, dem.resolution * 100.0)
        except BaseException:
            self._remove_chunk(work)
            raise

    # ---- Backend: build_orthos -------------------------------------------------------------------------------
    def build_orthos(self, facades, margins, method, pixel_m, progress, cancelled):
        prog = Progress.of(progress, cancelled)
        method = Method.from_key(method)
        todo = [f for f in facades if not f.removed]
        results = {}
        for i, f in enumerate(todo):
            sub = prog.sub(i / float(len(todo)), (i + 1) / float(len(todo)))
            sig = signature(f, margins, method, self.enu)
            try:
                work = self._find_work(sig)
                if work is None or self._stage_of(work) not in ("dem", "ortho"):
                    ok, msg = self._build_dem(f, margins, method, sig, sub.sub(0.0, 0.4)) if work is None else (True, "")
                    if not ok:
                        results[f.letter] = (False, msg)
                        continue
                    work = self._find_work(sig)
                results[f.letter] = self._build_ortho(f, margins, work, pixel_m, sub.sub(0.4, 1.0) if work is not None else sub)
            except Cancelled:
                log("cancelled during the orthophoto of façade %s" % f.letter)
                break
            except Exception as exc:
                log("orthophoto of façade %s failed: %s" % (f.letter, exc))
                results[f.letter] = (False, explain(exc))
        return results

    def _build_ortho(self, f, margins, work, pixel_m, prog):
        t0 = time.time()
        m = margins_for(margins, f)
        stage, pixel = self._stage_of(work), _meta_get(work, META_PIXEL)
        if stage == "ortho" and pixel and abs(float(pixel) - pixel_m) < 1e-9 and work.orthomosaic is not None:
            self._ortho_of[f.letter] = work.key
            finished(prog, "Façade %s: orthophoto already built" % f.letter)
            return True, "Already built."
        w, h = geometry.image_size_px(f, m, pixel_m)
        if w * h > geometry.TOO_BIG_MPIX * 1e6:
            return False, "The picture would be %d x %d pixels (%.0f megapixels): use a bigger pixel size." % (w, h, w * h / 1e6)
        box = Metashape.BBox(Metashape.Vector(plane_box(f, m)[:2]), Metashape.Vector(plane_box(f, m)[2:]))
        self._call(prog, 0.0, 0.95, "Façade %s: building the orthophoto (%d x %d px)" % (f.letter, w, h), work.buildOrthomosaic,
                   surface_data=Metashape.DataSource.ElevationData, blending_mode=Metashape.MosaicBlending, fill_holes=True, region=box,
                   resolution=float(pixel_m), replace_asset=True)
        ortho = work.orthomosaic
        if ortho is None:
            raise RuntimeError("Metashape built no orthophoto for this façade.")
        _meta_set(work, META_STAGE, "ortho")
        _meta_set(work, META_PIXEL, repr(float(pixel_m)))
        self._thumbs.pop(f.letter, None)
        self._ortho_of[f.letter] = work.key
        log("orthophoto of façade %s: %dx%d px at %.3f m in %.1f s" % (f.letter, ortho.width, ortho.height, ortho.resolution, time.time() - t0))
        finished(prog, "Façade %s: orthophoto ready" % f.letter)
        return True, "%d x %d px at %.1f cm." % (ortho.width, ortho.height, pixel_m * 100.0)

    # ---- Backend: thumbnail ------------------------------------------------------------------------------------
    def _work_for(self, facade):
        """The work chunk holding the last orthophoto built for this façade (whatever the method), or None."""
        key = self._ortho_of.get(facade.letter)
        if key is None:
            return None
        work = self.doc.findChunk(key) if hasattr(self.doc, "findChunk") else None
        if work is None:
            for other in self.doc.chunks:
                if other.key == key:
                    work = other
        return work

    def thumbnail(self, facade, width=240):
        if facade.letter in self._thumbs:
            return self._thumbs[facade.letter]
        work = self._work_for(facade)
        if work is None or work.orthomosaic is None:
            raise RuntimeError("The orthophoto of façade %s is not built yet." % facade.letter)
        ortho = work.orthomosaic
        factor = max(1.0, ortho.width / float(width))
        path = os.path.join(self.tmp(), "thumb_%s.png" % facade.letter)
        # `resolution` resamples the whole picture; `width` / `height` would only cut a window of that many native pixels out of it (measured)
        work.exportRaster(path, source_data=Metashape.DataSource.OrthomosaicData, image_format=Metashape.ImageFormatPNG,
                          resolution=float(ortho.resolution) * factor, save_alpha=False, white_background=True, clip_to_boundary=False)
        arr = self._read_image(path)
        self._thumbs[facade.letter] = arr
        return arr

    # ---- Backend: export -----------------------------------------------------------------------------------------
    def export(self, facades, folder, fmt, options, progress, cancelled):
        prog = Progress.of(progress, cancelled)
        options = options or {}
        ext = EXT.get(str(fmt).lower().lstrip("."), "png")
        info = self.project_info()
        os.makedirs(folder, exist_ok=True)
        todo = [f for f in facades if not f.removed]
        out = []
        for i, f in enumerate(todo):
            sub = prog.sub(i / float(len(todo)), (i + 1) / float(len(todo)))
            path = os.path.join(folder, "%s_%s_facade_%s.%s" % (info["site"], info["date"], f.letter, ext))
            try:
                work = self._work_for(f)
                if work is None or work.orthomosaic is None:
                    out.append((path, False, "The orthophoto of this façade is not built yet."))
                    continue
                t0 = time.time()
                self._export_one(f, work, path, ext, options, sub)
                log("exported %s (%.1f s, %.1f MB)" % (os.path.basename(path), time.time() - t0, os.path.getsize(path) / 1e6))
                out.append((path, True, ""))
            except Cancelled:
                log("cancelled during the export of façade %s" % f.letter)
                break
            except Exception as exc:
                log("export of façade %s failed: %s" % (f.letter, exc))
                out.append((path, False, explain(exc)))
        return out

    def _export_one(self, f, work, path, ext, options, prog):
        ortho = work.orthomosaic
        big = ortho.width * ortho.height * 3 > 3.5e9
        comp = Metashape.ImageCompression()
        comp.tiff_big = bool(big)
        comp.tiff_tiled = False
        comp.tiff_overviews = False
        comp.jpeg_quality = int(options.get("jpeg_quality", 92))
        fmt = {"png": Metashape.ImageFormatPNG, "jpg": Metashape.ImageFormatJPEG, "tif": Metashape.ImageFormatTIFF}[ext]
        self._call(prog, 0.0, 0.9, "Façade %s: writing %s" % (f.letter, os.path.basename(path)), work.exportRaster, path,
                   source_data=Metashape.DataSource.OrthomosaicData, image_format=fmt, image_compression=comp, save_alpha=False,
                   white_background=True, clip_to_boundary=False, save_world=False)
        if not os.path.isfile(path):
            raise RuntimeError("Metashape wrote no file.")
        pixel = float(_meta_get(work, META_PIXEL) or ortho.resolution)
        if options.get("georef_txt", True):
            self._write_georef(f, work, path, pixel)
        if options.get("dxf"):
            self._write_dxf(f, work, path, pixel, options)
        finished(prog, "Façade %s: done" % f.letter)

    # ---- DXF with the picture standing in its vertical plane -------------------------------------------------
    def _dxf_crs(self, options):
        """Coordinate system of the DXF: `options["dxf_epsg"]`, else the chunk's own when it is projected, else WGS 84 / UTM of the site."""
        src = self._src()
        epsg = options.get("dxf_epsg")
        if epsg:
            return Metashape.CoordinateSystem("EPSG::%d" % int(epsg)), "EPSG:%d" % int(epsg)
        crs = src.crs
        if crs is not None and not getattr(crs, "geographic", True) and not getattr(crs, "geocentric", False):
            return crs, str(getattr(crs, "name", "chunk coordinate system"))
        T = src.transform.matrix
        mid = np.linalg.inv(self.enu) @ np.append(np.median(self._points, axis=0) if self._points is not None else np.zeros(3), 1.0)
        lon, lat = crs.project(T.mulp(_vec(mid[:3])))[:2]
        zone = int((lon + 180.0) // 6.0) + 1
        code = (32600 if lat >= 0 else 32700) + zone
        return Metashape.CoordinateSystem("EPSG::%d" % code), "EPSG:%d (WGS 84 / UTM %d%s)" % (code, zone, "N" if lat >= 0 else "S")

    def _write_dxf(self, f, work, path, pixel, options):
        """`<picture>.dxf`: one IMAGE entity placed in 3D (E, N, height of the output system) so that the picture stands on the façade, at scale.
        The image path in the DXF is relative (same folder). Written with `aeroexports.dxfout`, extended with a 3D placement."""
        from ..aeroexports import dxfout
        ortho = work.orthomosaic
        tcrs, label = self._dxf_crs(options)
        src = self._src()
        T = src.transform.matrix
        Ei = np.linalg.inv(self.enu)

        def to_out(s, t):
            enu = geometry.plane_to_enu(f, [[s, t, 0.0]])[0]
            pc = Ei @ np.append(enu, 1.0)
            return np.array(list(tcrs.project(T.mulp(_vec(pc[:3])))), dtype=np.float64)

        left, bottom = float(ortho.left), float(ortho.bottom)
        step = 50.0
        p0, pu, pv = to_out(left, bottom), to_out(left + step, bottom), to_out(left, bottom + step)
        item = {"layer": "FACADE_%s" % f.letter, "file": os.path.basename(path), "size_px": (ortho.width, ortho.height),
                "insert": (p0[0], p0[1]), "insert_z": p0[2], "pixel": (pixel, pixel),
                "u_vec": tuple((pu - p0) * (pixel / step)), "v_vec": tuple((pv - p0) * (pixel / step))}
        dxf = os.path.splitext(path)[0] + ".dxf"
        dxfout.write_dxf(dxf, [item])
        log("DXF of façade %s written in %s: %s" % (f.letter, label, os.path.basename(dxf)))
        return dxf

    def _write_georef(self, f, work, path, pixel):
        """<picture>.txt: the frame, the pixel size and the exact extent, enough to place and measure the picture."""
        ortho = work.orthomosaic
        lines = geometry.georef_lines(f, self._margins_of(work, f), pixel)
        lines = [ln for ln in lines if not ln.startswith(("image size px", "picture top-left"))]     # replaced by the exact values below
        lines += ["image size px: %d x %d" % (ortho.width, ortho.height),
                  "picture top-left corner in the plane (s along, t up) m: %.4f %.4f" % (ortho.left, ortho.top),
                  "exact picture extent in the plane frame (s along, t up) m: left %.4f bottom %.4f right %.4f top %.4f" %
                  (ortho.left, ortho.bottom, ortho.right, ortho.top)]
        with open(os.path.splitext(path)[0] + ".txt", "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    @staticmethod
    def _margins_of(work, facade):
        from .model import Margins
        raw = _meta_get(work, "aerofacade.margins")
        return Margins.from_dict(json.loads(raw)) if raw else Margins()

    # ---- cleanup ---------------------------------------------------------------------------------------------------
    def cleanup(self, keep_chunks=None):
        """Delete the work chunks of this source chunk (unless `keep_chunks`, default `self.keep_work_chunks`) and the temp files."""
        keep = self.keep_work_chunks if keep_chunks is None else keep_chunks
        if not keep:
            for other in list(self.doc.chunks):
                try:
                    if _meta_get(other, "aerofacade.source") == str(self._key) or other.label == PREVIEW_LABEL:
                        self.doc.remove(other)
                except Exception:
                    pass
        if self._tmp and os.path.isdir(self._tmp):
            shutil.rmtree(self._tmp, ignore_errors=True)
        self._tmp = None
