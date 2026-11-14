"""Authenticated, idempotent investigation reports; never writes shopping choices."""

import copy
import json
import os
import secrets
import time
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field, field_validator
from starlette.concurrency import run_in_threadpool

from spending_api.lab_chat import InterpreterGate
from spending_api.lab_engine import canonical, fingerprint
from spending_api.lab_investigator import InvestigationSelection, investigate_decision, run_investigation_agent
from spending_api.lab_models import Constraints, InputModel
from spending_api.lab_store import LabConflict, LabStore, now_iso

LimitName = Literal["budget_cents", "time_budget_minutes", "max_stores", "travel_mode"]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$", min_length=1, max_length=80)]


class InvestigationRequest(InputModel):
    request_id: UUID
    message: str = Field(default="Why doesn't this plan work, and what is the smallest change that helps?", min_length=1, max_length=1500)
    constraints: Constraints | None = None
    assignments: dict[Identifier, Identifier] | None = Field(default=None, min_length=1, max_length=10)
    protected_constraints: list[LimitName] = Field(default_factory=list, max_length=4)
    allowed_travel_modes: list[Literal["walk", "drive"]] | None = Field(default=None, max_length=2)

    @field_validator("message")
    @classmethod
    def meaningful_message(cls, value):
        if not value.strip() or any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            raise ValueError("Enter a nonblank question without control characters")
        return value

    @field_validator("protected_constraints", "allowed_travel_modes")
    @classmethod
    def unique_values(cls, value):
        if value is not None and len(set(value)) != len(value):
            raise ValueError("Repeat each setting at most once")
        return value


def _provider_investigate(payload):
    """Construct a provider only inside the bounded worker, never at app startup."""
    provider = os.environ.get("LAB_AI_PROVIDER", "gemini").strip().lower()
    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise ValueError("Provider is not configured")
        from langchain_google_genai import ChatGoogleGenerativeAI

        model = ChatGoogleGenerativeAI(
            model=os.environ.get("LAB_AI_MODEL") or "gemini-3.8-flash",
            google_api_key=key, temperature=0, max_output_tokens=512,
            timeout=15, max_retries=0,
        )
    elif provider == "nvidia":
        key = os.environ.get("NVIDIA_API_KEY", "")
        if not key:
            raise ValueError("Provider is not configured")
        from langchain_nvidia_ai_endpoints import ChatNVIDIA

        model = ChatNVIDIA(
            model=os.environ.get("LAB_AI_MODEL") or "nvidia/nemotron-3.5-lightning-30b-a3b",
            api_key=key, temperature=0, max_tokens=512, timeout=15, max_retries=0,
        )
    else:
        raise ValueError("Provider is not supported")
    result = run_investigation_agent(payload["report"], model, payload["question"])
    if result["mode"] != "langchain":
        raise ValueError("Model investigation did not complete")
    return {
        "evidence_ids": result.get("highlighted_evidence_ids", []),
        "alternative_ids": result["highlighted_alternative_ids"],
        "question_id": result["question"]["id"] if result["question"] else None,
    }


def _apply_selection(report, candidate):
    """An injected callback can select presentation IDs, never replace facts."""
    selection = InvestigationSelection.model_validate(candidate)
    evidence = {e["id"] for e in report["evidence"]}
    alternatives = {a["id"] for a in report["alternatives"]}
    questions = {q["id"]: q for q in report["questions"]}
    if not set(selection.evidence_ids) <= evidence or not set(selection.alternative_ids) <= alternatives:
        raise ValueError("Unknown investigation evidence")
    if selection.question_id is not None and selection.question_id not in questions:
        raise ValueError("Unknown investigation question")
    return {
        **report, "mode": "langchain",
        "highlighted_evidence_ids": list(dict.fromkeys(selection.evidence_ids)),
        "highlighted_alternative_ids": list(dict.fromkeys(selection.alternative_ids)),
        "question": copy.deepcopy(questions.get(selection.question_id)),
    }


class InvestigationService:
    def __init__(self, store: LabStore, *, interpreter=None, timeout_seconds=12.0):
        self.store = store
        if interpreter is None and os.environ.get("LAB_AI_ENABLED", "false").lower() in {"true", "1"}:
            interpreter = _provider_investigate
        self.gate = InterpreterGate(interpreter, timeout_seconds)
        with store.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS lab_investigations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    request_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    anchor_choice_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    lease_token TEXT,
                    lease_until REAL NOT NULL DEFAULT 0,
                    response_json TEXT,
                    UNIQUE(session_id, request_id)
                );
                CREATE INDEX IF NOT EXISTS lab_investigations_session
                    ON lab_investigations(session_id, sequence);
            """)

    def close(self):
        self.gate.close()

    def _claim(self, session_id, body):
        request_hash = fingerprint(body.model_dump(mode="json", exclude={"request_id"}))
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # Replay precedes current-choice lookup: the saved result is immutable.
            existing = connection.execute(
                "SELECT * FROM lab_investigations WHERE session_id=? AND request_id=?",
                (session_id, str(body.request_id)),
            ).fetchone()
            if existing:
                if not secrets.compare_digest(existing["request_hash"], request_hash):
                    raise LabConflict("This investigation request identifier was used for different data")
                if existing["response_json"] is not None:
                    return {"response": json.loads(existing["response_json"])}
                if existing["lease_token"] and existing["lease_until"] > time.time():
                    raise LabConflict("This investigation is still processing. Retry after it finishes.")
            initial = connection.execute(
                "SELECT id FROM choices WHERE session_id=? AND stage='initial'", (session_id,)
            ).fetchone()
            if initial is None:
                raise LabConflict("Submit your initial choice before investigating alternatives")
            latest = connection.execute(
                "SELECT id,evaluation FROM choices WHERE session_id=? ORDER BY sequence DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if existing is None:
                count = connection.execute(
                    "SELECT COUNT(*) FROM lab_investigations WHERE session_id=?", (session_id,)
                ).fetchone()[0]
                if count >= 100:
                    raise LabConflict("This session has reached its 100-investigation limit")
                identity, created_at = str(uuid4()), now_iso()
                connection.execute(
                    """INSERT INTO lab_investigations
                    (id,session_id,request_id,request_hash,anchor_choice_id,created_at)
                    VALUES (?,?,?,?,?,?)""",
                    (identity, session_id, str(body.request_id), request_hash, latest["id"], created_at),
                )
            else:
                identity, created_at = existing["id"], existing["created_at"]
            lease = str(uuid4())
            connection.execute(
                """UPDATE lab_investigations SET lease_token=?,lease_until=?,anchor_choice_id=?
                WHERE id=?""", (lease, time.time() + 60, latest["id"], identity),
            )
            return {"id": identity, "created_at": created_at, "lease": lease,
                    "anchor": latest["id"], "evaluation": json.loads(latest["evaluation"])}

    def _complete(self, session_id, body, claim, report):
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM lab_investigations WHERE id=?", (claim["id"],)).fetchone()
            if row is None or row["lease_token"] != claim["lease"]:
                raise LabConflict("The investigation changed while processing. Please retry.")
            latest = connection.execute(
                "SELECT id FROM choices WHERE session_id=? ORDER BY sequence DESC LIMIT 1", (session_id,)
            ).fetchone()
            response = {
                **report, "investigation_id": claim["id"], "request_id": str(body.request_id),
                "created_at": claim["created_at"], "anchor_choice_id": claim["anchor"],
                "stale": latest["id"] != claim["anchor"],
            }
            if response["stale"]:
                response["notice"] = "A newer choice was saved during this investigation. Start a new investigation before applying proposals. " + response["notice"]
            connection.execute(
                "UPDATE lab_investigations SET response_json=?,lease_token=NULL,lease_until=0 WHERE id=?",
                (canonical(response), claim["id"]),
            )
            return response

    def _release(self, claim):
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE lab_investigations SET lease_token=NULL,lease_until=0 WHERE id=? AND lease_token=?",
                (claim["id"], claim["lease"]),
            )

    async def investigate(self, session, body):
        claim = await run_in_threadpool(self._claim, session["id"], body)
        if "response" in claim:
            return claim["response"]
        try:
            # Import lazily so route registration never projects or contacts providers.
            from spending_api.lab_financial import financial_summary

            wallet = await run_in_threadpool(financial_summary, session["scenario"])
            constraints = body.constraints.model_dump(mode="json") if body.constraints else claim["evaluation"]["constraints"]
            assignments = body.assignments if body.assignments is not None else claim["evaluation"]["assignments"]
            try:
                report = await run_in_threadpool(
                    investigate_decision, session["scenario"], constraints,
                    assignments=assignments, protected_constraints=body.protected_constraints,
                    allowed_travel_modes=body.allowed_travel_modes, wallet_summary=wallet,
                )
            except ValueError:
                raise HTTPException(422, "Investigation assignments or settings are invalid for this scenario") from None
            if self.gate.interpreter is not None:
                try:
                    selection = await self.gate.interpret({"report": copy.deepcopy(report), "question": body.message})
                    report = _apply_selection(report, selection)
                except Exception:
                    report = {**report, "mode": "fallback", "agent_notice": (
                        "The optional model was unavailable, busy, timed out, or returned an invalid selection. "
                        "Calculated evidence remains available."
                    )}
            return await run_in_threadpool(self._complete, session["id"], body, claim, report)
        finally:
            await run_in_threadpool(self._release, claim)


def register_investigation_routes(app, store: LabStore, *, interpreter=None, timeout_seconds=12.0):
    """Register one session-scoped route; caller must close app.state.lab_investigation."""
    service = InvestigationService(store, interpreter=interpreter, timeout_seconds=timeout_seconds)
    app.state.lab_investigation = service
    bearer = HTTPBearer(auto_error=False)

    @app.post("/api/v1/sessions/{session_id}/investigate")
    async def investigate(
        session_id: UUID, body: InvestigationRequest,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        session = await run_in_threadpool(
            store.session, str(session_id), credentials.credentials if credentials else None,
        )
        return await service.investigate(session, body)

    return service
