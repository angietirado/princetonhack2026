"""Independent bounded-context lifecycle tests; no models, data files or network."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from spending_api import conversation
from spending_api.conversation import (
    ConversationBusyError,
    ConversationReference,
    ConversationResetError,
    ConversationScope,
    ConversationStore,
    QuestionContext,
)

ORIGIN = datetime(2026, 11, 14, 12, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.elapsed = 0.0
        self.wall_offset = 0.0

    def monotonic(self):
        return self.elapsed

    def wall(self):
        return ORIGIN + timedelta(seconds=self.elapsed + self.wall_offset)

    def advance(self, seconds):
        self.elapsed += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    return ConversationStore(clock=clock.monotonic, wall_clock=clock.wall)


def scope(**changes):
    values = {
        "authority": "synthetic-auth-epoch",
        "source": "fixture",
        "selector": "synthetic-source-selector",
        "customer_id": "synthetic-customer",
        "source_hash": "a" * 64,
    }
    return ConversationScope(**{**values, **changes})


def reference(receipt):
    assert receipt["state"] == "active"
    return ConversationReference(id=receipt["id"], revision=receipt["revision"])


def start(store, question="Compare the two explicit periods.", *, owner=None):
    lease = store.begin(question, owner or scope())
    return store.finish(lease, status="answered")


def canonical_size(questions, needed_inputs=()):
    """The documented UTF-8 wire contract, independent of the store's accounting."""
    return len(json.dumps(
        {"questions": list(questions), "needed_inputs": list(needed_inputs)},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8"))


def test_new_context_has_no_history_and_follow_up_gets_only_accepted_questions(store):
    first = store.begin("Compare November 1-7 and November 8-14.", scope())
    assert isinstance(first.history, QuestionContext)
    assert first.history.questions == () and first.history.needed_inputs == ()
    receipt = store.finish(first, status="answered")
    assert receipt["revision"] == receipt["turns_used"] == 1
    second = store.begin("What about groceries in those dates?", scope(), reference(receipt))
    assert second.history.questions == ("Compare November 1-7 and November 8-14.",)
    assert second.history.needed_inputs == ()
    next_receipt = store.finish(second, status="answered")
    assert next_receipt["id"] == receipt["id"]
    assert next_receipt["revision"] == next_receipt["turns_used"] == 2


def test_history_value_is_immutable_and_does_not_change_after_later_commit(store):
    first = start(store, "Original goal")
    lease = store.begin("Follow-up", scope(), reference(first))
    history = lease.history
    with pytest.raises((AttributeError, TypeError, ValueError)):
        history.questions = ("Mutated",)
    receipt = store.finish(lease, status="answered")
    assert history.questions == ("Original goal",)
    next_lease = store.begin("Another question", scope(), reference(receipt))
    assert next_lease.history.questions == ("Original goal", "Follow-up")
    assert next_lease.history.needed_inputs == ()


def test_clarification_preserves_goal_and_replaces_missing_input_codes(store):
    first = store.begin("Compare my spending.", scope())
    receipt = store.finish(first, status="clarification", needed_inputs=("date_range",))
    second = store.begin("Use November 1-7 and November 8-14.", scope(), reference(receipt))
    assert second.history.questions == ("Compare my spending.",)
    assert second.history.needed_inputs == ("date_range",)
    receipt = store.finish(second, status="clarification", needed_inputs=("financial_goal",))
    third = store.begin("Help me understand the difference.", scope(), reference(receipt))
    assert third.history.needed_inputs == ("financial_goal",)
    assert third.history.questions == (
        "Compare my spending.", "Use November 1-7 and November 8-14.",
    )


@pytest.mark.parametrize("status", ["limited", "unavailable"])
def test_unsuccessful_existing_turn_preserves_committed_revision_and_history(store, status):
    first = start(store, "Original goal")
    pending = store.begin("Failed attempt must not be remembered", scope(), reference(first))
    receipt = store.finish(pending, status=status)
    assert receipt == first
    retry = store.begin("Explicit retry", scope(), reference(receipt))
    assert retry.history.questions == ("Original goal",)
    assert retry.history.needed_inputs == ()


@pytest.mark.parametrize("status", ["limited", "unavailable"])
def test_unsuccessful_first_turn_retains_no_conversation(store, monkeypatch, status):
    monkeypatch.setattr(conversation, "MAX_CONVERSATIONS", 1)
    pending = store.begin("Unsuccessful first question", scope())
    assert store.finish(pending, status=status) is None
    fresh = store.begin("Fresh question", scope())
    assert fresh.history.questions == ()


def test_rollback_restores_existing_revision_without_pending_question(store):
    first = start(store, "Committed goal")
    pending = store.begin("Cancelled question", scope(), reference(first))
    store.rollback(pending)
    retry = store.begin("Replacement question", scope(), reference(first))
    assert retry.history.questions == ("Committed goal",)
    result = store.finish(retry, status="answered")
    third = store.begin("Next", scope(), reference(result))
    assert third.history.questions == ("Committed goal", "Replacement question")


def test_rollback_of_first_turn_releases_pending_capacity(store, monkeypatch):
    monkeypatch.setattr(conversation, "MAX_CONVERSATIONS", 1)
    pending = store.begin("Cancelled first question", scope())
    store.rollback(pending)
    replacement = store.begin("Replacement", scope())
    assert replacement.history.questions == ()


def test_fourth_accepted_turn_closes_without_silently_dropping_original_goal(store):
    receipt = start(store, "Original goal")
    active_reference = None
    for number in range(2, 5):
        active_reference = reference(receipt)
        lease = store.begin(f"Follow-up {number}", scope(), active_reference)
        assert lease.history.questions[0] == "Original goal"
        assert len(lease.history.questions) == number - 1
        receipt = store.finish(lease, status="answered")
    assert receipt == {"state": "closed", "reason": "turn_limit", "turns_used": 4}
    with pytest.raises(ConversationResetError):
        store.begin("Fifth question", scope(), active_reference)


def test_canonical_utf8_byte_boundary_includes_multibyte_and_escaped_text(store, monkeypatch):
    questions = ("Café\n\"goal\"", "Follow-up é")
    first = start(store, questions[0])
    monkeypatch.setattr(conversation, "MAX_CONVERSATION_BYTES", canonical_size(questions))
    exact = store.begin(questions[1], scope(), reference(first))
    assert exact.history.questions == (questions[0],)
    store.rollback(exact)
    monkeypatch.setattr(conversation, "MAX_CONVERSATION_BYTES", canonical_size(questions) - 1)
    with pytest.raises(ConversationResetError):
        store.begin(questions[1], scope(), reference(first))


def test_existing_missing_codes_count_toward_continuation_admission(store, monkeypatch):
    first = store.begin("Goal", scope())
    receipt = store.finish(first, status="clarification", needed_inputs=("date_range",))
    size = canonical_size(("Goal", "2026-11-01 through 2026-11-07"), ("date_range",))
    monkeypatch.setattr(conversation, "MAX_CONVERSATION_BYTES", size - 1)
    with pytest.raises(ConversationResetError):
        store.begin("2026-11-01 through 2026-11-07", scope(), reference(receipt))


def test_final_missing_code_growth_closes_valid_result_instead_of_truncating(store, monkeypatch):
    monkeypatch.setattr(conversation, "MAX_CONVERSATION_BYTES", canonical_size(("Goal",)))
    pending = store.begin("Goal", scope())
    receipt = store.finish(pending, status="clarification", needed_inputs=("date_range",))
    assert receipt == {"state": "closed", "reason": "byte_limit", "turns_used": 1}


def test_ttl_is_absolute_and_not_extended_by_a_follow_up(store, clock):
    first = start(store)
    expiry = datetime.fromisoformat(first["expires_at"].replace("Z", "+00:00"))
    assert expiry == ORIGIN + timedelta(seconds=600)
    clock.advance(599)
    pending = store.begin("Follow-up", scope(), reference(first))
    second = store.finish(pending, status="answered")
    assert second["expires_at"] == first["expires_at"]
    clock.advance(1)
    with pytest.raises(ConversationResetError):
        store.begin("Expired", scope(), reference(second))


def test_wall_clock_jump_does_not_expire_an_unelapsed_monotonic_lease(store, clock):
    first = start(store)
    clock.wall_offset = 86400
    lease = store.begin("Same monotonic lifetime", scope(), reference(first))
    assert lease.history.questions
    second = store.finish(lease, status="answered")
    assert second["expires_at"] == first["expires_at"]


def test_expiry_during_pending_turn_does_not_resurrect_context(store, clock):
    first = start(store)
    pending = store.begin("Pending question", scope(), reference(first))
    clock.advance(600)
    store.sweep()
    result = store.finish(pending, status="answered")
    assert result["state"] == "closed" and result["reason"] == "expired"
    assert result["turns_used"] == 1
    with pytest.raises(ConversationResetError):
        store.begin("Late continuation", scope(), reference(first))


@pytest.mark.parametrize("field,value", [
    ("authority", "other-auth-epoch"),
    ("source", "nessie"),
    ("selector", "other-selector"),
    ("customer_id", "other-customer"),
    ("source_hash", "b" * 64),
])
def test_every_scope_component_must_match_before_history_is_returned(store, field, value):
    first = start(store)
    with pytest.raises(ConversationResetError) as error:
        store.begin("Never inherit this history", scope(**{field: value}), reference(first))
    assert value not in str(error.value)


def test_equal_empty_snapshot_hashes_do_not_replace_customer_scope(store):
    empty_hash = "0" * 64
    first_scope = scope(source="nessie", source_hash=empty_hash, customer_id="first-customer")
    first = start(store, owner=first_scope)
    other_scope = scope(source="nessie", source_hash=empty_hash, customer_id="second-customer")
    with pytest.raises(ConversationResetError):
        store.begin("Same empty hash, different customer", other_scope, reference(first))


def test_old_revision_cannot_be_replayed_after_a_successful_commit(store):
    first = start(store)
    old_reference = reference(first)
    lease = store.begin("Second turn", scope(), old_reference)
    second = store.finish(lease, status="answered")
    with pytest.raises(ConversationResetError):
        store.begin("Replay of second turn", scope(), old_reference)
    current = store.begin("Third turn", scope(), reference(second))
    assert current.history.questions[-1] == "Second turn"


def test_forget_during_pending_turn_prevents_commit_and_history_resurrection(store):
    first = start(store, "Remembered goal")
    pending = store.begin("Pending follow-up", scope(), reference(first))
    store.forget(first["id"], scope().authority)
    result = store.finish(pending, status="answered")
    assert result["state"] == "closed" and result["reason"] == "cleared"
    assert result["turns_used"] == 1
    with pytest.raises(ConversationResetError):
        store.begin("Late follow-up", scope(), reference(first))


@pytest.mark.parametrize("status", ["limited", "unavailable"])
def test_failed_pending_turn_cannot_restore_a_forgotten_active_receipt(store, status):
    first = start(store)
    pending = store.begin("Pending follow-up", scope(), reference(first))
    store.forget(first["id"], scope().authority)
    result = store.finish(pending, status=status)
    assert result["state"] == "closed" and result["reason"] == "cleared"
    assert result["turns_used"] == 1


def test_forget_with_wrong_authority_does_not_erase_another_context(store):
    first = start(store)
    store.forget(first["id"], "wrong-authority")
    pending = store.begin("Authorized follow-up", scope(), reference(first))
    assert pending.history.questions


def test_forgetting_an_old_id_does_not_erase_a_new_context(store):
    first = start(store, "First goal")
    store.forget(first["id"], scope().authority)
    second = start(store, "Second goal")
    assert second["id"] != first["id"]
    store.forget(first["id"], scope().authority)
    lease = store.begin("Follow-up", scope(), reference(second))
    assert lease.history.questions == ("Second goal",)


def test_capacity_evicts_least_recent_nonpending_context(store, monkeypatch, clock):
    monkeypatch.setattr(conversation, "MAX_CONVERSATIONS", 2)
    first = start(store, "First")
    clock.advance(1)
    second = start(store, "Second")
    clock.advance(1)
    lease = store.begin("Refresh first usage", scope(), reference(first))
    first = store.finish(lease, status="answered")
    clock.advance(1)
    third = start(store, "Third")
    assert third["id"] not in {first["id"], second["id"]}
    with pytest.raises(ConversationResetError):
        store.begin("Evicted", scope(), reference(second))
    surviving = store.begin("Still present", scope(), reference(first))
    assert surviving.history.questions[0] == "First"


def test_pending_context_is_never_evicted_to_admit_new_work(store, monkeypatch, clock):
    monkeypatch.setattr(conversation, "MAX_CONVERSATIONS", 2)
    first = start(store, "First")
    pending = store.begin("Pending first", scope(), reference(first))
    clock.advance(1)
    second = start(store, "Second")
    clock.advance(1)
    start(store, "Third")
    result = store.finish(pending, status="answered")
    assert result["state"] == "active" and result["id"] == first["id"]
    with pytest.raises(ConversationResetError):
        store.begin("Evicted second", scope(), reference(second))


def test_all_pending_capacity_fails_busy_without_stealing_a_lease(store, monkeypatch):
    monkeypatch.setattr(conversation, "MAX_CONVERSATIONS", 2)
    first = store.begin("First pending", scope())
    second = store.begin("Second pending", scope())
    with pytest.raises(ConversationBusyError):
        store.begin("Third cannot start", scope())
    first_result = store.finish(first, status="answered")
    second_result = store.finish(second, status="answered")
    assert first_result["state"] == second_result["state"] == "active"
    assert first_result["id"] != second_result["id"]


def test_a_second_begin_for_the_same_pending_revision_is_busy(store):
    first = start(store, "Original")
    pending = store.begin("One pending question", scope(), reference(first))
    with pytest.raises(ConversationBusyError):
        store.begin("Competing question", scope(), reference(first))
    result = store.finish(pending, status="answered")
    next_lease = store.begin("Next", scope(), reference(result))
    assert next_lease.history.questions == ("Original", "One pending question")


@pytest.mark.parametrize("status", ["answered", "limited", "unavailable"])
def test_a_finished_lease_cannot_commit_again(store, status):
    first = start(store, "Original")
    pending = store.begin("One candidate", scope(), reference(first))
    receipt = store.finish(pending, status=status)
    with pytest.raises(ConversationResetError):
        store.finish(pending, status="answered")
    next_lease = store.begin("Next", scope(), reference(receipt))
    expected = ("Original", "One candidate") if status == "answered" else ("Original",)
    assert next_lease.history.questions == expected


def test_a_rolled_back_lease_cannot_commit_later(store):
    first = start(store, "Original")
    pending = store.begin("Cancelled", scope(), reference(first))
    store.rollback(pending)
    with pytest.raises(ConversationResetError):
        store.finish(pending, status="answered")
    retry = store.begin("Replacement", scope(), reference(first))
    assert retry.history.questions == ("Original",)


@pytest.mark.parametrize("status,codes", [
    ("clarification", ()),
    ("clarification", ("private-invalid-code",)),
    ("clarification", ("date_range", 42)),
    ("clarification", (
        "date_range", "location", "monthly_rent_budget", "bedrooms",
        "minimum_square_feet", "housing_preferences", "financial_goal",
    )),
    ("answered", ("date_range",)),
    ("unrecognized-status", ()),
])
def test_invalid_finish_rolls_back_without_poisoning_committed_history(store, status, codes):
    first = store.begin("Original goal", scope())
    receipt = store.finish(first, status="clarification", needed_inputs=("date_range",))
    pending = store.begin("Invalid candidate", scope(), reference(receipt))
    with pytest.raises(ConversationResetError) as error:
        store.finish(pending, status=status, needed_inputs=codes)
    assert "private-invalid-code" not in str(error.value)
    retry = store.begin("Retry with explicit dates", scope(), reference(receipt))
    assert retry.history.questions == ("Original goal",)
    assert retry.history.needed_inputs == ("date_range",)


@pytest.mark.parametrize("status", ["limited", "unavailable"])
def test_failed_turn_does_not_replace_previously_requested_input_codes(store, status):
    first = store.begin("Original goal", scope())
    receipt = store.finish(first, status="clarification", needed_inputs=("date_range",))
    pending = store.begin("Failed candidate", scope(), reference(receipt))
    unchanged = store.finish(pending, status=status, needed_inputs=("financial_goal",))
    assert unchanged == receipt
    retry = store.begin("Try again", scope(), reference(unchanged))
    assert retry.history.needed_inputs == ("date_range",)
    assert retry.history.questions == ("Original goal",)


def test_close_is_terminal_for_new_and_existing_conversations(store):
    first = start(store)
    store.close()
    with pytest.raises(ConversationResetError):
        store.begin("New after close", scope())
    with pytest.raises(ConversationResetError):
        store.begin("Existing after close", scope(), reference(first))
    store.sweep()
    with pytest.raises(ConversationResetError):
        store.begin("Sweep cannot reopen closed store", scope())
