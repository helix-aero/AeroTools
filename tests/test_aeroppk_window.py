"""Aero-PPK window: one fixed size, opened centred on the screen of the Metashape main window, never left under the taskbar.
The rules of `controls` (size, screen choice, centring, clamping) on made-up screens, with the taskbar at the bottom, on the left, on
top and on the right, a 1366 x 768 laptop, two screens side by side and one on the left of the primary one. Pure functions: tuples
(x, y, width, height), no Qt. The window itself (frame, moves, screens that change) is tested by tests_qt/run_qt_aeroppk_window.py."""
import itertools

import pytest

from aerotools import controls

DESIGN = (1180, 780)
FRAME = (16, 39)                                   # borders and title bar of a Windows 10 window

# the usable area (x, y, w, h) a screen leaves once the taskbar (40 px, 48 px wide on the left) is taken away
FULL_HD = {"bottom": (0, 0, 1920, 1040), "top": (0, 40, 1920, 1040), "left": (48, 0, 1872, 1080), "right": (0, 0, 1872, 1080)}
LAPTOP = {"bottom": (0, 0, 1366, 728), "top": (0, 40, 1366, 728), "left": (48, 0, 1318, 768), "right": (0, 0, 1318, 768)}


def frame_of(size, extra=FRAME):
    return (size[0] + extra[0], size[1] + extra[1])


@pytest.mark.parametrize("taskbar", ["bottom", "top", "left", "right"])
def test_a_full_hd_screen_gets_the_design_size(taskbar):
    assert controls.fit_size(DESIGN, FULL_HD[taskbar]) == DESIGN


def test_a_laptop_screen_gets_less_height_than_the_design_and_never_more_than_it_has():
    assert controls.fit_size(DESIGN, LAPTOP["bottom"]) == (1180, 728 - 39 - 8)
    assert controls.fit_size(DESIGN, LAPTOP["top"]) == (1180, 681)
    width, height = controls.fit_size(DESIGN, LAPTOP["left"])
    assert (width, height) == (1180, 768 - 39 - 8) and width + FRAME[0] <= 1318


def test_a_small_screen_gives_a_small_window_and_a_tiny_one_never_a_negative_size():
    assert controls.fit_size(DESIGN, (0, 0, 1024, 728)) == (1024 - 16 - 8, 681)
    assert controls.fit_size(DESIGN, (0, 0, 100, 100)) == (200, 200)


@pytest.mark.parametrize("taskbar", ["bottom", "top", "left", "right"])
@pytest.mark.parametrize("screen", [FULL_HD, LAPTOP])
def test_the_window_centred_on_a_screen_has_its_whole_frame_inside_the_usable_area(screen, taskbar):
    area = screen[taskbar]
    size = frame_of(controls.fit_size(DESIGN, area))
    x, y = controls.centred_in(size, area)
    assert area[0] <= x and x + size[0] <= area[0] + area[2]
    assert area[1] <= y and y + size[1] <= area[1] + area[3]               # the bottom edge is above the taskbar, whichever side it is on


def test_centring_puts_the_window_in_the_middle_when_there_is_room():
    assert controls.centred_in((1196, 819), (0, 0, 1920, 1040)) == (362, 110)
    assert controls.centred_in((1196, 819), (1920, 0, 1920, 1040)) == (1920 + 362, 110)       # on the second screen
    assert controls.centred_in((1196, 819), (-1920, 0, 1920, 1040)) == (-1920 + 362, 110)     # on a screen left of the primary one


def test_a_window_dropped_under_the_taskbar_is_moved_up_just_enough():
    area = LAPTOP["bottom"]
    frame = (200, 500, 1196, 700)                                           # its bottom edge is at 1200, the area ends at 728
    assert controls.clamp_into(frame, area) == (170, 28)
    assert controls.clamp_into((200, 20, 800, 600), area) == (200, 20)      # a window that is in place is left alone


def test_a_window_with_its_title_bar_above_the_area_is_moved_down_and_one_off_to_the_side_is_moved_in():
    area = LAPTOP["top"]                                                    # taskbar on top: the area starts at y = 40
    assert controls.clamp_into((10, 0, 800, 600), area) == (10, 40)
    assert controls.clamp_into((-300, 100, 800, 600), area) == (0, 100)
    assert controls.clamp_into((1000, 100, 800, 600), area) == (566, 100)


def test_a_window_larger_than_its_area_is_aligned_on_the_top_left_where_the_title_bar_is():
    assert controls.clamp_into((500, 500, 1300, 900), (0, 0, 1024, 700)) == (0, 0)
    assert controls.clamp_into((500, 500, 1300, 900), (48, 40, 1000, 600)) == (48, 40)


def test_the_screen_is_the_one_that_holds_the_centre_of_the_window():
    areas = [(0, 0, 1920, 1040), (1920, 0, 1920, 1040), (-1366, 0, 1366, 728)]
    assert controls.area_for((100, 100, 1196, 819), areas) == 0
    assert controls.area_for((1200, 100, 1196, 819), areas) == 0                 # centre at x = 1798, still on the first screen
    assert controls.area_for((1400, 100, 1196, 819), areas) == 1                 # centre at x = 1998, on the second screen
    assert controls.area_for((-1300, 50, 1196, 600), areas) == 2
    assert controls.area_for((100, 100, 10, 10), []) == 0


def test_a_window_whose_centre_is_off_every_screen_goes_to_the_nearest_one():
    areas = [(0, 0, 1920, 1040), (1920, 0, 1920, 1040)]
    assert controls.area_for((4000, 100, 400, 400), areas) == 1
    assert controls.area_for((-3000, 100, 400, 400), areas) == 0
    assert controls.area_for((100, 5000, 400, 400), areas) == 0


def test_the_main_window_on_the_second_screen_decides_where_the_window_opens():
    areas = [(0, 0, 1366, 728), (1366, 0, 1920, 1040)]                       # laptop screen, then a bigger monitor
    host = (1500, 80, 1400, 900)                                              # Metashape is on the monitor
    area = areas[controls.area_for(host, areas)]
    assert area == areas[1]
    size = frame_of(controls.fit_size(DESIGN, area))
    assert size == (1196, 819)                                                 # the design size: the monitor is big enough
    assert controls.centred_in(size, area) == (1366 + (1920 - 1196) // 2, 110)
    host_on_laptop = (50, 20, 1200, 700)
    area = areas[controls.area_for(host_on_laptop, areas)]
    assert controls.fit_size(DESIGN, area) == (1180, 681)                      # and the same window is smaller on the laptop


@pytest.mark.parametrize("area", [(0, 0, 1920, 1040), (0, 40, 1366, 728), (48, 0, 1318, 768), (1920, 0, 1280, 984), (-1366, 112, 1366, 728)])
def test_whatever_the_screen_and_the_drop_the_clamped_frame_is_inside_the_area_when_it_fits(area):
    size = frame_of(controls.fit_size(DESIGN, area))
    for dx, dy in itertools.product((-2000, -300, 0, 400, 3000), repeat=2):
        x, y = controls.clamp_into((area[0] + dx, area[1] + dy, size[0], size[1]), area)
        assert area[0] <= x and x + size[0] <= area[0] + area[2]
        assert area[1] <= y and y + size[1] <= area[1] + area[3]
