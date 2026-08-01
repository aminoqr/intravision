"""Metric transforms are pure functions, so they're testable without network or quota."""

from datetime import datetime, timedelta, timezone

import pytest

from ft.metrics import (
    active_now,
    active_project_data,
    avg_milestone,
    build_metrics,
    coalition_standings,
    is_blackholed,
    is_validated,
    level_distribution,
    level_ups,
    median_level,
    parse_dt,
    project_popularity,
    recent_validations,
    validations_since,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def pu(login="alice", project="libft", validated=True, minutes_ago=10, mark=100,
       occurrence=0, status="finished"):
    return {
        "occurrence": occurrence,
        "final_mark": mark,
        "status": status,
        "validated?": validated,
        "marked_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
        "project": {"name": project},
        "user": {"id": hash(login) % 1000, "login": login, "displayname": login.title()},
    }


def cu(login="alice", level=3.5, blackholed=None):
    return {
        "level": level,
        "blackholed_at": blackholed,
        "user": {"id": hash(login) % 1000, "login": login, "displayname": login.title()},
    }


class TestBlackhole:
    def test_future_deadline_is_still_active(self):
        future = (NOW + timedelta(days=30)).isoformat()
        assert not is_blackholed(cu(blackholed=future), now=NOW)

    def test_past_deadline_is_absorbed(self):
        past = (NOW - timedelta(days=1)).isoformat()
        assert is_blackholed(cu(blackholed=past), now=NOW)

    def test_null_deadline_is_active(self):
        assert not is_blackholed(cu(blackholed=None), now=NOW)


class TestAvgMilestone:
    def test_mean_of_whole_levels(self):
        # 3.5 → 3, 4.9 → 4, 2.1 → 2  → mean 3.0
        rows = [cu(level=3.5), cu(level=4.9), cu(level=2.1)]
        assert avg_milestone(rows, now=NOW) == 3.0

    def test_excludes_past_blackholes_keeps_future(self):
        past = (NOW - timedelta(days=1)).isoformat()
        future = (NOW + timedelta(days=30)).isoformat()
        rows = [
            cu("gone", level=10.0, blackholed=past),
            cu("safe", level=2.7, blackholed=future),
            cu("open", level=4.2, blackholed=None),
        ]
        # milestones 2 and 4 → mean 3.0
        assert avg_milestone(rows, now=NOW) == 3.0

    def test_empty_is_zero(self):
        assert avg_milestone([], now=NOW) == 0.0


class TestParsing:
    @pytest.mark.parametrize("value", ["2026-08-01T12:00:00.000Z", "2026-08-01T13:00:00+01:00"])
    def test_handles_both_timestamp_formats(self, value):
        assert parse_dt(value) is not None

    def test_bad_input_is_none_not_raise(self):
        assert parse_dt("not a date") is None
        assert parse_dt(None) is None

    def test_ruby_question_mark_key(self):
        assert is_validated({"validated?": True})
        assert is_validated({"validated": True})
        assert not is_validated({})


class TestRecentValidations:
    def test_only_validated_rows(self):
        rows = recent_validations([pu(validated=True), pu(validated=False)], now=NOW)
        assert len(rows) == 1

    def test_newest_first(self):
        rows = recent_validations(
            [pu(project="old", minutes_ago=500), pu(project="new", minutes_ago=1)], now=NOW
        )
        assert [r["project"] for r in rows] == ["new", "old"]

    def test_occurrence_is_one_indexed_for_display(self):
        assert recent_validations([pu(occurrence=2)], now=NOW)[0]["attempt"] == 3

    def test_skips_rows_without_marked_at(self):
        row = pu()
        row["marked_at"] = None
        assert recent_validations([row], now=NOW) == []

    def test_respects_limit(self):
        assert len(recent_validations([pu() for _ in range(30)], limit=5, now=NOW)) == 5


class TestDistribution:
    def test_buckets_by_whole_level(self):
        dist = level_distribution([cu(level=0.5), cu(level=1.2), cu(level=1.9)])
        assert dist[0]["count"] == 1
        assert dist[1]["count"] == 2

    def test_excludes_blackholed(self):
        dist = level_distribution([cu(level=2.0), cu(level=2.0, blackholed="2026-01-01T00:00:00Z")])
        assert sum(d["count"] for d in dist) == 1

    def test_empty_input(self):
        assert level_distribution([]) == []

    def test_median_even_and_odd(self):
        assert median_level([cu(level=1.0), cu(level=3.0), cu(level=5.0)]) == 3.0
        assert median_level([cu(level=1.0), cu(level=3.0)]) == 2.0

    def test_median_of_nothing_is_zero(self):
        assert median_level([]) == 0.0


class TestCoalitions:
    def test_sorted_and_normalised(self):
        rows = coalition_standings(
            [{"name": "B", "score": 50, "color": "#f00"}, {"name": "A", "score": 100}]
        )
        assert [r["name"] for r in rows] == ["A", "B"]
        assert rows[0]["pct"] == 100.0
        assert rows[1]["pct"] == 50.0

    def test_all_zero_scores_do_not_divide_by_zero(self):
        rows = coalition_standings([{"name": "A", "score": 0}, {"name": "B", "score": 0}])
        assert all(r["pct"] == 0.0 for r in rows)


class TestActiveProjectData:
    def test_percentages_use_full_total_and_one_decimal(self):
        rows = [
            pu(project="A", status="in_progress", login="a1"),
            pu(project="A", status="in_progress", login="a2"),
            pu(project="B", status="in_progress", login="b1"),
            pu(project="C", status="finished", login="c1"),
        ]
        nodes = active_project_data(rows)
        assert [n["name"] for n in nodes] == ["A", "B"]
        assert nodes[0]["count"] == 2
        assert nodes[0]["percentage"] == "66.7%"
        assert nodes[1]["percentage"] == "33.3%"
        assert "color" in nodes[0]

    def test_folds_tail_into_others_when_more_than_seven(self):
        rows = []
        # 9 unique in-progress projects, one each
        for i in range(9):
            rows.append(pu(project=f"P{i}", status="in_progress", login=f"u{i}"))
        # Boost first so ordering is stable: P0 has 3, rest 1
        rows.append(pu(project="P0", status="in_progress", login="u0b"))
        rows.append(pu(project="P0", status="in_progress", login="u0c"))

        nodes = active_project_data(rows)
        assert len(nodes) == 8
        assert nodes[-1]["name"] == "Others"
        assert nodes[-1]["count"] == 2  # P7 + P8
        assert sum(n["count"] for n in nodes) == 11
        assert nodes[0]["name"] == "P0"
        assert nodes[0]["count"] == 3

    def test_empty_when_nothing_in_progress(self):
        assert active_project_data([pu(status="finished")]) == []


class TestOther:
    def test_active_now_counts_open_sessions(self):
        assert active_now([{"end_at": None}, {"end_at": "2026-08-01T10:00:00Z"}, {}]) == 2

    def test_popularity_counts_only_in_progress(self):
        rows = project_popularity(
            [pu(project="x", status="in_progress"), pu(project="x", status="in_progress"),
             pu(project="y", status="finished")]
        )
        assert rows == [{"project": "x", "count": 2}]

    def test_validations_since_respects_window(self):
        rows = [pu(minutes_ago=60), pu(minutes_ago=60 * 24 * 30)]
        assert validations_since(rows, days=7, now=NOW) == 1

    def test_level_ups_detects_crossing(self):
        ups = level_ups([cu("alice", 4.1)], [cu("alice", 3.8)])
        assert len(ups) == 1
        assert ups[0]["milestone"] == 4

    def test_no_level_up_within_same_whole_level(self):
        assert level_ups([cu("alice", 3.9)], [cu("alice", 3.1)]) == []

    def test_cold_start_has_no_previous(self):
        assert level_ups([cu("alice", 4.1)], None) == []


class TestBuildMetrics:
    def test_produces_every_key_the_renderer_needs(self):
        m = build_metrics(
            projects_users=[pu()], cursus_users=[cu()],
            coalitions=[{"name": "A", "score": 10}],
            locations=[{"end_at": None}], now=NOW,
        )
        for key in ("pulse", "recent_validations", "project_popularity",
                    "level_distribution", "coalitions", "level_ups",
                    "average_milestone", "active_project_data", "generated_at"):
            assert key in m
        assert "average_level" not in m
        assert "active_projects" not in m

    def test_survives_all_endpoints_failing(self):
        """A refresh where every API call failed must still produce a renderable shape."""
        m = build_metrics(projects_users=[], cursus_users=[], coalitions=[],
                          locations=[], now=NOW)
        assert m["pulse"]["on_campus"] == 0
        assert m["recent_validations"] == []
