"""Bounded, ephemeral USER question context; never a store of financial evidence.

All methods are synchronous and used on the application's event loop. A lease
owns one candidate turn until finish/rollback. Removing an entry revokes it, even
while its caller still owns a lease; no late completion can put it back.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, get_args

MAX_CONVERSATION_TURNS = 4
MAX_CONVERSATION_BYTES = 8192
CONVERSATION_TTL_SECONDS = 600
MAX_CONVERSATIONS = 8
MAX_CONTEXT_QUESTION_CHARS = 2000
CONVERSATION_SWEEP_SECONDS = 30

MissingInput = Literal[
    "date_range",
    "location",
    "monthly_rent_budget",
    "bedrooms",
    "minimum_square_feet",
    "housing_preferences",
    "financial_goal",
    "comparison_options",
    "household_size",
    "income",
    "savings_target",
]
MISSING_INPUT_CODES = frozenset(get_args(MissingInput))
RESET_MESSAGE = (
    "This conversation can no longer be continued. "
    "Start a new question with the needed details."
)


class ConversationResetError(ValueError):
    def __init__(self):
        super().__init__(RESET_MESSAGE)


class ConversationBusyError(Exception):
    def __init__(self):
        super().__init__("Conversation context is busy; retry shortly")


@dataclass(frozen=True)
class ConversationScope:
    authority: str
    source: str
    selector: str
    customer_id: str
    source_hash: str


@dataclass(frozen=True)
class ConversationReference:
    id: str
    revision: int


@dataclass(frozen=True)
class QuestionContext:
    questions: tuple[str, ...] = ()
    needed_inputs: tuple[str, ...] = ()

    def canonical_json(self) -> str:
        return json.dumps(
            {"questions": self.questions, "needed_inputs": self.needed_inputs},
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )

    def byte_count(self) -> int:
        return len(self.canonical_json().encode("utf-8"))


@dataclass(eq=False)
class _Entry:
    id: str
    scope: ConversationScope
    created_at: float
    last_accepted_at: float
    deadline: float
    expires_at: str
    context: QuestionContext = field(default_factory=QuestionContext)
    revision: int = 0
    pending: ConversationLease | None = None
    closed_reason: str | None = None


@dataclass(eq=False)
class ConversationLease:
    history: QuestionContext
    _entry: _Entry = field(repr=False)
    _question: str = field(repr=False)
    _finished: bool = field(default=False, repr=False)


class ConversationStore:
    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ):
        self._clock = clock if clock is not None else time.monotonic
        self._wall_clock = wall_clock if wall_clock is not None else lambda: datetime.now(UTC)
        self._entries: dict[str, _Entry] = {}
        self._closed = False

    def _drop(self, entry: _Entry, reason: str) -> None:
        if self._entries.get(entry.id) is entry:
            del self._entries[entry.id]
        if entry.closed_reason is None:
            entry.closed_reason = reason

    def sweep(self) -> None:
        now = self._clock()
        for entry in tuple(self._entries.values()):
            if now >= entry.deadline:
                self._drop(entry, "expired")

    def close(self) -> None:
        self._closed = True
        for entry in tuple(self._entries.values()):
            self._drop(entry, "cleared")

    def forget(self, identifier: str, authority: str) -> None:
        self.sweep()
        entry = self._entries.get(identifier)
        if entry is not None and secrets.compare_digest(entry.scope.authority, authority):
            self._drop(entry, "cleared")

    @staticmethod
    def _active(entry: _Entry) -> dict:
        return {
            "state": "active",
            "id": entry.id,
            "revision": entry.revision,
            "turns_used": entry.revision,
            "expires_at": entry.expires_at,
        }

    @staticmethod
    def _inactive(entry: _Entry, *, turns_used: int | None = None) -> dict:
        return {
            "state": "closed",
            "reason": entry.closed_reason or "cleared",
            "turns_used": entry.revision if turns_used is None else turns_used,
        }

    @staticmethod
    def _check_context(context: QuestionContext) -> None:
        try:
            if (
                len(context.questions) > MAX_CONVERSATION_TURNS
                or context.byte_count() > MAX_CONVERSATION_BYTES
            ):
                raise ConversationResetError()
        except UnicodeError:
            raise ConversationResetError() from None

    def begin(
        self,
        question: str,
        scope: ConversationScope,
        reference: ConversationReference | None = None,
    ) -> ConversationLease:
        if self._closed:
            raise ConversationResetError()
        if (
            not isinstance(question, str)
            or not 1 <= len(question.strip()) <= MAX_CONTEXT_QUESTION_CHARS
        ):
            raise ValueError("Invalid conversation question")
        question = question.strip()
        if not isinstance(scope, ConversationScope) or any(
            not isinstance(value, str) or not value
            for value in (
                scope.authority,
                scope.source,
                scope.selector,
                scope.customer_id,
                scope.source_hash,
            )
        ):
            raise ValueError("Invalid conversation scope")
        self.sweep()
        if reference is not None:
            if (
                not isinstance(reference, ConversationReference)
                or not isinstance(reference.id, str)
                or type(reference.revision) is not int
                or reference.revision < 1
            ):
                raise ConversationResetError()
            entry = self._entries.get(reference.id)
            if entry is None or entry.scope.authority != scope.authority:
                raise ConversationResetError()
            if entry.revision != reference.revision:
                raise ConversationResetError()
            if entry.scope != scope:
                self._drop(entry, "cleared")
                raise ConversationResetError()
            if entry.pending is not None:
                raise ConversationBusyError()
            self._check_context(
                QuestionContext(entry.context.questions + (question,), entry.context.needed_inputs)
            )
        else:
            self._check_context(QuestionContext((question,), ()))
            now = self._clock()
            wall_now = self._wall_clock()
            if wall_now.tzinfo is None or wall_now.utcoffset() is None:
                raise ValueError("Conversation expiry requires an aware clock")
            expires_at = (
                wall_now.astimezone(UTC) + timedelta(seconds=CONVERSATION_TTL_SECONDS)
            ).isoformat().replace("+00:00", "Z")
            if len(self._entries) >= MAX_CONVERSATIONS:
                evictable = [row for row in self._entries.values() if row.pending is None]
                if not evictable:
                    raise ConversationBusyError()
                self._drop(min(evictable, key=lambda row: row.last_accepted_at), "cleared")
            identifier = secrets.token_hex(32)
            while identifier in self._entries:
                identifier = secrets.token_hex(32)
            entry = _Entry(
                id=identifier,
                scope=scope,
                created_at=now,
                last_accepted_at=now,
                deadline=now + CONVERSATION_TTL_SECONDS,
                expires_at=expires_at,
            )
            self._entries[identifier] = entry
        lease = ConversationLease(history=entry.context, _entry=entry, _question=question)
        entry.pending = lease
        return lease

    def preflight(
        self, question: str, reference: ConversationReference, authority: str
    ) -> None:
        """Reject a known invalid continuation before loading its financial source.

        begin repeats these checks after the fresh snapshot is bound; a clear
        request can revoke the entry while source loading awaits I/O.
        """
        self.sweep()
        if (
            self._closed
            or not isinstance(reference, ConversationReference)
            or not isinstance(reference.id, str)
            or type(reference.revision) is not int
            or reference.revision < 1
        ):
            raise ConversationResetError()
        entry = self._entries.get(reference.id)
        if (
            entry is None
            or entry.scope.authority != authority
            or entry.revision != reference.revision
        ):
            raise ConversationResetError()
        if entry.pending is not None:
            raise ConversationBusyError()
        if (
            not isinstance(question, str)
            or not 1 <= len(question.strip()) <= MAX_CONTEXT_QUESTION_CHARS
        ):
            raise ConversationResetError()
        self._check_context(
            QuestionContext(
                entry.context.questions + (question.strip(),), entry.context.needed_inputs
            )
        )

    def rollback(self, lease: ConversationLease) -> None:
        if lease._finished:
            return
        lease._finished = True
        entry = lease._entry
        if entry.pending is lease:
            entry.pending = None
            if entry.revision == 0:
                self._drop(entry, "cleared")

    def finish(
        self,
        lease: ConversationLease,
        status: str,
        needed_inputs: tuple[str, ...] | list[str] = (),
    ) -> dict | None:
        if lease._finished:
            raise ConversationResetError()
        entry = lease._entry
        try:
            if (
                not isinstance(status, str)
                or status not in {"answered", "clarification", "limited", "unavailable"}
                or not isinstance(needed_inputs, (tuple, list))
                or len(needed_inputs) > 6
                or any(
                    not isinstance(code, str) or code not in MISSING_INPUT_CODES
                    for code in needed_inputs
                )
                or status == "clarification" and not needed_inputs
                or status == "answered" and needed_inputs
            ):
                raise ConversationResetError()
            self.sweep()
            if self._entries.get(entry.id) is not entry:
                lease._finished = True
                entry.pending = None
                return self._inactive(entry)
            if entry.pending is not lease:
                raise ConversationResetError()
            if status in {"limited", "unavailable"}:
                self.rollback(lease)
                return None if entry.revision == 0 else self._active(entry)
            candidate = QuestionContext(
                entry.context.questions + (lease._question,),
                tuple(needed_inputs),
            )
            turns_used = entry.revision + 1
            if candidate.byte_count() > MAX_CONVERSATION_BYTES:
                self._drop(entry, "byte_limit")
                result = self._inactive(entry, turns_used=turns_used)
            else:
                entry.context = candidate
                entry.revision = turns_used
                entry.last_accepted_at = self._clock()
                if turns_used >= MAX_CONVERSATION_TURNS:
                    self._drop(entry, "turn_limit")
                    result = self._inactive(entry)
                else:
                    result = self._active(entry)
            entry.pending = None
            lease._finished = True
            return result
        except (ValueError, TypeError):
            self.rollback(lease)
            raise
