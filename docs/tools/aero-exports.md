# Aero-Exports: export the ortho and the DEM

**Aero > Aero-Exports** writes the orthomosaic and/or the DEM of the open project to an `Outputs` folder next to the `.psx`
(save the project first). Tick **Ortho** and/or **DEM**, pick a **Format** and, if you want one, a pixel size in cm. The window
shows each raster's native pixel; a size you type is exact, and the ortho and the DEM then share the same grid. Files get short
names (site, date, pixel, type: `SITE_260704_3cm_ortho.tif`, `SITE_260704_3cm_dtm.tif`). If the same export already exists you
choose between overwriting it and a new version (`_v2`).

- **Formats.** Full quality GeoTIFF; a compressed *client quick view* (GeoTIFF with JPEG compression, about 10 times smaller, the
  DEM stays lossless); JPEG + world file; JPEG 2000 (written with QGIS or Global Mapper); ECW (written by Global Mapper, which
  must be installed); and an **AutoCAD drawing**, DXF or DWG (DWG needs AutoCAD, which converts the DXF in the background).
- **AutoCAD drawing.** `TOPO.dxf` (or `.dwg`) holds the ortho and the DEM in two layers. The image files sit in `00_Assets` and
  are linked with relative paths, so the folder can be moved or sent as it is. **Crop white background** (on by default) makes
  AutoCAD show only the real data area of each image; the image files themselves are not changed.
- **DEM colours and shading.** **Colours and shading...** opens a window with a live preview of the DEM picture: palette, light
  (sun, four lights, slope), light direction and height, strength and colour range. The drawing uses the look you keep, and the
  setting is saved on this computer. Next to a JPEG 2000 or ECW export you can also save that coloured picture.
- **Coordinates and heights.** **Export in** converts to another coordinate system (search by name or EPSG code), and **Heights**
  to another height system. The heights are computed by AeroTools from the geoid grid, which it downloads once on request from
  the official PROJ grid server; a raster in degrees is switched to a metric system, because a pixel in cm needs metres.
- **Which raster.** A chunk can hold several DEMs and orthomosaics; Aero-Exports always exports the **default** one (the one in normal
  font in the Workspace pane; double-click another to make it the default). Each card says which one it is (size and pixel), and warns
  when the default DEM is much coarser than the ortho. The window follows the active chunk by itself, and its first line names the chunk.
- **Boundary shapes.** Metashape cuts an export to the chunk's boundary shapes. When the chunk has some, a tick box **Clip to the chunk's
  boundary shapes** appears (off by default: the whole area of the DEM is exported, as you see it in Metashape); tick it to cut the export to the shapes.
- **Tiles.** **Split into tiles** cuts the export into square tiles of a chosen size, one file each; empty tiles are skipped.

Not checked by hand yet: the clip and the speed of very large images in the AutoCAD window itself. They were tested with
AutoCAD's console engine only.
