import pytest

from minisb.probes import PROBES


@pytest.mark.parametrize("name", sorted(PROBES))
def test_probe_passes_known_answer_check_and_measures(name, tmp_path):
    probe = PROBES[name]()
    probe.setup(tmp_path)
    try:
        assert probe.check()
        assert probe.measure(0.02) > 0
    finally:
        probe.teardown()
