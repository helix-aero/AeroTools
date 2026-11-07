"""Aero-L3: the lens calibration written in the DJI XMP (`DewarpData`), as Metashape wants it. Pure Python 3.9.

`DewarpData` = `date;fx,fy,cx,cy,k1,k2,p1,p2,k3`. fx, fy in pixels; cx, cy are the offsets of the principal point from the centre of
the picture, in pixels (the same meaning as Metashape's `cx`, `cy`); the distortion is Brown / OpenCV with k1 k2 k3 radial and two
tangential terms. **The p1 and p2 of DJI are the p2 and p1 of Metashape**: measured on the reference project, where Metashape's initial
calibration holds p1 = 8th value and p2 = 7th value of `DewarpData`.
When `DewarpFlag` is 1 the picture is already undistorted: the camera is a pinhole of focal `CalibratedFocalLength`, principal point at the centre.
"""

from collections import namedtuple

Calibration = namedtuple("Calibration", "width height f cx cy k1 k2 k3 p1 p2 source date")


class CalibrationError(Exception):
    """The XMP has no usable calibration."""


def parse_dewarp(text):
    """(date, fx, fy, cx, cy, k1, k2, p1, p2, k3) of a `DewarpData` string, floats in the order DJI writes them (not Metashape's p1 / p2). Raises ValueError."""
    if not text or ";" not in text:
        raise ValueError("no DewarpData")
    date, _, rest = text.partition(";")
    values = [float(part) for part in rest.split(",") if part.strip() != ""]
    if len(values) != 9:
        raise ValueError("DewarpData has {} numbers instead of 9".format(len(values)))
    return (date.strip(),) + tuple(values)


def calibration_for(width, height, dewarp_flag, dewarp_data, calibrated_focal=None):
    """The initial `Calibration` of a lens. Raises `CalibrationError` when the XMP does not hold what the rule needs."""
    try:
        flag = int(float(dewarp_flag)) if dewarp_flag is not None else 0
    except (TypeError, ValueError):
        flag = 0
    if flag == 1:
        if not calibrated_focal:
            raise CalibrationError("DewarpFlag is 1 but CalibratedFocalLength is missing")
        return Calibration(width, height, float(calibrated_focal), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "ideal", "")
    try:
        date, fx, fy, cx, cy, k1, k2, p1_dji, p2_dji, k3 = parse_dewarp(dewarp_data)
    except ValueError as exc:
        raise CalibrationError(str(exc))
    if fx <= 0:
        raise CalibrationError("the focal length of DewarpData is not positive")
    return Calibration(width, height, fx, cx, cy, k1, k2, k3, p2_dji, p1_dji, "dewarp", date)


def pixel_size_mm(width, focal_mm, focal_35mm):
    """Pixel pitch in millimetres worked out the way Metashape does it, from the 35 mm equivalent (a 36 mm wide full frame), or None."""
    try:
        if not (width and focal_mm and focal_35mm):
            return None
        return 36.0 * float(focal_mm) / float(focal_35mm) / float(width)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def same_calibration(a, b, tol=1e-9):
    """True if two calibrations hold the same numbers."""
    return all(abs(x - y) <= tol for x, y in zip(a[:10], b[:10]))
