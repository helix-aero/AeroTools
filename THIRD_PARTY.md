# Third-party notices

AeroTools is released under the licence in [LICENSE](LICENSE). This page lists the files of other authors that this repository
contains or installs, with the licence of each. It lists only what is in this copy.

- [Fonts](#fonts)
- [Icons and logos](#icons-and-logos)
- [AI models](#ai-models)
- [PROJ and the libraries delivered with it](#proj-and-the-libraries-delivered-with-it)
- [Test data](#test-data)
- [Software installed on request](#software-installed-on-request)

## Fonts

Manrope and Rubik, in `assets/fonts/`, are under the SIL Open Font License 1.1. Each font comes with its licence text
(`OFL-Manrope.txt`, `OFL-Rubik.txt`) and its copyright line, see [assets/fonts/NOTICE.txt](assets/fonts/NOTICE.txt).

## Icons and logos

- The GitHub icon at the bottom of the Aero-GCP panel is the `mark-github` icon of [Octicons](https://github.com/primer/octicons),
  drawn from its path in `aerotools/tools/fastgcp/panel.py`. Octicons is under the MIT licence:

  > MIT License
  >
  > Copyright (c) 2026 GitHub Inc.
  >
  > Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation
  > files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy,
  > modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
  > Software is furnished to do so, subject to the following conditions:
  >
  > The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
  > Software.
  >
  > THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
  > WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
  > COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
  > ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

  GitHub and its logo are trademarks of GitHub, Inc.
- The two logos in `assets/logo/` show the HELIX AERO brand. They belong to HELIX AERO.

## AI models

`models/checker.pt` and `models/spray.pt` (PyTorch), and `models/checker.onnx` and `models/spray.onnx` (the same two models
converted to ONNX), are the detection models called "Aero V1.0" in Aero-GCP. They are YOLO11n models that HELIX AERO
fine-tuned from the `yolo11n.pt` weights of Ultralytics, with the Ultralytics training code.

- The files carry the metadata written by Ultralytics: author "Ultralytics", licence "AGPL-3.0 License
  (https://ultralytics.com/license)".
- Ultralytics offers YOLO11 under two licences: the GNU AGPL-3.0, or the Ultralytics Enterprise License
  (see [ultralytics.com/license](https://www.ultralytics.com/license)).
- The images used for the fine-tuning are not part of this repository.

Read the Ultralytics licence before you reuse these weights outside AeroTools. The same notice sits next to the files, in
`models/NOTICE.txt`.

## PROJ and the libraries delivered with it

Aero-Exports converts coordinates and heights with the PROJ programs in `aerotools/vendor/proj/`. The programs, their
libraries (all in `aerotools/vendor/proj/bin/`) and the licence text of each one, copied from the upstream project at the
version below, are listed here. The texts are in [aerotools/vendor/proj/licenses/](aerotools/vendor/proj/licenses).

| Component | Version | Files | Licence | Licence text |
|---|---|---|---|---|
| [PROJ](https://proj.org) | 9.8.1 | `proj_9.dll`, `cs2cs.exe`, `cct.exe` | MIT style licence, which PROJ applies to "all source, data files and other contents" of its package | [PROJ.txt](aerotools/vendor/proj/licenses/PROJ.txt) |
| [OpenSSL](https://www.openssl.org) | 3.0.20 | `libcrypto-3-x64.dll`, `libssl-3-x64.dll` | Apache License 2.0, Copyright 1998-2026 The OpenSSL Authors | [OpenSSL.txt](aerotools/vendor/proj/licenses/OpenSSL.txt) |
| [libcurl](https://curl.se) | 8.20.0 | `libcurl.dll` | curl licence, Copyright (c) 1996 - 2026, Daniel Stenberg and many contributors | [curl.txt](aerotools/vendor/proj/licenses/curl.txt) |
| [LibTIFF](https://libtiff.gitlab.io/libtiff/) | 4.7.1 | `tiff.dll` | LibTIFF licence, Copyright (c) 1988-1997 Sam Leffler, Copyright (c) 1991-1997 Silicon Graphics, Inc. | [libtiff.txt](aerotools/vendor/proj/licenses/libtiff.txt) |
| [libjpeg-turbo](https://libjpeg-turbo.org) | libjpeg API 6.2 | `jpeg62.dll` | IJG License for the libjpeg API, Modified BSD and zlib licences for other parts. This software is based in part on the work of the Independent JPEG Group. | [libjpeg-turbo.txt](aerotools/vendor/proj/licenses/libjpeg-turbo.txt), [libjpeg-turbo-README.ijg.txt](aerotools/vendor/proj/licenses/libjpeg-turbo-README.ijg.txt) |
| [zlib](https://zlib.net) | 1.3.2 | `zlib.dll` | zlib licence, Copyright (C) 1995-2026 Jean-loup Gailly and Mark Adler | [zlib.txt](aerotools/vendor/proj/licenses/zlib.txt) |
| [Zstandard](https://facebook.github.io/zstd/) | 1.5.7 | `zstd.dll` | Dual licence BSD or GPLv2, used here under the BSD licence, Copyright (c) Meta Platforms, Inc. and affiliates | [zstd.txt](aerotools/vendor/proj/licenses/zstd.txt) |
| [liblzma (XZ Utils)](https://tukaani.org/xz/) | 5.4.7 | `liblzma.dll` | Public domain | [xz-liblzma.txt](aerotools/vendor/proj/licenses/xz-liblzma.txt) |
| [Brotli](https://github.com/google/brotli) | 1.2.0 | `brotlicommon.dll`, `brotlidec.dll` | MIT licence, Copyright (c) 2009, 2010, 2013-2016 by the Brotli Authors | [brotli.txt](aerotools/vendor/proj/licenses/brotli.txt) |
| [libwebp and libsharpyuv](https://chromium.googlesource.com/webm/libwebp) | libwebp 1.6.0 | `libwebp.dll`, `libsharpyuv.dll` | BSD 3-clause licence, Copyright (c) 2010, Google Inc., with the WebM patent grant | [libwebp.txt](aerotools/vendor/proj/licenses/libwebp.txt), [libwebp-PATENTS.txt](aerotools/vendor/proj/licenses/libwebp-PATENTS.txt) |
| [LERC](https://github.com/Esri/lerc) | not stated in the file | `lerc.dll` | Apache License 2.0 | [lerc.txt](aerotools/vendor/proj/licenses/lerc.txt) |
| [SQLite](https://sqlite.org) | 3.53.0 | `sqlite3.dll` | Public domain | [sqlite3.txt](aerotools/vendor/proj/licenses/sqlite3.txt) |

**The coordinate database.** `aerotools/vendor/proj/share/proj/proj.db` is the PROJ database. Its metadata table lists the
registries it holds: EPSG v12.029 (2025-10-02), ESRI (ArcGIS Pro 3.6), IGNF 3.1.0, NKG 1.0.w, IAU_2015, OGC and PROJ itself.

The EPSG Dataset is owned by the International Association of Oil and Gas Producers (IOGP). It may be used, copied and
distributed under the [EPSG Dataset Terms of Use](https://epsg.org/terms-of-use.html). Among other conditions, those terms ask
that the ownership of the dataset by IOGP be acknowledged, that recipients be told of the terms, and that the data is
provided "as is" without warranty. They say the dataset is published at no charge and that distribution for profit is
forbidden. Read them before you pass `proj.db` on.

The `proj/data/` folder next to it (`files.geojson`, `grid_ops.json`) is the catalogue of the PROJ grids. The grids themselves
are not in this repository: when a height conversion needs one, Aero-Exports downloads it once, on request, from the PROJ
grid server (`cdn.proj.org`), where each grid keeps the licence its publisher chose.

## Test data

- `tests/data/fr_ign_RAC23.tif` is the height grid RGF93 v2b to NGF-IGN78 (RAC23) for Corsica, converted from the file
  `RAC23.mnt`. The metadata of the file say "Derived from work by IGN France" and give the Licence Ouverte (Open Licence) of
  Etalab as its licence: https://www.etalab.gouv.fr/wp-content/uploads/2014/05/Open_Licence.pdf. The grid is used by the tests
  of Aero-Exports only.

## Software installed on request

The installers can download Python packages from [PyPI](https://pypi.org) and the PyTorch download server for the AI
detection of Aero-GCP: numpy, onnxruntime, opencv-python-headless, torch, torchvision, ultralytics and onnx. They are not part of
this repository. Each one keeps its own licence; the Ultralytics package is under the GNU AGPL-3.0 (see [AI models](#ai-models)).
