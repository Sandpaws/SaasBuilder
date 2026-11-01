import math

import pytest

from painpoint.config import load_config
from painpoint.scoring import DAY, recency_weight, score_cluster

NOW = 1_800_000_000


def member(i, age_days=1, thread=None, urgency=3, budget="none", **kw):
    m = {
        "post_id": f"p{i}", "thread_id": thread or f"t{i}", "created_utc": NOW - age_days * DAY, "urgency": urgency,
        "budget_signal": budget, "community": "r/SaaS", "competitors": [], "workaround": None, "persona": "unknown",
        "quote": f"quote {i}", "url": f"https://x.test/{i}", "upvotes": 3,
    }
    m.update(kw)
    return m


def cluster(members, check=None):
    return {"label": "a problem", "category": "other", "members": members, "check": check}


@pytest.fixture
def cfg():
    c = load_config()
    c["scoring"]["half_life_days"] = 0  # disable recency unless a test turns it on
    return c


def test_recency_weight_halves_at_half_life():
    assert recency_weight(0, 90) == 1.0
    assert recency_weight(90, 90) == pytest.approx(0.5)
    assert recency_weight(180, 90) == pytest.approx(0.25)
    assert recency_weight(500, 0) == 1.0  # disabled


def test_repetition_anchors(cfg):
    one = score_cluster(cluster([member(0)]), cfg, NOW, 1)
    assert one["repetition"] == pytest.approx(10 * math.log10(2) / math.log10(101), abs=0.01)  # ~1.5
    hundred = score_cluster(cluster([member(i) for i in range(100)]), cfg, NOW, 100)
    assert hundred["repetition"] == 10.0
    many = score_cluster(cluster([member(i) for i in range(500)]), cfg, NOW, 500)
    assert many["repetition"] == 10.0  # capped


def test_repetition_is_monotonic_in_threads(cfg):
    scores = [score_cluster(cluster([member(i) for i in range(n)]), cfg, NOW, n)["repetition"] for n in (1, 3, 10, 30)]
    assert scores == sorted(scores) and len(set(scores)) == 4


def test_extra_comments_in_one_thread_count_less_than_new_threads(cfg):
    same_thread = [member(i, thread="t0") for i in range(5)]
    separate = [member(i) for i in range(5)]
    a = score_cluster(cluster(same_thread), cfg, NOW, 1)
    b = score_cluster(cluster(separate), cfg, NOW, 5)
    assert a["threads"] == 1 and b["threads"] == 5
    assert a["repetition"] < b["repetition"]
    assert a["mentions"] == b["mentions"] == 5


def test_recency_lowers_repetition(cfg):
    cfg["scoring"]["half_life_days"] = 90
    fresh = score_cluster(cluster([member(i, age_days=1) for i in range(10)]), cfg, NOW, 10)
    stale = score_cluster(cluster([member(i, age_days=360) for i in range(10)]), cfg, NOW, 10)
    assert stale["repetition"] < fresh["repetition"] / 2


def test_intensity_and_wtp(cfg):
    low = score_cluster(cluster([member(0, urgency=1)]), cfg, NOW, 1)
    high = score_cluster(cluster([member(0, urgency=5)]), cfg, NOW, 1)
    assert low["intensity"] == 0 and high["intensity"] == 10
    pay = score_cluster(cluster([member(i, budget="willing_to_pay") for i in range(4)]), cfg, NOW, 4)
    none = score_cluster(cluster([member(i) for i in range(4)]), cfg, NOW, 4)
    assert pay["wtp"] == 10 and none["wtp"] == 0 and pay["budget_share"] == 1.0
    partial = score_cluster(cluster([member(0, budget="price_complaint"), member(1), member(2), member(3)]), cfg, NOW, 4)
    assert 0 < partial["wtp"] < 10


def test_opportunity_unchecked_uses_neutral_gap_and_flags_it(cfg):
    s = score_cluster(cluster([member(0)]), cfg, NOW, 1)
    assert s["competition_checked"] is False and s["competition_gap"] == 5.0 and s["saturation"] is None


def test_opportunity_rewards_less_saturated_markets(cfg):
    ms = [member(i, urgency=4) for i in range(6)]
    empty = score_cluster(cluster(ms, {"status": "ok", "saturation": 1, "solutions": []}), cfg, NOW, 6)
    crowded = score_cluster(cluster(ms, {"status": "ok", "saturation": 9, "solutions": []}), cfg, NOW, 6)
    assert empty["competition_checked"] and empty["opportunity"] > crowded["opportunity"]
    w = cfg["scoring"]["weights"]
    expected = (w["repetition"] * empty["repetition"] + w["intensity"] * empty["intensity"]
                + w["wtp"] * empty["wtp"] + w["competition_gap"] * 9) / sum(w.values())
    assert empty["opportunity"] == pytest.approx(expected, abs=0.01)


def test_failed_check_is_treated_as_unchecked(cfg):
    s = score_cluster(cluster([member(0)], {"status": "no_results", "saturation": None, "solutions": []}), cfg, NOW, 1)
    assert s["competition_checked"] is False


@pytest.mark.parametrize("ages,expected", [
    ([100], "older"),
    ([5], "new"),
    ([5, 6, 7, 40], "rising"),
    ([5, 40, 45, 50], "falling"),
    ([5, 40], "steady"),
])
def test_trend(cfg, ages, expected):
    s = score_cluster(cluster([member(i, age_days=a) for i, a in enumerate(ages)]), cfg, NOW, len(ages))
    assert s["trend"] == expected


def test_evidence_levels(cfg):
    weak = score_cluster(cluster([member(0)]), cfg, NOW, 1)
    moderate = score_cluster(cluster([member(i) for i in range(4)]), cfg, NOW, 4)
    strong = score_cluster(cluster([member(i, community="r/a" if i % 2 else "r/b") for i in range(10)]), cfg, NOW, 10)
    assert (weak["evidence"], moderate["evidence"], strong["evidence"]) == ("weak", "moderate", "strong")


def test_competitors_and_quotes_are_aggregated(cfg):
    ms = [member(0, competitors=["QuickBooks"], urgency=5), member(1, competitors=["quickbooks", "Xero"]), member(2)]
    s = score_cluster(cluster(ms), cfg, NOW, 3)
    assert s["competitors_named"][0] == ("QuickBooks", 2)
    assert s["quotes"][0]["text"] == "quote 0"  # highest urgency first
