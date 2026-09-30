"""Decide si la ejecución programada debe correr. El cron de GitHub va en UTC, no conoce
el horario de verano y puede retrasarse varias horas, así que hay varias franjas de
respaldo: corre la primera que llegue a partir de las 7:00 de Madrid en un día laborable
si aún no existe el informe de hoy. Las ejecuciones manuales siempre corren."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

EARLIEST_HOUR = 7
HIST = Path(__file__).resolve().parent.parent / "data" / "radar" / "history"


def should_run(now: datetime, event: str, hist: Path = HIST) -> tuple[bool, str]:
    if event != "schedule":
        return True, f"evento {event}: siempre corre"
    if now.weekday() >= 5:
        return False, "fin de semana"
    if now.hour < EARLIEST_HOUR:
        return False, f"en Madrid son las {now:%H:%M}; aún es pronto (desde las {EARLIEST_HOUR}:00)"
    if (hist / f"{now:%Y-%m-%d}.json").exists():
        return False, "el informe de hoy ya existe"
    return True, "día laborable, desde las 7:00 y sin informe de hoy"


if __name__ == "__main__":
    ok, why = should_run(datetime.now(ZoneInfo("Europe/Madrid")), os.environ.get("GITHUB_EVENT_NAME", "manual"))
    print(f"run={'true' if ok else 'false'} ({why})")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"run={'true' if ok else 'false'}\n")
    sys.exit(0)
