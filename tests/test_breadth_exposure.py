import math

from strategy.breadth_exposure import breadth_exposure_multiplier


def test_breadth_exposure_buckets():
    assert breadth_exposure_multiplier(80) == 1.0
    assert breadth_exposure_multiplier(60) == 1.0
    assert breadth_exposure_multiplier(59.999) == 0.5
    assert breadth_exposure_multiplier(40) == 0.5
    assert breadth_exposure_multiplier(39.999) == 0.0


def test_breadth_exposure_missing_is_fail_closed():
    assert breadth_exposure_multiplier(None) == 0.0
    assert breadth_exposure_multiplier("bad") == 0.0
    assert breadth_exposure_multiplier(math.nan) == 0.0


def test_breadth_exposure_does_not_use_change_10d():
    # The frozen V3 policy is based on breadth level only.
    assert breadth_exposure_multiplier(55) == 0.5
