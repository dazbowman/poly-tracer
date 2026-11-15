"""Map Gamma tags (and market text) to the fixed category buckets of SPEC §9 Phase 1.

Buckets: politics, geopolitics, sports, esports, crypto, crypto_short,
economics_finance, tech_ai, culture, weather, other.

crypto_short (5/15/60-minute up/down latency games, excluded from signals by
default) is detected from the market itself, not tags.
"""

from __future__ import annotations

import re

BUCKETS = (
    "politics",
    "geopolitics",
    "sports",
    "esports",
    "crypto",
    "crypto_short",
    "economics_finance",
    "tech_ai",
    "culture",
    "weather",
    "other",
)

# tag slug -> bucket; first match in priority order wins
_TAG_RULES: list[tuple[str, set[str]]] = [
    (
        "esports",
        {"esports", "league-of-legends", "counter-strike", "cs2", "dota-2", "valorant", "lol"},
    ),
    (
        "sports",
        {
            "sports",
            "nfl",
            "nba",
            "mlb",
            "nhl",
            "soccer",
            "football",
            "epl",
            "la-liga",
            "serie-a",
            "bundesliga",
            "ucl",
            "champions-league",
            "tennis",
            "golf",
            "ufc",
            "mma",
            "boxing",
            "f1",
            "formula-1",
            "nascar",
            "cricket",
            "rugby",
            "olympics",
            "college-football",
            "college-basketball",
            "cfb",
            "cbb",
            "wnba",
            "mls",
            "hockey",
            "baseball",
            "basketball",
        },
    ),
    (
        "geopolitics",
        {
            "geopolitics",
            "ukraine",
            "russia",
            "israel",
            "gaza",
            "middle-east",
            "china",
            "iran",
            "north-korea",
            "war",
            "nato",
            "world",
            "foreign-policy",
        },
    ),
    (
        "politics",
        {
            "politics",
            "us-politics",
            "elections",
            "2024-election",
            "2026-midterms",
            "2028-election",
            "trump",
            "congress",
            "senate",
            "house",
            "supreme-court",
            "presidential",
            "governor",
            "mayor",
            "uk-politics",
            "canada",
            "france",
            "germany",
            "poll",
            "cabinet",
            "white-house",
        },
    ),
    (
        "economics_finance",
        {
            "economics",
            "economy",
            "fed",
            "fed-rates",
            "interest-rates",
            "inflation",
            "cpi",
            "gdp",
            "jobs",
            "macro",
            "finance",
            "stocks",
            "equities",
            "sp500",
            "nasdaq",
            "ipo",
            "earnings",
            "commodities",
            "gold",
            "oil",
            "treasury",
            "recession",
            "tariffs",
            "trade",
        },
    ),
    (
        "tech_ai",
        {
            "ai",
            "artificial-intelligence",
            "openai",
            "tech",
            "technology",
            "science",
            "space",
            "spacex",
            "nasa",
            "big-tech",
            "chips",
            "semiconductors",
        },
    ),
    (
        "crypto",
        {
            "crypto",
            "bitcoin",
            "btc",
            "ethereum",
            "eth",
            "solana",
            "defi",
            "nft",
            "memecoins",
            "airdrops",
            "stablecoins",
        },
    ),
    (
        "weather",
        {"weather", "climate", "hurricane", "temperature", "heat", "snow", "rain"},
    ),
    (
        "culture",
        {
            "culture",
            "pop-culture",
            "entertainment",
            "movies",
            "music",
            "awards",
            "oscars",
            "grammys",
            "celebrity",
            "tv",
            "gaming",
            "games",
            "mentions",
            "tweets",
            "social-media",
            "royals",
            "religion",
            "pope",
        },
    ),
]

# 5/15/60-minute (and hourly/daily) crypto up-down series, detected on slug/question
_SHORT_CRYPTO_RE = re.compile(
    r"(bitcoin|btc|ethereum|eth|solana|sol|xrp|doge)[-\s].*"
    r"(up-or-down|up or down|\d+(am|pm)-et|-\d{1,2}(am|pm)-)",
    re.IGNORECASE,
)
_SHORT_SERIES_RE = re.compile(r"-(5m|15m|1h|hourly)-|up-or-down", re.IGNORECASE)
_CRYPTO_WORD_RE = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|dogecoin|doge)\b", re.IGNORECASE
)


def is_short_crypto(slug: str, question: str, end_ts: int | None, start_ts: int | None) -> bool:
    """Short-duration crypto up/down market (SPEC §4.8)."""
    text = f"{slug} {question}"
    if not _CRYPTO_WORD_RE.search(text):
        return False
    if _SHORT_CRYPTO_RE.search(text) or _SHORT_SERIES_RE.search(slug):
        return True
    # fall back to actual duration: crypto market open for <= 24h
    if end_ts is None or start_ts is None:
        return False
    return 0 < end_ts - start_ts <= 24 * 3600 * 1000


def bucket_for(tag_slugs: list[str], slug: str, question: str) -> str:
    tags = {t.lower() for t in tag_slugs}
    for bucket, rule_tags in _TAG_RULES:
        if tags & rule_tags:
            return bucket
    # no tag matched: light text fallback for the big three
    text = f"{slug} {question}".lower()
    if _CRYPTO_WORD_RE.search(text):
        return "crypto"
    if re.search(r"\b(fed|rate cut|rate hike|cpi|inflation|gdp)\b", text):
        return "economics_finance"
    return "other"


def categorize(
    tag_slugs: list[str],
    slug: str,
    question: str,
    end_ts: int | None = None,
    start_ts: int | None = None,
) -> str:
    if is_short_crypto(slug, question, end_ts, start_ts):
        return "crypto_short"
    return bucket_for(tag_slugs, slug, question)
