from polycopy.util.timeutil import ms_to_dt, parse_ts_ms


def test_iso_z():
    assert parse_ts_ms("2026-11-15T01:40:00Z") == 1794706800000


def test_gamma_space_plus00():
    ms = parse_ts_ms("2026-11-14 22:48:18.191513+00")
    assert ms is not None
    assert ms_to_dt(ms).hour == 22


def test_gamma_triple_zero_suffix():
    ms = parse_ts_ms("2026-11-14 22:48:18.191513+00:00:00")
    assert ms == parse_ts_ms("2026-11-14 22:48:18.191513+00")


def test_date_only_and_junk():
    assert parse_ts_ms("2026-11-15") == 1794700800000
    assert parse_ts_ms("") is None
    assert parse_ts_ms(None) is None
    assert parse_ts_ms("not a date") is None
