import numpy as np
import pytest

from minisb import validator


def test_identical_samples_are_fully_similar():
    sample = [10.0, 11.0, 12.0]
    assert validator.similarity(sample, sample) == pytest.approx(1.0)
    assert validator.one_sided_distance(sample, sample) == pytest.approx(0.0)


def test_shifted_constant_sample_gives_relative_gap():
    assert validator.one_sided_distance([50.0], [100.0]) == pytest.approx(0.5)
    assert validator.one_sided_distance([80.0] * 5, [100.0] * 5) == pytest.approx(0.2)


def test_faster_observation_is_not_penalised():
    assert validator.one_sided_distance([120.0, 130.0], [100.0, 110.0]) == pytest.approx(0.0)
    assert validator.distance([120.0, 130.0], [100.0, 110.0]) > 0


def test_distance_is_symmetric_up_to_scale():
    a, b = [1.0, 2.0, 3.0], [2.0, 3.0, 5.0]
    assert validator.distance(a, b, scale=1.0) == pytest.approx(validator.distance(b, a, scale=1.0))


def test_criteria_excludes_outlier_and_keeps_typical_sample():
    healthy = [[100, 101, 99], [100, 102, 98], [101, 100, 99], [60, 61, 59]]
    center, defects = validator.criteria(healthy, alpha=0.9)
    assert defects == [3]
    assert np.median(center) == pytest.approx(100, abs=1.5)


def test_verdict_flags_only_degraded_sample():
    center = np.array([100.0, 101.0, 99.0, 100.5, 99.5])
    assert not validator.verdict([100.2, 99.8, 100.9, 99.1, 100.0], center, 0.95)["defective"]
    assert validator.verdict([90.0, 91.0, 89.0, 90.5, 89.5], center, 0.95)["defective"]
