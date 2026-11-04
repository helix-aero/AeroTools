"""Reglages d'affichage des vignettes : bornes, memorisation, calcul des pixels."""

import pytest

from aerotools.tools.fastgcp import imageadjust


def test_values_are_clamped_and_neutral_by_default():
    model = imageadjust.Adjustments()
    assert model.is_neutral() and model.signature() == (0, 0, 0, 0)
    model.set("exposure", 500)
    model.set("sharpness", -20)
    assert model.get("exposure") == 100 and model.get("sharpness") == 0
    model.set("contrast", "abc")
    assert model.get("contrast") == 0
    assert not model.is_neutral()


def test_subscribers_hear_only_real_changes():
    model, heard = imageadjust.Adjustments(), []
    model.subscribe(lambda: heard.append(1))
    assert model.set("contrast", 10) is True
    assert model.set("contrast", 10) is False
    model.reset()
    assert len(heard) == 2 and model.reset() is False and len(heard) == 2


def test_a_failing_subscriber_does_not_break_the_others():
    model, heard = imageadjust.Adjustments(), []

    def broken():
        raise RuntimeError("boom")

    model.subscribe(broken)
    model.subscribe(lambda: heard.append(1))
    model.set("saturation", 5)
    assert heard == [1]


def test_factors_are_neutral_at_zero_and_monotonic():
    assert imageadjust.exposure_gain(0) == 1.0 and imageadjust.exposure_gain(100) == 4.0
    assert imageadjust.contrast_factor(0) == 1.0 and imageadjust.contrast_factor(-100) == 0.0
    assert imageadjust.contrast_factor(100) == 3.0
    assert imageadjust.saturation_factor(-100) == 0.0 and imageadjust.saturation_factor(0) == 1.0
    assert imageadjust.sharpness_amount(0) == 0.0 and imageadjust.sharpness_amount(100) == 2.0


def test_values_are_remembered_between_openings():
    model = imageadjust.Adjustments()
    model.set("exposure", 30)
    model.set("sharpness", 40)
    model.save()
    again = imageadjust.load()
    assert again.get("exposure") == 30 and again.get("sharpness") == 40
    assert again.get("contrast") == 0


def test_a_corrupt_stored_value_falls_back_to_neutral():
    from aerotools import config
    config.put("aigcp", "adj_exposure", "beaucoup")
    config.put("aigcp", "adj_contrast", 9999)
    model = imageadjust.load()
    assert model.get("exposure") == 0 and model.get("contrast") == 100


np = pytest.importorskip("numpy")


def _photo():
    return np.random.RandomState(1).randint(0, 256, size=(24, 32, 3)).astype(np.uint8)


def test_neutral_values_leave_the_pixels_alone():
    photo = _photo()
    assert np.array_equal(imageadjust.adjust_array(photo, {}), photo)


def test_exposure_brightens_and_darkens():
    photo = np.full((4, 4, 3), 100, np.uint8)
    assert imageadjust.adjust_array(photo, {"exposure": 50})[0, 0, 0] == 200
    assert imageadjust.adjust_array(photo, {"exposure": -50})[0, 0, 0] == 50


def test_contrast_pivots_on_mid_grey():
    photo = np.array([[[64, 64, 64], [128, 128, 128], [192, 192, 192]]], np.uint8)
    out = imageadjust.adjust_array(photo, {"contrast": 50})[0, :, 0]
    assert out[0] < 64 and abs(int(out[1]) - 128) <= 1 and out[2] > 192


def test_zero_saturation_gives_grey_and_boost_spreads_the_channels():
    photo = np.array([[[200, 100, 50]]], np.uint8)
    grey = imageadjust.adjust_array(photo, {"saturation": -100})[0, 0]
    assert grey[0] == grey[1] == grey[2]
    vivid = imageadjust.adjust_array(photo, {"saturation": 50})[0, 0]
    assert int(vivid[0]) - int(vivid[2]) > int(photo[0, 0, 0]) - int(photo[0, 0, 2])


def test_sharpness_raises_local_contrast_and_keeps_flat_areas():
    flat = np.full((16, 16, 3), 120, np.uint8)
    assert np.array_equal(imageadjust.adjust_array(flat, {"sharpness": 100}), flat)
    edge = np.zeros((16, 16, 3), np.uint8)
    edge[:, 8:] = 120
    out = imageadjust.adjust_array(edge, {"sharpness": 100})
    assert out[:, 7, 0].max() == 0 and out[:, 8, 0].min() > 120      # depassement de part et d'autre du bord
    assert out.shape == edge.shape and out.dtype == np.uint8


def test_the_input_is_never_modified():
    photo = _photo()
    before = photo.copy()
    imageadjust.adjust_array(photo, {"exposure": 20, "contrast": 20, "saturation": 20, "sharpness": 20})
    assert np.array_equal(photo, before)
