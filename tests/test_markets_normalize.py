import json
from pathlib import Path

from polycopy.catalog.markets import normalize_market, rows_to_frame

FIXTURES = Path(__file__).parent / "fixtures"


def _base_market() -> dict:
    return {
        "id": "123",
        "conditionId": "0xABCDEF",
        "slug": "will-x-happen",
        "question": "Will X happen?",
        "clobTokenIds": json.dumps(["111", "222"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["0.4", "0.6"]),
        "negRisk": False,
        "active": True,
        "closed": False,
        "startDate": "2026-01-01T00:00:00Z",
        "endDate": "2026-12-31T00:00:00Z",
        "events": [{"id": 9, "slug": "x-event"}],
        "tags": [{"slug": "politics"}],
    }


def test_open_market():
    row = normalize_market(_base_market())
    assert row is not None
    assert row["condition_id"] == "0xabcdef"  # lowercased
    assert row["yes_token_id"] == "111" and row["no_token_id"] == "222"
    assert row["payout_yes"] is None and row["status"] == "open"
    assert row["category_bucket"] == "politics"


def test_resolved_yes():
    m = _base_market()
    m.update(
        closed=True,
        outcomePrices=json.dumps(["1", "0"]),
        umaResolutionStatus="resolved",
        closedTime="2026-06-01 12:00:00+00",
    )
    row = normalize_market(m)
    assert row["payout_yes"] == 1.0
    assert row["status"] == "resolved"
    assert row["resolved_ts"] is not None


def test_resolved_half():
    m = _base_market()
    m.update(closed=True, outcomePrices=json.dumps(["0.5", "0.5"]))
    assert normalize_market(m)["payout_yes"] == 0.5


def test_closed_without_hard_prices_is_unresolved():
    m = _base_market()
    m.update(closed=True, outcomePrices=json.dumps(["0.97", "0.03"]))
    row = normalize_market(m)
    assert row["payout_yes"] is None
    assert row["status"] == "closed_unresolved"


def test_non_two_token_market_skipped():
    m = _base_market()
    m["clobTokenIds"] = json.dumps(["1"])
    assert normalize_market(m) is None


def test_real_fixture_normalizes():
    markets = json.loads((FIXTURES / "gamma_markets.json").read_text())
    rows = [normalize_market(m) for m in markets]
    rows = [r for r in rows if r]
    assert rows, "fixture contained no 2-token markets"
    df = rows_to_frame(rows)
    assert df["condition_id"].null_count() == 0
