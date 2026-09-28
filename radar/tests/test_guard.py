from datetime import datetime
from zoneinfo import ZoneInfo

from radar.guard import should_run

MAD = ZoneInfo("Europe/Madrid")


def at(y, mo, d, h, mi=30):
    return datetime(y, mo, d, h, mi, tzinfo=MAD)


def test_summer_and_winter_cron_slots(tmp_path):
    # Cron 05:30 y 06:30 UTC. Verano (UTC+2) -> 7:30 y 8:30; invierno (UTC+1) -> 6:30 y 7:30.
    assert should_run(at(2026, 9, 28, 7), "schedule", tmp_path)[0]
    assert should_run(at(2026, 9, 28, 8), "schedule", tmp_path)[0]
    assert not should_run(at(2026, 12, 1, 6), "schedule", tmp_path)[0]
    assert should_run(at(2026, 12, 1, 7), "schedule", tmp_path)[0]


def test_skips_weekend_and_existing_report(tmp_path):
    assert not should_run(at(2026, 9, 26, 7), "schedule", tmp_path)[0]  # sábado
    (tmp_path / "2026-09-28.json").write_text("{}")
    assert not should_run(at(2026, 9, 28, 8), "schedule", tmp_path)[0]


def test_manual_always_runs(tmp_path):
    assert should_run(at(2026, 9, 26, 3), "workflow_dispatch", tmp_path)[0]
