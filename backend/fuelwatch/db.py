"""SQLite persistence (design part 3.3): world epochs and demand history.

SQLite in WAL mode, one connection guarded by a lock so it can be used from the
event loop and from worker threads. Everything is keyed by a world epoch so tick
numbers from different worlds never mix. Only what must outlive a restart is kept
here; the snapshot and forecasts are rebuilt from the simulator.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import DemandRecord

SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS world_epoch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    instance_id INTEGER NOT NULL,
    scenario_id TEXT NOT NULL,
    scenario_version TEXT NOT NULL,
    seed INTEGER NOT NULL,
    tick_minutes INTEGER NOT NULL,
    last_tick INTEGER NOT NULL,
    last_allocation_count INTEGER NOT NULL,
    last_served_liters REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS demand_history (
    epoch_id INTEGER NOT NULL REFERENCES world_epoch(id),
    station_id TEXT NOT NULL,
    fuel_type TEXT NOT NULL,
    tick INTEGER NOT NULL,
    demand REAL NOT NULL,
    served REAL NOT NULL,
    unmet REAL NOT NULL,
    PRIMARY KEY (epoch_id, station_id, fuel_type, tick)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS demand_history_by_tick ON demand_history (epoch_id, tick);
CREATE TABLE IF NOT EXISTS proposal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch_id INTEGER NOT NULL REFERENCES world_epoch(id),
    station_id TEXT NOT NULL,
    fuel_type TEXT NOT NULL,
    snapshot_tick INTEGER NOT NULL,
    expires_tick INTEGER NOT NULL,
    status TEXT NOT NULL,
    status_reason TEXT,
    policy TEXT NOT NULL,
    confidence REAL NOT NULL,
    risk_before REAL,
    risk_after REAL,
    explanation TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS proposal_by_status ON proposal (epoch_id, status);
CREATE TABLE IF NOT EXISTS proposal_line (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL REFERENCES proposal(id),
    depot_id TEXT NOT NULL,
    station_id TEXT NOT NULL,
    route_id TEXT NOT NULL,
    fuel_type TEXT NOT NULL,
    proposed_quantity REAL NOT NULL,
    idempotency_key TEXT UNIQUE,
    status TEXT NOT NULL,
    sim_allocation_id INTEGER,
    executed_quantity REAL,
    failure_code TEXT
);
CREATE INDEX IF NOT EXISTS proposal_line_by_proposal ON proposal_line (proposal_id);
CREATE TABLE IF NOT EXISTS alert (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch_id INTEGER NOT NULL REFERENCES world_epoch(id),
    station_id TEXT NOT NULL,
    fuel_type TEXT NOT NULL,
    level TEXT NOT NULL,
    first_tick INTEGER NOT NULL,
    tick INTEGER NOT NULL,
    hours_p50 REAL,
    hours_p90 REAL,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS alert_by_state ON alert (epoch_id, state);
CREATE TABLE IF NOT EXISTS execution_attempt (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id INTEGER NOT NULL REFERENCES proposal_line(id),
    attempted_at TEXT NOT NULL,
    snapshot_tick INTEGER NOT NULL,
    snapshot_age_s REAL NOT NULL,
    http_status INTEGER,
    error_code TEXT,
    outcome TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS execution_attempt_by_line ON execution_attempt (line_id);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""

_CLOSED_WITHOUT_SENDING = {"SUPERSEDED": "SUPERSEDED", "EXPIRED": "EXPIRED", "REJECTED": "REJECTED_BY_OPERATOR"}


@dataclass(frozen=True)
class EpochRow:
    """A world epoch and the last thing we observed in it (used to spot a reset)."""

    id: int
    started_at: str
    reason: str
    instance_id: int
    scenario_id: str
    scenario_version: str
    seed: int
    tick_minutes: int
    last_tick: int
    last_allocation_count: int
    last_served_liters: float


_EPOCH_COLUMNS = (
    "id, started_at, reason, instance_id, scenario_id, scenario_version, seed, tick_minutes, "
    "last_tick, last_allocation_count, last_served_liters"
)


class Database:
    def __init__(self, path: str = ":memory:") -> None:
        if path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._migrate()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"database schema {version} is newer than this build ({SCHEMA_VERSION})")
        self._conn.executescript(_SCHEMA)
        self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- epochs ----------------------------------------------------------------------

    def latest_epoch(self) -> EpochRow | None:
        with self._lock:
            row = self._conn.execute(f"SELECT {_EPOCH_COLUMNS} FROM world_epoch ORDER BY id DESC LIMIT 1").fetchone()
        return EpochRow(**dict(row)) if row else None

    def insert_epoch(self, *, reason: str, instance_id: int, scenario_id: str, scenario_version: str, seed: int, tick_minutes: int, tick: int, allocation_count: int, served_liters: float) -> EpochRow:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO world_epoch (started_at, reason, instance_id, scenario_id, scenario_version, seed, tick_minutes,"
                " last_tick, last_allocation_count, last_served_liters) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (datetime.now(UTC).isoformat(), reason, instance_id, scenario_id, scenario_version, seed, tick_minutes, tick, allocation_count, served_liters),
            )
            row = self._conn.execute(f"SELECT {_EPOCH_COLUMNS} FROM world_epoch WHERE id=?", (cur.lastrowid,)).fetchone()
        return EpochRow(**dict(row))

    def update_epoch_observation(self, epoch_id: int, *, tick: int, allocation_count: int, served_liters: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE world_epoch SET last_tick=?, last_allocation_count=?, last_served_liters=? WHERE id=?",
                (tick, allocation_count, served_liters, epoch_id),
            )

    # -- demand history ----------------------------------------------------------------

    def upsert_demand(self, epoch_id: int, records: Iterable[DemandRecord]) -> int:
        rows = [(epoch_id, r.station_id, r.fuel_type.value, r.tick, r.demand_liters, r.served_liters, r.unmet_liters) for r in records]
        if not rows:
            return 0
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(
                    "INSERT INTO demand_history (epoch_id, station_id, fuel_type, tick, demand, served, unmet) VALUES (?,?,?,?,?,?,?)"
                    " ON CONFLICT (epoch_id, station_id, fuel_type, tick) DO UPDATE SET demand=excluded.demand, served=excluded.served, unmet=excluded.unmet",
                    rows,
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return len(rows)

    def complete_run(self, epoch_id: int, rows_per_tick: int) -> tuple[int, int] | None:
        """The newest run of consecutive complete ticks, as (first_tick, last_tick); None if there is none.

        A tick is complete when it has all its rows. Older ticks beyond a gap are kept but are not
        part of the run: the forecaster needs a contiguous recent window, not tick 0.
        """
        with self._lock:
            counts = self._conn.execute(
                "SELECT tick FROM demand_history WHERE epoch_id=? GROUP BY tick HAVING COUNT(*) >= ? ORDER BY tick",
                (epoch_id, rows_per_tick),
            ).fetchall()
        run: tuple[int, int] | None = None
        for row in counts:
            t = row["tick"]
            run = (run[0], t) if run is not None and t == run[1] + 1 else (t, t)
        return run

    def demand_rows(self, epoch_id: int, *, station_id: str | None = None, fuel_type: str | None = None, from_tick: int = 0, limit: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT station_id, fuel_type, tick, demand, served, unmet FROM demand_history WHERE epoch_id=? AND tick>=?"
        args: list[object] = [epoch_id, from_tick]
        if station_id:
            sql += " AND station_id=?"
            args.append(station_id)
        if fuel_type:
            sql += " AND fuel_type=?"
            args.append(fuel_type)
        sql += " ORDER BY tick, station_id, fuel_type"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def demand_row_count(self, epoch_id: int) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM demand_history WHERE epoch_id=?", (epoch_id,)).fetchone()[0]

    # -- proposals -----------------------------------------------------------------------

    def insert_proposal(self, *, epoch_id: int, station_id: str, fuel_type: str, snapshot_tick: int, expires_tick: int, policy: str, confidence: float, risk_before: float | None, risk_after: float | None, explanation: dict, lines: list[dict]) -> int:
        """Store a proposal and its lines in one transaction; each line's key is `fw-p{proposal}-l{line}`.

        The key is written with the line, before anything can be sent, so a crash or a timeout later
        always finds the same key to retry with (design 3.3).
        """
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                cur = self._conn.execute(
                    "INSERT INTO proposal (epoch_id, station_id, fuel_type, snapshot_tick, expires_tick, status, policy, confidence, risk_before, risk_after, explanation, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,'PROPOSED',?,?,?,?,?,?,?)",
                    (epoch_id, station_id, fuel_type, snapshot_tick, expires_tick, policy, confidence, risk_before, risk_after, json.dumps(explanation), now, now),
                )
                pid = cur.lastrowid
                for line in lines:
                    lc = self._conn.execute(
                        "INSERT INTO proposal_line (proposal_id, depot_id, station_id, route_id, fuel_type, proposed_quantity, status) VALUES (?,?,?,?,?,?,'PLANNED')",
                        (pid, line["depot_id"], line["station_id"], line["route_id"], line["fuel_type"], line["quantity"]),
                    )
                    self._conn.execute("UPDATE proposal_line SET idempotency_key=? WHERE id=?", (f"fw-p{pid}-l{lc.lastrowid}", lc.lastrowid))
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return pid

    def set_proposal_status(self, proposal_id: int, status: str, reason: str | None = None, *, only_from: tuple[str, ...] | None = None) -> bool:
        """Move a proposal to `status`; with `only_from`, only if it is currently in one of those states."""
        sql = "UPDATE proposal SET status=?, status_reason=?, updated_at=? WHERE id=?"
        args: list[object] = [status, reason, datetime.now(UTC).isoformat(), proposal_id]
        if only_from:
            sql += f" AND status IN ({','.join('?' * len(only_from))})"
            args.extend(only_from)
        with self._lock:
            changed = self._conn.execute(sql, args).rowcount > 0
            if changed and status in _CLOSED_WITHOUT_SENDING:
                # A proposal that ends without being approved will never send its planned lines.
                self._conn.execute("UPDATE proposal_line SET status='SKIPPED', failure_code=? WHERE proposal_id=? AND status='PLANNED'", (_CLOSED_WITHOUT_SENDING[status], proposal_id))
            return changed

    def proposals(self, *, epoch_id: int | None = None, statuses: tuple[str, ...] | None = None, station_id: str | None = None, fuel_type: str | None = None, limit: int = 100) -> list[dict]:
        sql, args = "SELECT * FROM proposal WHERE 1=1", []
        if epoch_id is not None:
            sql += " AND epoch_id=?"
            args.append(epoch_id)
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            args.extend(statuses)
        if station_id:
            sql += " AND station_id=?"
            args.append(station_id)
        if fuel_type:
            sql += " AND fuel_type=?"
            args.append(fuel_type)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["explanation"] = json.loads(d["explanation"])
                d["lines"] = [dict(x) for x in self._conn.execute("SELECT * FROM proposal_line WHERE proposal_id=? ORDER BY id", (d["id"],)).fetchall()]
                out.append(d)
        return out

    def proposal(self, proposal_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT id FROM proposal WHERE id=?", (proposal_id,)).fetchone()
            if row is None:
                return None
            d = dict(self._conn.execute("SELECT * FROM proposal WHERE id=?", (proposal_id,)).fetchone())
            d["explanation"] = json.loads(d["explanation"])
            d["lines"] = [dict(x) for x in self._conn.execute("SELECT * FROM proposal_line WHERE proposal_id=? ORDER BY id", (proposal_id,)).fetchall()]
            for line in d["lines"]:
                line["attempts"] = [dict(x) for x in self._conn.execute("SELECT * FROM execution_attempt WHERE line_id=? ORDER BY id", (line["id"],)).fetchall()]
        return d

    # -- alerts ---------------------------------------------------------------------------

    def alerts(self, *, epoch_id: int | None = None, states: tuple[str, ...] | None = None, limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM alert WHERE 1=1", []
        if epoch_id is not None:
            sql += " AND epoch_id=?"
            args.append(epoch_id)
        if states:
            sql += f" AND state IN ({','.join('?' * len(states))})"
            args.extend(states)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def insert_alert(self, *, epoch_id: int, station_id: str, fuel_type: str, level: str, tick: int, hours_p50: float | None, hours_p90: float | None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO alert (epoch_id, station_id, fuel_type, level, first_tick, tick, hours_p50, hours_p90, state, updated_at) VALUES (?,?,?,?,?,?,?,?,'open',?)",
                (epoch_id, station_id, fuel_type, level, tick, tick, hours_p50, hours_p90, datetime.now(UTC).isoformat()),
            )
        return cur.lastrowid

    def refresh_alert(self, alert_id: int, *, level: str, tick: int, hours_p50: float | None, hours_p90: float | None) -> None:
        with self._lock:
            self._conn.execute("UPDATE alert SET level=?, tick=?, hours_p50=?, hours_p90=?, updated_at=? WHERE id=?", (level, tick, hours_p50, hours_p90, datetime.now(UTC).isoformat(), alert_id))

    def set_alert_state(self, alert_id: int, state: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE alert SET state=?, updated_at=? WHERE id=?", (state, datetime.now(UTC).isoformat(), alert_id))

    # -- ledger: lines, attempts, audit -----------------------------------------------------

    _LINE_COLUMNS = ("status", "sim_allocation_id", "executed_quantity", "failure_code")

    def update_line(self, line_id: int, **fields: object) -> None:
        unknown = set(fields) - set(self._LINE_COLUMNS)
        if unknown or not fields:
            raise ValueError(f"cannot update line columns {sorted(unknown) or fields}")
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE proposal_line SET {sets} WHERE id=?", (*fields.values(), line_id))

    def lines_in(self, statuses: tuple[str, ...], *, epoch_id: int | None = None) -> list[dict]:
        sql = f"SELECT l.*, p.epoch_id FROM proposal_line l JOIN proposal p ON p.id=l.proposal_id WHERE l.status IN ({','.join('?' * len(statuses))})"
        args: list[object] = list(statuses)
        if epoch_id is not None:
            sql += " AND p.epoch_id=?"
            args.append(epoch_id)
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql + " ORDER BY l.id", args).fetchall()]

    def ledger(self, epoch_id: int, *, statuses: tuple[str, ...] | None = None, limit: int = 100) -> list[dict]:
        """Lines that have been, or are being, sent, newest first, with their proposal's headline fields."""
        wanted = statuses or ("SENDING", "UNKNOWN", "SUBMITTED", "IN_TRANSIT", "ARRIVED", "FAILED", "REJECTED")
        sql = (
            "SELECT l.*, p.epoch_id, p.snapshot_tick, p.policy, p.status AS proposal_status, p.confidence"
            f" FROM proposal_line l JOIN proposal p ON p.id=l.proposal_id WHERE p.epoch_id=? AND l.status IN ({','.join('?' * len(wanted))})"
            " ORDER BY l.id DESC LIMIT ?"
        )
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, (epoch_id, *wanted, limit)).fetchall()]

    def add_attempt(self, line_id: int, *, snapshot_tick: int, snapshot_age_s: float, http_status: int | None, error_code: str | None, outcome: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO execution_attempt (line_id, attempted_at, snapshot_tick, snapshot_age_s, http_status, error_code, outcome) VALUES (?,?,?,?,?,?,?)",
                (line_id, datetime.now(UTC).isoformat(), snapshot_tick, snapshot_age_s, http_status, error_code, outcome),
            )
        return cur.lastrowid

    def last_attempt_at(self, line_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT MAX(attempted_at) FROM execution_attempt WHERE line_id=?", (line_id,)).fetchone()
        return row[0]

    def audit(self, actor: str, action: str, entity: str, entity_id: object, detail: dict | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log (at, actor, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?)",
                (datetime.now(UTC).isoformat(), actor, action, entity, str(entity_id), json.dumps(detail or {})),
            )

    def audit_rows(self, *, limit: int = 100, offset: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]
