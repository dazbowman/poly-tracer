from polycopy.catalog.categories import bucket_for, categorize, is_short_crypto

H = 3600 * 1000


def test_tag_priority_esports_before_sports():
    assert bucket_for(["sports", "esports", "league-of-legends"], "lol-x", "LoL match") == "esports"


def test_sports_and_politics():
    assert bucket_for(["nba"], "nba-lal-bos", "Lakers vs Celtics") == "sports"
    assert bucket_for(["us-politics", "trump"], "x", "Will Trump…") == "politics"


def test_short_crypto_by_slug_series():
    assert is_short_crypto(
        "bitcoin-up-or-down-november-14-3pm-et", "Bitcoin Up or Down", None, None
    )
    assert categorize([], "ethereum-up-or-down-5m-1700000000", "ETH up or down?", None, None) == (
        "crypto_short"
    )


def test_short_crypto_by_duration():
    start, end = 0, 2 * H  # 2-hour crypto market
    assert is_short_crypto("btc-above-100k-at-5pm", "Bitcoin above $100k at 5pm ET?", end, start)
    # 30-day crypto market is NOT short
    assert not is_short_crypto("btc-100k-december", "BTC $100k in December?", 30 * 24 * H, 0)


def test_long_crypto_is_crypto():
    assert categorize(["crypto", "bitcoin"], "btc-dec", "BTC $100k by Dec 31?", None, None) == (
        "crypto"
    )


def test_unknown_is_other():
    assert bucket_for([], "some-market", "Will something niche happen?") == "other"


def test_non_crypto_short_game_is_not_crypto_short():
    # a 2-hour esports market must not be crypto_short
    assert categorize(["esports"], "lol-blg-gen", "LoL game 2 winner", 2 * H, 0) == "esports"
