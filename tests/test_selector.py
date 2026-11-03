import pytest

from minisb import selector

DETECTIONS = {"b1": {1, 2}, "b2": {2, 3, 4}, "b3": set(range(1, 11)), "b4": {5}}


def test_coverage_counts_union_of_detected_defects():
    assert selector.coverage(["b1"], DETECTIONS) == pytest.approx(0.2)
    assert selector.coverage(["b2"], DETECTIONS) == pytest.approx(0.3)
    assert selector.coverage(["b1", "b2"], DETECTIONS) == pytest.approx(0.4)
    assert selector.coverage([], DETECTIONS) == 0.0


def test_greedy_prefers_coverage_per_second():
    costs = {"b1": 1.0, "b2": 1.0, "b3": 10.0, "b4": 0.1}
    assert selector.greedy(costs, DETECTIONS, target=0.0) == ["b4", "b2", "b1", "b3"]
    assert selector.greedy({**costs, "b3": 2.0}, DETECTIONS, target=0.0) == ["b4", "b3"]
    assert selector.optimal({**costs, "b3": 2.0}, DETECTIONS, target=0.0) == ["b3"]


def test_greedy_stops_when_risk_is_low_enough():
    costs = {"b1": 1.0, "b2": 1.0, "b3": 100.0, "b4": 1.0}
    assert selector.greedy(costs, DETECTIONS, probability=1.0, target=0.6) == ["b2", "b1"]
    assert selector.greedy(costs, DETECTIONS, probability=0.3, target=0.6) == []


def test_optimal_is_never_more_expensive_than_greedy():
    costs = {"b1": 1.0, "b2": 1.0, "b3": 10.0, "b4": 0.1}
    for target in (0.0, 0.3, 0.6):
        greedy = selector.greedy(costs, DETECTIONS, target=target)
        best = selector.optimal(costs, DETECTIONS, target=target)
        assert sum(costs[n] for n in best) <= sum(costs[n] for n in greedy) + 1e-9
        assert selector.residual_risk(best, DETECTIONS, 1.0) <= target + 1e-9
