import metrics_core as mr


def record(project, tokens, start):
    return {
        "project_path": project,
        "timestamp_start": start,
        "cache_read_tokens": tokens,
    }


def test_projects_are_ordered_by_spend_not_by_recency():
    # "cheap" was touched most recently; "expensive" burned 100x more.
    # The panel is titled "Total de tokens por projeto" and its job is
    # to answer which project is burning tokens, so spend wins.
    records = [
        record("/p/expensive", 1_000_000, "2026-08-01T10:00:00Z"),
        record("/p/cheap", 10_000, "2026-08-12T10:00:00Z"),
    ]
    ordered, totals, max_total = mr.compute_project_totals(
        records, mr.compute_project_last_seen(records)
    )
    assert ordered == ["/p/expensive", "/p/cheap"]
    assert totals["/p/expensive"] == 1_000_000
    assert max_total == 1_000_000


def test_equal_spend_falls_back_to_most_recent():
    records = [
        record("/p/older", 500, "2026-08-01T10:00:00Z"),
        record("/p/newer", 500, "2026-08-12T10:00:00Z"),
    ]
    ordered, _, _ = mr.compute_project_totals(
        records, mr.compute_project_last_seen(records)
    )
    assert ordered == ["/p/newer", "/p/older"]


def test_no_records_gives_zero_max_and_no_crash():
    ordered, totals, max_total = mr.compute_project_totals([], {})
    assert ordered == []
    assert totals == {}
    assert max_total == 0
