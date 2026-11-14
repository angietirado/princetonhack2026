"""SQLite persistence and aggregate reporting for anonymous experiment sessions."""

import csv
import io
import json
import secrets
import sqlite3
import statistics
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from spending_api.lab_engine import canonical, fingerprint


class LabConflict(Exception):
    pass


class LabNotFound(Exception):
    pass


class LabUnauthorized(Exception):
    pass


def now_iso():
    return datetime.now(UTC).isoformat()


def _average(values):
    return statistics.mean(values) if values else None


def _choice(row):
    return {
        "id": row["id"],
        "stage": row["stage"],
        "evaluation": json.loads(row["evaluation"]),
        "created_at": row["created_at"],
    }


class LabStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("Use a temporary SQLite file so request connections share state")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    token TEXT NOT NULL,
                    scenario_id TEXT NOT NULL,
                    scenario_snapshot TEXT NOT NULL,
                    scenario_hash TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    is_demo INTEGER NOT NULL CHECK (is_demo IN (0, 1)),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS choices (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    request_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    stage TEXT NOT NULL CHECK (stage IN ('initial', 'revised')),
                    evaluation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, request_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS choices_single_initial
                    ON choices(session_id) WHERE stage='initial';
                CREATE INDEX IF NOT EXISTS sessions_filter
                    ON sessions(scenario_id, is_demo, created_at);
                CREATE INDEX IF NOT EXISTS choices_session
                    ON choices(session_id, sequence);
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def public_session(row, *, include_token=False):
        result = {
            "id": row["id"],
            "scenario": json.loads(row["scenario_snapshot"]),
            "scenario_hash": row["scenario_hash"],
            "constraints": json.loads(row["constraints_json"]),
            "is_demo": bool(row["is_demo"]),
            "created_at": row["created_at"],
        }
        if include_token:
            result["token"] = row["token"]
        return result

    def create_session(self, request: dict, scenario: dict):
        request_hash = fingerprint(request)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM sessions WHERE request_id=?", (request["request_id"],)
            ).fetchone()
            if existing:
                if not secrets.compare_digest(existing["request_hash"], request_hash):
                    raise LabConflict("This request identifier was already used for different data")
                return self.public_session(existing, include_token=True)
            session_id = str(uuid4())
            connection.execute(
                """INSERT INTO sessions
                (id,request_id,request_hash,token,scenario_id,scenario_snapshot,scenario_hash,
                 constraints_json,is_demo,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_id, request["request_id"], request_hash, secrets.token_urlsafe(32),
                    scenario["id"], canonical(scenario), fingerprint(scenario),
                    canonical(request["constraints"]), int(request["is_demo"]), now_iso(),
                ),
            )
            row = connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            return self.public_session(row, include_token=True)

    def session(self, session_id: str, token: str | None = None, *, admin=False):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise LabNotFound("Session not found")
            if not admin and (
                not token or not secrets.compare_digest(row["token"].encode(), token.encode())
            ):
                raise LabUnauthorized("A valid session access token is required")
            result = self.public_session(row)
            result["choices"] = [
                _choice(choice) for choice in connection.execute(
                    "SELECT * FROM choices WHERE session_id=? ORDER BY sequence", (session_id,)
                )
            ]
            return result

    def add_choice(self, session_id: str, request: dict, evaluation: dict):
        request_hash = fingerprint(request)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM choices WHERE session_id=? AND request_id=?",
                (session_id, request["request_id"]),
            ).fetchone()
            if existing:
                if not secrets.compare_digest(existing["request_hash"], request_hash):
                    raise LabConflict("This request identifier was already used for different data")
                return _choice(existing)
            initial = connection.execute(
                "SELECT id FROM choices WHERE session_id=? AND stage='initial'", (session_id,)
            ).fetchone()
            if request["stage"] == "initial" and initial:
                raise LabConflict("This session already has an initial choice")
            if request["stage"] == "revised" and not initial:
                raise LabConflict("Submit an initial choice before revising it")
            count = connection.execute(
                "SELECT COUNT(*) FROM choices WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            if count >= 100:
                raise LabConflict("This session has reached its 100-choice limit")
            choice_id = str(uuid4())
            connection.execute(
                """INSERT INTO choices
                (id,session_id,request_id,request_hash,stage,evaluation,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    choice_id, session_id, request["request_id"], request_hash,
                    request["stage"], canonical(evaluation), now_iso(),
                ),
            )
            return _choice(connection.execute(
                "SELECT * FROM choices WHERE id=?", (choice_id,)
            ).fetchone())

    @staticmethod
    def _where(scenario_id, data_mode):
        clauses, values = [], []
        if scenario_id:
            clauses.append("scenario_id=?")
            values.append(scenario_id)
        if data_mode != "all":
            clauses.append("is_demo=?")
            values.append(int(data_mode == "demo"))
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), values

    def _filtered(self, scenario_id=None, data_mode="participants"):
        where, values = self._where(scenario_id, data_mode)
        with self.connect() as connection:
            # A read transaction binds the session and choice queries to one database snapshot.
            connection.execute("BEGIN")
            sessions = connection.execute(
                "SELECT * FROM sessions" + where + " ORDER BY created_at DESC, id", values
            ).fetchall()
            choice_rows = connection.execute(
                """SELECT choices.* FROM choices
                JOIN (SELECT id FROM sessions""" + where + """) selected
                ON choices.session_id=selected.id ORDER BY choices.sequence""", values
            ).fetchall()
        choices = {}
        for row in choice_rows:
            choices.setdefault(row["session_id"], []).append(_choice(row))
        return sessions, choices

    @staticmethod
    def _session_summary(row, choices):
        initial = next((item for item in choices if item["stage"] == "initial"), None)
        revised = next((item for item in reversed(choices) if item["stage"] == "revised"), None)
        comparable = bool(
            initial and revised
            and initial["evaluation"]["constraints"] == revised["evaluation"]["constraints"]
        )
        scenario = json.loads(row["scenario_snapshot"])
        return {
            "id": row["id"], "scenario_id": row["scenario_id"], "scenario_name": scenario["name"],
            "scenario_version": scenario["version"], "scenario_hash": row["scenario_hash"],
            "is_demo": bool(row["is_demo"]), "created_at": row["created_at"],
            "constraints": json.loads(row["constraints_json"]),
            "initial": initial, "latest_revised": revised, "choice_count": len(choices),
            "comparable": comparable,
        }

    def sessions(self, scenario_id=None, data_mode="participants", limit=50, offset=0):
        rows, choices = self._filtered(scenario_id, data_mode)
        return {
            "sessions": [
                self._session_summary(row, choices.get(row["id"], []))
                for row in rows[offset:offset + limit]
            ],
            "total": len(rows), "limit": limit, "offset": offset,
        }

    def summary(self, scenario_id=None, data_mode="participants"):
        rows, choices = self._filtered(scenario_id, data_mode)
        initial_costs, initial_times, savings, time_changes = [], [], [], []
        initial_count = revised_count = completed = infeasible = 0
        scenarios, store_counts, store_names = {}, Counter(), {}
        tradeoffs = []
        changed_constraints = 0
        for row in rows:
            snapshot = json.loads(row["scenario_snapshot"])
            session_choices = choices.get(row["id"], [])
            summary = self._session_summary(row, session_choices)
            group = scenarios.setdefault(row["scenario_id"], {
                "scenario_id": row["scenario_id"], "scenario_name": snapshot["name"],
                "sessions": 0, "initial_choices": 0, "revised_choices": 0,
            })
            group["sessions"] += 1
            for store in snapshot["stores"]:
                store_names[(snapshot["id"], store["id"])] = store["name"]
            if summary["initial"]:
                completed += 1
                baseline = summary["initial"]["evaluation"]
                initial_costs.append(baseline["total_cost_cents"])
                initial_times.append(baseline["total_minutes"])
            if summary["comparable"]:
                after = summary["latest_revised"]["evaluation"]
                savings.append(baseline["total_cost_cents"] - after["total_cost_cents"])
                time_changes.append(after["total_minutes"] - baseline["total_minutes"])
            elif summary["latest_revised"]:
                changed_constraints += 1
            for choice in session_choices:
                evaluation = choice["evaluation"]
                is_initial = choice["stage"] == "initial"
                initial_count += is_initial
                revised_count += not is_initial
                group["initial_choices" if is_initial else "revised_choices"] += 1
                infeasible += not evaluation["feasible"]
                for store_id in evaluation["store_ids"]:
                    store_counts[(snapshot["id"], store_id)] += 1
                tradeoffs.append({
                    "session_id": row["id"], "scenario_id": row["scenario_id"],
                    "stage": choice["stage"], "cost_cents": evaluation["total_cost_cents"],
                    "minutes": evaluation["total_minutes"], "feasible": evaluation["feasible"],
                    "is_demo": bool(row["is_demo"]),
                })
        notices = [
            "These are choices inside a simulation, not actual Houston spending or purchases.",
            "Savings compare an initial choice with its latest revision only when constraints match.",
            "Before/after differences are descriptive; practice and self-selection can affect results.",
            "Store counts include every saved choice; one choice can include two stores.",
            "Cross-scenario averages mix different settings. Filter a scenario for closer comparisons.",
        ]
        if data_mode == "demo":
            notices.append("Demo sessions are generated examples, not participant observations.")
        elif data_mode == "all":
            notices.append("This view combines participant submissions and generated demo sessions.")
        if changed_constraints:
            notices.append(
                f"{changed_constraints} revised sessions changed constraints and are excluded "
                "from paired savings and time comparisons."
            )
        if len(tradeoffs) > 2000:
            notices.append("The plot shows up to 2,000 choices; totals use all matching choices.")
        return {
            "data_mode": data_mode, "session_count": len(rows),
            "completed_session_count": completed,
            "initial_choice_count": initial_count, "revised_choice_count": revised_count,
            "matched_pair_count": len(savings), "infeasible_choice_count": infeasible,
            "mean_initial_cost_cents": _average(initial_costs),
            "median_initial_cost_cents": statistics.median(initial_costs) if initial_costs else None,
            "mean_initial_minutes": _average(initial_times),
            "mean_savings_cents": _average(savings), "mean_time_change_minutes": _average(time_changes),
            "scenario_breakdown": sorted(scenarios.values(), key=lambda item: item["scenario_id"]),
            "store_choices": [
                {"store_id": key[1], "scenario_id": key[0], "store_name": store_names[key],
                 "choice_count": count}
                for key, count in sorted(store_counts.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
            "tradeoffs": tradeoffs[:2000], "notices": notices,
        }

    def export(self, scenario_id=None, data_mode="participants"):
        rows, choices = self._filtered(scenario_id, data_mode)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow([
            "session_id", "scenario_id", "scenario_name", "scenario_version", "scenario_hash",
            "is_demo", "choice_id", "stage", "created_at", "basket_cost_cents",
            "transport_cost_cents", "total_cost_cents", "total_minutes", "feasible",
            "budget_cents", "time_budget_minutes", "travel_mode", "max_stores", "store_ids",
        ])
        for row in rows:
            scenario = json.loads(row["scenario_snapshot"])
            for choice in choices.get(row["id"], []):
                value = choice["evaluation"]
                constraints = value["constraints"]
                cells = [
                    row["id"], row["scenario_id"], scenario["name"], scenario["version"],
                    row["scenario_hash"], bool(row["is_demo"]), choice["id"], choice["stage"],
                    choice["created_at"], value["basket_cost_cents"], value["transport_cost_cents"],
                    value["total_cost_cents"], value["total_minutes"], value["feasible"],
                    constraints["budget_cents"], constraints["time_budget_minutes"],
                    constraints["travel_mode"], constraints["max_stores"], "|".join(value["store_ids"]),
                ]
                writer.writerow([
                    "'" + item if isinstance(item, str) and (
                        item.startswith(("\t", "\r", "\n"))
                        or item.lstrip().startswith(("=", "+", "-", "@"))
                    ) else item for item in cells
                ])
        return output.getvalue()



