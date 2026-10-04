"""Keeps a data folder in step with the app version that uses it (plan R15: "settings migrated
by version number").

    data_dir\\version.json   {"schema": n, "app": "<last version that ran here>"}

Each migration upgrades the folder by one schema number and must leave it readable by the
previous app version too (additive only), because rollback is one click: an older version
can meet a folder a newer one already touched. It then just logs that and carries on.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .version import __version__

log = logging.getLogger("oneprod.migrate")


def _m1_record(data_dir: Path) -> None:
    """Schema 1: start recording which version last used the folder (nothing to convert)."""


MIGRATIONS = {1: _m1_record}
SCHEMA = max(MIGRATIONS)


def migrate(data_dir: Path) -> dict:
    f = data_dir / "version.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    have = int(d.get("schema") or 0)
    done = []
    for n in range(have + 1, SCHEMA + 1):
        MIGRATIONS[n](data_dir)
        done.append(n)
    newer = have > SCHEMA
    if newer:
        log.warning("this data folder was last used by a newer One Production (%s) - running anyway",
                    d.get("app"))
    f.write_text(json.dumps({"schema": max(have, SCHEMA), "app": __version__,
                             "previous_app": d.get("app")}, indent=1), encoding="utf-8")
    return {"from": have, "to": max(have, SCHEMA), "ran": done, "newer_data": newer}
