"""Fast Export: the DEM footprint when the tiles on the edge reach past the image (real Metashape DEMs are tiled)."""

import numpy as np

from aerotools.tools.fastexport import footprint, tiffio


class FakeTiff(object):
    """A 300 x 200 DEM stored in 256-pixel tiles: the tiles of the last column and last row are padded past the image."""
    width, height = 300, 200

    def __init__(self, path):
        pass

    def text(self, tag):
        return ""

    def blocks(self):
        for top in (0, 256):
            for left in (0, 256):
                if top >= self.height or left >= self.width:
                    continue
                yield top, left, np.full((256, 256), 5.0, dtype=np.float32)       # valid data everywhere, padding included


def test_padded_edge_tiles_do_not_overflow_the_mask(monkeypatch):
    monkeypatch.setattr(tiffio, "Tiff", FakeTiff)
    mask, step = footprint.dem_mask("fake.tif", target=100)
    assert step == 3 and mask.shape == (67, 100) and mask.all()
