"""Decide si la ejecución programada debe correr (el cron de GitHub va en UTC y
no conoce el horario de verano). Corre si en Madrid son las 7:xx u 8:xx de un día
laborable y aún no existe el informe de hoy. Las ejecuciones manuales siempre corren."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HIST = Path(__file__).resolve().parent.parent / "data" / "radar" / "history"


def should_run(now: datetime, event: str, hist: Path = HIST) -> tuple[bool, str]:
    if event != "schedule":
        return True, f"evento {event}: siempre corre"
    if now.weekday() >= 5:
        return False, "fin de semana"
    if now.hour not in (7, 8):
        return False, f"en Madrid son las {now:%H:%M}; se espera 7:xx-8:xx"
    if (hist / f"{now:%Y-%m-%d}.json").exists():
        return False, "el informe de hoy ya existe"
    return True, "hora correcta y sin informe de hoy"


if __name__ == "__main__":
    ok, why = should_run(datetime.now(ZoneInfo("Europe/Madrid")), os.environ.get("GITHUB_EVENT_NAME", "manual"))
    print(f"run={'true' if ok else 'false'} ({why})")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"run={'true' if ok else 'false'}\n")
    sys.exit(0)
