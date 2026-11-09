# Third-party notices

AeroTools is released under the licence in [LICENSE](LICENSE). This page lists the files of other authors that this repository
contains or installs, with the licence of each. It lists only what is in this copy.

- [Fonts](#fonts)
- [Icons and logos](#icons-and-logos)
- [AI models](#ai-models)
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

## Software installed on request

The installers can download Python packages from [PyPI](https://pypi.org) and the PyTorch download server for the AI
detection of Aero-GCP: numpy, onnxruntime, opencv-python-headless, torch, torchvision, ultralytics and onnx. They are not part of
this repository. Each one keeps its own licence; the Ultralytics package is under the GNU AGPL-3.0 (see [AI models](#ai-models)).
