# SPDX-License-Identifier: GPL-3.0-only
"""Tap and swipe recognition, including a digitiser that drops the finger mid-drag."""
import pytest
from wigix.device import Touch
from wigix.engine import Gesture


def run(samples, swipe=True):
    """samples: [(t, down, x, y)]; returns the events in order. Time keeps passing after the last sample."""
    g, out = Gesture(), []
    for t, down, x, y in samples:
        out += g.feed(down, x, y, t, swipe)
    out += g.tick(samples[-1][0] + 1.0)
    return out


def test_a_short_still_touch_is_a_tap_once_the_lift_is_confirmed():
    g = Gesture()
    assert g.feed(True, 500, 300, 0.00) == [] and g.feed(True, 503, 301, 0.05) == []
    assert g.feed(False, 503, 301, 0.08) == []                 # not yet: the finger may come back
    assert g.tick(0.12) == []
    assert g.tick(0.20) == [("tap", 500, 300)]
    assert g.tick(5.0) == []                                   # reported once


def test_a_clear_horizontal_drag_swipes_before_the_finger_lifts_and_never_taps():
    ev = run([(0.00, True, 600, 300), (0.03, True, 560, 302), (0.06, True, 520, 305), (0.09, True, 480, 303),
              (0.12, False, 480, 303)])
    assert ev == [("swipe", "next_page")]
    assert run([(0.0, True, 200, 300), (0.05, True, 300, 305), (0.1, False, 300, 305)]) == [("swipe", "prev_page")]


def test_a_lift_reported_in_the_middle_of_a_drag_does_not_become_a_tap():
    """The bug: a dropped sample read as a release, so a swipe that had barely started tapped (and zoomed in) the card."""
    ev = run([(0.00, True, 600, 300), (0.02, True, 596, 300), (0.03, False, 596, 300),     # spurious lift
              (0.08, True, 540, 304), (0.11, True, 500, 306), (0.14, False, 500, 306)])
    assert ev == [("swipe", "next_page")]


def test_two_separate_touches_are_two_taps():
    ev = run([(0.0, True, 100, 100), (0.05, False, 100, 100), (0.5, True, 400, 400), (0.55, False, 400, 400)])
    assert ev == [("tap", 100, 100), ("tap", 400, 400)]


def test_wandering_off_and_back_is_not_a_tap():
    assert run([(0.0, True, 300, 300), (0.1, True, 345, 300), (0.2, True, 302, 300), (0.25, False, 302, 300)], swipe=False) == []
    assert run([(0.0, True, 300, 300), (0.1, True, 360, 300), (0.15, False, 360, 300)], swipe=False) == []   # swiping disabled: nothing


def test_a_long_press_is_not_a_tap_and_a_vertical_drag_is_not_a_swipe():
    assert run([(0.0, True, 300, 300), (1.4, True, 301, 300), (1.45, False, 301, 300)]) == []
    assert run([(0.0, True, 300, 100), (0.1, True, 310, 300), (0.2, False, 310, 300)]) == []


def test_swipe_fires_once_per_touch():
    g = Gesture()
    ev = []
    for i, x in enumerate((600, 540, 480, 420, 360, 300)):
        ev += g.feed(True, x, 300, i * 0.03)
    assert ev == [("swipe", "next_page")]


@pytest.mark.parametrize("tag", [0xEA, 0xED])
def test_touch_reports_with_any_tag(tag):
    from wigix.device import WigiDash
    d = WigiDash.__new__(WigiDash)
    d._last_touch = Touch(False, 0, 0)
    reports = iter([bytes([1, tag, 0xAE, 0x02, 0x7C, 0x01, 0, 0]), bytes([2, tag, 0xAE, 0x02, 0x7C, 0x01, 0, 0]),
                    bytes([0, tag, 0xAE, 0x02, 0x7C, 0x01, 0, 0]), bytes(8)])
    d._read = lambda *a: next(reports)
    down, lift = d.touch(), d.touch()
    assert (down.down, down.x, down.y) == (True, 0x2AE, 0x17C) and lift.down is False
    assert d.touch() is None and d.touch() is None
