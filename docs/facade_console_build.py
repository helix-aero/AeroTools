# Aero-Facade study: build the planar DEM and the orthomosaic of ONE detected wall, in the Metashape 2.2.1 console. Never saves the project.
#
#   1. export the tie points (3 lines, see docs/AERO_FACADE_STUDY.md, "Check 1") and detect the walls offline:
#        python -m aerotools.tools.facade.detect tie_enu.npy --out walls.json
#   2. edit the five settings below, then in the console:   exec(open(r"C:\path\facade_console_build.py").read())
#
# Built from steps that were verified in the Citadelle session (planar OrthoProjection matrix, 2D region, DEM from tie points, chunk.region depth crop,
# ortho from the planar DEM, ortho_view.captureView); the whole script in one go has NOT been run yet. exportRaster does not write vertical planar rasters
# (tested, silent), so the picture is taken with ortho_view.captureView. Its framing is NOT understood: in the test the capture showed ~3x less
# ground than ov.scale asked for. Check the picture and adjust ov.scale (px per metre) until the whole façade is in it.
import json
import os

import Metashape as M

WALLS = r"C:\temp\walls.json"        # output of the detector (same frame as the exported tie points)
WALL = 0                              # index (`id`) of the wall to build
EPSG = 3942                           # a projected CRS WITHOUT vertical datum (a vertical datum breaks the vertical plane: "Vertical datum out of range")
BUF, DEPTH, PIXEL = 1.0, 2.0, 0.03    # margin on the four sides (m), depth kept in front of and behind the plane (m), ortho pixel (m)
OUT = r"C:\temp\facade_%d.png"

V = M.Vector
doc = M.app.document
c0 = doc.chunk
T = c0.transform.matrix
LF = c0.crs.localframe(T.mulp(c0.region.center))       # world -> local east/north/up, the frame of the exported tie points
LFi = LF.inv()
crs = M.CoordinateSystem("EPSG::%d" % EPSG)
w = [x for x in json.load(open(WALLS)) if x["id"] == WALL][0]


def to_crs(e, n, u):
    return crs.project(LFi.mulp(V((e, n, u))))                  # ENU -> world -> projected (E, N, h)


# which side are the cameras on? the plane normal n must point to them (so the picture is not mirrored)
x0, y0, x1, y1, z0 = w["x0"], w["y0"], w["x1"], w["y1"], w["z0"]
ux, uy = x1 - x0, y1 - y0
L = (ux * ux + uy * uy) ** 0.5
ux, uy = ux / L, uy / L
cams = [LF.mulp(T.mulp(k.center)) for k in c0.cameras if k.transform is not None]
side = sum((q.x - x0) * uy - (q.y - y0) * ux for q in cams) / len(cams)    # n = (uy, -ux, 0)
if side < 0:                                                    # the cameras are on the other side: walk the wall the other way round
    x0, y0, ux, uy = x1, y1, -ux, -uy
H = w["height"]

o = to_crs(x0, y0, z0)
u = to_crs(x0 + ux, y0 + uy, z0) - o
v = to_crs(x0, y0, z0 + 1.0) - o
n = to_crs(x0 + uy, y0 - ux, z0) - o
F = M.Matrix([[u.x, v.x, n.x, o.x], [u.y, v.y, n.y, o.y], [u.z, v.z, n.z, o.z], [0, 0, 0, 1]])
proj = M.OrthoProjection()
proj.type = M.OrthoProjection.Type.Planar
proj.crs = crs
proj.matrix = F.inv()                                           # CRS -> plane frame (x along the wall, y up, z toward the cameras)
box = M.BBox(V((-BUF, -BUF)), V((L + BUF, H + BUF)))            # 2D, in plane coordinates (a 3D box is refused)

c = c0.copy(items=[M.DataSource.TiePointsData], keypoints=False)    # work copy: the user's chunk is never changed
c.label = "FACADE_%d" % WALL
Ti = T.inv()
ch = lambda p: Ti.mulp(crs.unproject(p))                        # projected -> chunk coordinates
oc = ch(o)
eu, ev, en = ch(o + u) - oc, ch(o + v) - oc, ch(o + n) - oc     # wall axes in chunk coordinates (chunk units are not metres)
s = eu.norm()
reg = M.Region()
reg.center = ch(o + u * (L / 2) + v * ((H) / 2))
reg.size = V((L + 2 * BUF, H + 2 * BUF, 2 * DEPTH)) * s
reg.rot = M.Matrix([[eu.x / s, ev.x / s, en.x / s], [eu.y / s, ev.y / s, en.y / s], [eu.z / s, ev.z / s, en.z / s]])
c.region = reg                                                  # depth crop: without it the DEM runs from -10 m to +87 m
c.buildDem(source_data=M.DataSource.TiePointsData, interpolation=M.EnabledInterpolation, projection=proj, region=box, replace_asset=True)
e = c.elevation
print("DEM", e.width, e.height, e.resolution, e.min, e.max)       # expect max - min close to 2 x DEPTH
c.buildOrthomosaic(surface_data=M.DataSource.ElevationData, blending_mode=M.MosaicBlending, fill_holes=True, projection=proj, region=box,
                   resolution=PIXEL, replace_asset=True)
o2 = c.orthomosaic
print("ORTHO", o2.width, o2.height, o2.resolution)

# the picture: show the copy in the Ortho view and capture it (exportRaster writes nothing for a vertical plane)
doc.chunk = c
M.app.update()
ov = M.app.ortho_view
ov.view_mode = M.DataSource.OrthomosaicData
ov.center = V((L / 2.0, H / 2.0))
ov.scale = min(3800.0 / (L + 2 * BUF), 2600.0 / (H + 2 * BUF))
img = ov.captureView(int(ov.scale * (L + 2 * BUF)), int(ov.scale * (H + 2 * BUF)), False, True)
img.save(OUT % WALL)
doc.chunk = c0
print("saved", OUT % WALL, "- delete the chunk %s when done (never save the project)" % c.label)
