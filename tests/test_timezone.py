"""Unit tests for timezone handling and weekly window boundaries."""

from datetime import UTC, datetime

import pytz

from app.core.timezone_engine import get_current_week_window, get_timezone_obj, is_new_week


def test_get_current_week_window_monday_start():
    # Thursday, August 27, 2026, 12:00:00 UTC
    dt = datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC)
    week_id, start_utc, end_utc, now_utc = get_current_week_window("America/Los_Angeles", dt)

    assert week_id == "2026-W35"
    assert now_utc == dt

    # Monday in Los Angeles was August 24, 2026
    # PDT is UTC-7, so Monday 00:00:00 PDT is Monday 07:00:00 UTC
    assert start_utc.year == 2026
    assert start_utc.month == 8
    assert start_utc.day == 24
    assert start_utc.hour == 7
    assert start_utc.minute == 0

    # Sunday 23:59:59.999999 PDT is Monday August 31, 2026 06:59:59.999999 UTC
    assert end_utc.year == 2026
    assert end_utc.month == 8
    assert end_utc.day == 31
    assert end_utc.hour == 6
    assert end_utc.minute == 59
    assert end_utc.second == 59


def test_get_current_week_window_naive_datetime_and_invalid_timezone_fallback():
    # Naive datetime is treated as UTC
    naive_dt = datetime(2026, 8, 27, 12, 0, 0)
    week_id, start_utc, end_utc, now_utc = get_current_week_window("America/Los_Angeles", naive_dt)
    assert week_id == "2026-W35"
    assert now_utc.tzinfo is not None
    assert start_utc.day == 24
    assert end_utc.day == 31

    # Invalid timezone falls back to UTC
    tz = get_timezone_obj("Invalid/Zone")
    assert tz == pytz.UTC
    w_id_utc, start_fb, end_fb, _ = get_current_week_window("Invalid/Zone", naive_dt)
    assert w_id_utc == "2026-W35"
    assert start_fb.day == 24
    assert start_fb.hour == 0
    assert end_fb.day == 30
    assert end_fb.hour == 23


def test_is_new_week():
    assert is_new_week("2026-W34", "2026-W35") is True
    assert is_new_week("2026-W35", "2026-W35") is False
    assert is_new_week(None, "2026-W35") is False
