"""Thumbnail display adjustments (exposure, contrast, saturation, sharpness).

Only an aid for the operator's eye: the adjustments apply to the images
shown in the Review panel and in the refinement window, never to the project photos
nor to the crops recorded by the detection. The points stay in photo coordinates,
an adjustment does not move them.

Two layers:
  - `Adjustments`: the four values, their bounds, saving. Without Qt, testable.
  - `apply_image`: applies the values to a QImage with numpy (shipped with Metashape).
    Without numpy, the image comes back unchanged and `available()` returns False.
"""

from ... import config

SECTION = "aigcp"
KEYS = ("exposure", "contrast", "saturation", "sharpness")
RANGES = {"exposure": (-100, 100), "contrast": (-100, 100),
          "saturation": (-100, 100), "sharpness": (0, 100)}
CONFIG_KEYS = {key: "adj_" + key for key in KEYS}


def _clamp(key, value):
    low, high = RANGES[key]
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError):
        return 0 if low <= 0 else low
    return max(low, min(high, value))


class Adjustments(object):
    """The four adjustments, shared between the Review panel and the refinement window."""

    def __init__(self, values=None):
        self.values = {key: 0 for key in KEYS}
        for key, value in (values or {}).items():
            if key in self.values:
                self.values[key] = _clamp(key, value)
        self._subscribers = []

    def get(self, key):
        return self.values[key]

    def set(self, key, value):
        """Change one adjustment. Returns True if it moved; notifies the subscribers."""
        value = _clamp(key, value)
        if self.values[key] == value:
            return False
        self.values[key] = value
        self._notify()
        return True

    def reset(self):
        changed = any(self.values.values())
        for key in KEYS:
            self.values[key] = 0
        if changed:
            self._notify()
        return changed

    def is_neutral(self):
        return not any(self.values.values())

    def signature(self):
        return tuple(self.values[key] for key in KEYS)

    def subscribe(self, callback):
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback):
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def _notify(self):
        for callback in list(self._subscribers):
            try:
                callback()
            except Exception:
                pass

    def save(self):
        for key in KEYS:
            config.put(SECTION, CONFIG_KEYS[key], self.values[key])


def load():
    """Stored adjustments (or neutral ones)."""
    values = {}
    for key in KEYS:
        stored = config.get(SECTION, CONFIG_KEYS[key])
        if isinstance(stored, (int, float)) and not isinstance(stored, bool):
            values[key] = stored
    return Adjustments(values)


# --------------------------------------------------------------------------
# Factor of each adjustment
# --------------------------------------------------------------------------

def exposure_gain(value):
    """Linear gain: -100 -> /4, 0 -> x1, +100 -> x4 (two stops)."""
    return 2.0 ** (value / 50.0)


def contrast_factor(value):
    """Slope around mid grey: -100 -> 0, 0 -> 1, +100 -> 3."""
    return 1.0 + value / 100.0 if value < 0 else 1.0 + value / 50.0


def saturation_factor(value):
    """Mix with the luminance: -100 -> grey, 0 -> unchanged, +100 -> x3."""
    return 1.0 + value / 100.0 if value < 0 else 1.0 + value / 50.0


def sharpness_amount(value):
    """Unsharp mask strength: 0 -> nothing, 100 -> 2."""
    return value / 50.0


# --------------------------------------------------------------------------
# Pixels (numpy)
# --------------------------------------------------------------------------

def available():
    try:
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


def _blur(x):
    """Separable 5x5 binomial blur, repeated edges. x: (H, W, 3) float."""
    import numpy as np
    kernel = (1.0, 4.0, 6.0, 4.0, 1.0)
    h, w = x.shape[0], x.shape[1]
    padded = np.pad(x, ((0, 0), (2, 2), (0, 0)), mode="edge")
    wide = sum(c * padded[:, i:i + w] for i, c in enumerate(kernel)) / 16.0
    padded = np.pad(wide, ((2, 2), (0, 0), (0, 0)), mode="edge")
    return sum(c * padded[i:i + h] for i, c in enumerate(kernel)) / 16.0


def adjust_array(rgb, values):
    """Apply the adjustments to a (H, W, 3) uint8 array; returns a new uint8 array."""
    import numpy as np
    exposure, contrast = values.get("exposure", 0), values.get("contrast", 0)
    saturation, sharpness = values.get("saturation", 0), values.get("sharpness", 0)
    x = rgb.astype(np.float32) / 255.0
    if exposure:
        x *= np.float32(exposure_gain(exposure))
    if contrast:
        x = (x - 0.5) * np.float32(contrast_factor(contrast)) + 0.5
    if saturation:
        luma = x[:, :, 0] * 0.299 + x[:, :, 1] * 0.587 + x[:, :, 2] * 0.114
        x = luma[:, :, None] + (x - luma[:, :, None]) * np.float32(saturation_factor(saturation))
    np.clip(x, 0.0, 1.0, out=x)
    if sharpness:
        x = x + (x - _blur(x)) * np.float32(sharpness_amount(sharpness))
        np.clip(x, 0.0, 1.0, out=x)
    return (x * 255.0 + 0.5).astype(np.uint8)


def apply_image(image, adjustments):
    """QImage adjusted according to `adjustments` (Adjustments or dict); the input is never modified."""
    values = adjustments.values if isinstance(adjustments, Adjustments) else dict(adjustments or {})
    if not any(values.values()) or image is None or image.isNull() or not available():
        return image
    import numpy as np
    from ...qt import QtGui
    source = image.convertToFormat(QtGui.QImage.Format_RGB888)
    width, height, stride = source.width(), source.height(), source.bytesPerLine()
    raw = np.frombuffer(memoryview(source.constBits()), dtype=np.uint8, count=stride * height)
    rgb = raw.reshape(height, stride)[:, :width * 3].reshape(height, width, 3)
    out = np.ascontiguousarray(adjust_array(rgb, values))
    result = QtGui.QImage(out.data, width, height, width * 3, QtGui.QImage.Format_RGB888)
    return result.copy()       # copy: `out` disappears when the function returns
