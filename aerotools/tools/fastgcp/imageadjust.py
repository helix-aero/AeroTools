"""Reglages d'affichage des vignettes (exposition, contraste, saturation, nettete).

Uniquement une aide a l'oeil de l'operateur : les reglages s'appliquent aux images
montrees dans le Review panel et dans la fenetre d'affinage, jamais aux photos du projet
ni aux recadrages enregistres par la detection. Les points restent en coordonnees photo,
un reglage ne les deplace pas.

Deux couches :
  - `Adjustments` : les quatre valeurs, leurs bornes, la sauvegarde. Sans Qt, testable.
  - `apply_image` : applique les valeurs a une QImage avec numpy (livre avec Metashape).
    Sans numpy, l'image revient telle quelle et `available()` renvoie False.
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
    """Les quatre reglages, partages entre le Review panel et la fenetre d'affinage."""

    def __init__(self, values=None):
        self.values = {key: 0 for key in KEYS}
        for key, value in (values or {}).items():
            if key in self.values:
                self.values[key] = _clamp(key, value)
        self._subscribers = []

    def get(self, key):
        return self.values[key]

    def set(self, key, value):
        """Change un reglage. Renvoie True s'il a bouge ; previent les abonnes."""
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
    """Reglages memorises (ou neutres)."""
    values = {}
    for key in KEYS:
        stored = config.get(SECTION, CONFIG_KEYS[key])
        if isinstance(stored, (int, float)) and not isinstance(stored, bool):
            values[key] = stored
    return Adjustments(values)


# --------------------------------------------------------------------------
# Facteurs de chaque reglage
# --------------------------------------------------------------------------

def exposure_gain(value):
    """Gain lineaire : -100 -> /4, 0 -> x1, +100 -> x4 (deux diaphragmes)."""
    return 2.0 ** (value / 50.0)


def contrast_factor(value):
    """Pente autour du gris moyen : -100 -> 0, 0 -> 1, +100 -> 3."""
    return 1.0 + value / 100.0 if value < 0 else 1.0 + value / 50.0


def saturation_factor(value):
    """Melange avec la luminance : -100 -> gris, 0 -> inchange, +100 -> x3."""
    return 1.0 + value / 100.0 if value < 0 else 1.0 + value / 50.0


def sharpness_amount(value):
    """Force du masque flou : 0 -> rien, 100 -> 2."""
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
    """Flou binomial 5x5 separable, bords repetes. x : (H, W, 3) float."""
    import numpy as np
    kernel = (1.0, 4.0, 6.0, 4.0, 1.0)
    h, w = x.shape[0], x.shape[1]
    padded = np.pad(x, ((0, 0), (2, 2), (0, 0)), mode="edge")
    wide = sum(c * padded[:, i:i + w] for i, c in enumerate(kernel)) / 16.0
    padded = np.pad(wide, ((2, 2), (0, 0), (0, 0)), mode="edge")
    return sum(c * padded[i:i + h] for i, c in enumerate(kernel)) / 16.0


def adjust_array(rgb, values):
    """Applique les reglages a un tableau (H, W, 3) uint8 ; renvoie un tableau uint8 neuf."""
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
    """QImage reglee d'apres `adjustments` (Adjustments ou dict) ; l'entree n'est jamais modifiee."""
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
    return result.copy()       # copie : `out` disparait a la sortie de la fonction
