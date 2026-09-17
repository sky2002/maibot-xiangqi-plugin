from pathlib import Path

import pytest

from xiangqi.isolation import IsolationError, cpu_set, plan, topology


def test_reserve_physical_core_including_non_adjacent_smt():
    siblings = {i: {i % 4, i % 4 + 4} for i in range(8)}
    chosen = plan(set(range(8)), siblings)
    assert chosen.engine_cpu == 3
    assert chosen.reserved == {3, 7}
    assert chosen.host == {0, 1, 2, 4, 5, 6}
    assert chosen.host.isdisjoint(chosen.reserved)


def test_restricted_mask_and_explicit_cpu():
    siblings = {2: {2, 10}, 5: {5, 13}, 13: {5, 13}}
    chosen = plan({2, 5, 13}, siblings, 13)
    assert chosen.host == {2} and chosen.reserved == {5, 13}
    with pytest.raises(IsolationError):
        plan({2, 5, 13}, siblings, 0)


def test_single_physical_core_rejected_even_with_two_threads():
    with pytest.raises(IsolationError, match="两个"):
        plan({0, 1}, {0: {0, 1}, 1: {0, 1}})


def test_read_linux_topology(tmp_path):
    directory = tmp_path / "cpu2/topology"
    directory.mkdir(parents=True)
    (directory / "thread_siblings_list").write_text("2,10\n")
    assert topology({2}, tmp_path) == {2: {2, 10}}
    with pytest.raises(OSError):
        topology({3}, Path(tmp_path))


@pytest.mark.parametrize("text", ["", "1-0", "-1", "0-1-2", "1;2", "1,", "999999"])
def test_invalid_cpu_lists_fail_closed(text):
    with pytest.raises(IsolationError):
        cpu_set(text)
