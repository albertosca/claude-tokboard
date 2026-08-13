from datetime import datetime

import pytest

import metrics_core as mr

# A Thursday, mid-afternoon. Chosen so "since Monday" spans several days
# and never collapses to a single one.
NOW = datetime(2026, 8, 13, 15, 30, tzinfo=None).astimezone()


def at(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=None).astimezone()


def test_tudo_has_no_window():
    assert mr.resolve_period("tudo", NOW) is None


def test_ontem_is_a_closed_day():
    start, end = mr.resolve_period("ontem", NOW)
    assert start == at(2026, 8, 12)
    assert end == at(2026, 8, 13)


def test_desde_segunda_anchors_to_the_calendar_week():
    start, end = mr.resolve_period("desde-segunda", NOW)
    assert start == at(2026, 8, 10)
    assert end == NOW


def test_desde_segunda_on_a_monday_starts_that_same_morning():
    monday = at(2026, 8, 10, 9)
    start, end = mr.resolve_period("desde-segunda", monday)
    assert start == at(2026, 8, 10)
    assert end == monday


def test_quinze_dias_counts_today_as_one_of_the_fifteen():
    start, end = mr.resolve_period("15-dias", NOW)
    assert start == at(2026, 7, 30)
    assert end == NOW


def test_mes_is_thirty_days_including_today():
    start, end = mr.resolve_period("mes", NOW)
    assert start == at(2026, 7, 15)
    assert end == NOW


def test_unknown_period_is_rejected():
    with pytest.raises(ValueError):
        mr.resolve_period("semana-passada", NOW)


def test_every_period_has_a_portuguese_label():
    assert [p.id for p in mr.PERIODS] == [
        "ontem",
        "desde-segunda",
        "15-dias",
        "mes",
        "tudo",
    ]
    assert all(p.label and p.label == p.label.strip() for p in mr.PERIODS)
