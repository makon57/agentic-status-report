from __future__ import annotations

import json
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

from status.db.confirm import (
    confirm_draft_entries,
    mark_person_on_leave,
    record_draft_sent,
)
from status.db.models import Participation, Person, StatusEntry
from status.slack.blocks import (
    ACTION_CONFIRM,
    ACTION_PTO,
    build_draft_blocks,
    format_entry_text,
)
from status.slack.handlers import _authorize_person, register_handlers


class FakeSlackApp:
    def __init__(self) -> None:
        self.actions: dict[str, Any] = {}

    def action(self, action_id: str) -> Any:
        def decorator(callback: Any) -> Any:
            self.actions[action_id] = callback
            return callback

        return decorator

    def view(self, _callback_id: str) -> Any:
        return lambda callback: callback

    def command(self, _command: str) -> Any:
        return lambda callback: callback


def _confirm_body(*, slack_user_id: str = "U-PILOT") -> dict[str, Any]:
    return {
        "actions": [
            {
                "value": json.dumps(
                    {"person_id": "pilot", "week_ending": "2026-08-14"}
                )
            }
        ],
        "user": {"id": slack_user_id},
        "channel": {"id": "D123"},
        "message": {"ts": "1234.5678"},
    }


def test_authorize_person_requires_matching_slack_identity() -> None:
    session = MagicMock()
    person = Person(
        person_id="pilot",
        display_name="Pilot User",
        slack_user_id="U-PILOT",
    )

    with patch("status.slack.handlers.get_person", return_value=person):
        assert _authorize_person(session, "pilot", "U-PILOT") is person
        assert _authorize_person(session, "pilot", "U-OTHER") is None


def test_authorize_person_rejects_missing_slack_identity() -> None:
    session = MagicMock()
    person = Person(
        person_id="pilot",
        display_name="Pilot User",
        slack_user_id=None,
    )

    with patch("status.slack.handlers.get_person", return_value=person):
        assert _authorize_person(session, "pilot", "U-PILOT") is None


def test_confirm_rejects_unauthorized_slack_user() -> None:
    app = FakeSlackApp()
    register_handlers(app, bot_token="xoxb-test")
    ack = MagicMock()
    client = MagicMock()

    with (
        patch("status.slack.handlers.get_session") as session_context,
        patch("status.slack.handlers._authorize_person", return_value=None),
        patch("status.slack.handlers.confirm_draft_entries") as confirm,
        patch("status.slack.handlers._update_message") as update_message,
    ):
        session_context.return_value.__enter__.return_value = MagicMock()
        app.actions[ACTION_CONFIRM](
            ack,
            _confirm_body(slack_user_id="U-OTHER"),
            client,
        )

    ack.assert_called_once_with()
    confirm.assert_not_called()
    update_message.assert_not_called()
    assert "not authorized" in client.chat_postEphemeral.call_args.kwargs["text"]


def test_confirm_records_authorized_person() -> None:
    app = FakeSlackApp()
    register_handlers(app, bot_token="xoxb-test")
    ack = MagicMock()
    client = MagicMock()
    person = Person(
        person_id="pilot",
        display_name="Pilot User",
        slack_user_id="U-PILOT",
    )

    with (
        patch("status.slack.handlers.get_session") as session_context,
        patch("status.slack.handlers._authorize_person", return_value=person) as authorize,
        patch("status.slack.handlers.confirm_draft_entries") as confirm,
        patch("status.slack.handlers._update_message") as update_message,
    ):
        session = MagicMock()
        session_context.return_value.__enter__.return_value = session
        app.actions[ACTION_CONFIRM](ack, _confirm_body(), client)

    authorize.assert_called_once_with(session, "pilot", "U-PILOT")
    confirm.assert_called_once_with(
        session,
        "pilot",
        date(2026, 8, 14),
        confirmed_by="pilot",
    )
    update_message.assert_called_once()


def test_format_entry_text_includes_state_and_outcome() -> None:
    entry = StatusEntry(
        entry_id=uuid4(),
        week_ending=date(2026, 8, 14),
        person_id="pilot",
        epic_key="EET-5493",
        epic_name_snapshot="OpenShift Cluster Management Bot",
        project="EET",
        state="progressing",
        outcome="Shipped destroy command.",
        source="drafted",
    )
    text = format_entry_text(entry)
    assert "In progress" in text
    assert "EET-5493" in text
    assert "OpenShift Cluster Management Bot" in text
    assert "Shipped destroy command." in text


def test_build_draft_blocks_lists_entries_before_flags() -> None:
    entry = StatusEntry(
        entry_id=uuid4(),
        week_ending=date(2026, 8, 14),
        person_id="pilot",
        epic_key="EET-5519",
        epic_name_snapshot="Agentic Weekly Status Pipeline",
        project="EET",
        state="shipped",
        outcome="Shipped M1.",
        source="drafted",
    )
    flag = MagicMock(message="Gap note.")
    blocks = build_draft_blocks(
        person_id="pilot",
        display_name="Pilot User",
        week_ending=date(2026, 8, 14),
        entries=[entry],
        flags=[flag],
    )
    section_texts = [
        block["text"]["text"]
        for block in blocks
        if block.get("type") == "section" and "text" in block
    ]
    entry_index = next(i for i, text in enumerate(section_texts) if "EET-5519" in text)
    flag_index = next(i for i, text in enumerate(section_texts) if "Gap note." in text)
    assert entry_index < flag_index


def test_build_draft_blocks_hides_internal_collector_diagnostics() -> None:
    flags = [
        MagicMock(message="All merged PRs lacked Jira links; assignment needs confirmation."),
        MagicMock(message="The merged work had no corresponding Jira ticket."),
        MagicMock(message="Did the deployment produce a result worth reporting?"),
    ]
    blocks = build_draft_blocks(
        person_id="pilot",
        display_name="Pilot User",
        week_ending=date(2026, 8, 14),
        entries=[],
        flags=flags,
    )

    rendered = str(blocks)
    assert "lacked Jira links" not in rendered
    assert "no corresponding Jira ticket" not in rendered
    assert "deployment produce a result" in rendered


def test_build_draft_blocks_includes_confirm_button() -> None:
    entry = StatusEntry(
        entry_id=uuid4(),
        week_ending=date(2026, 8, 14),
        person_id="pilot",
        project="EET",
        state="shipped",
        outcome="Done.",
        source="drafted",
    )
    blocks = build_draft_blocks(
        person_id="pilot",
        display_name="Pilot User",
        week_ending=date(2026, 8, 14),
        entries=[entry],
        flags=[],
    )
    actions = [b for b in blocks if b.get("type") == "actions"]
    assert actions
    button_ids = [el["action_id"] for el in actions[0]["elements"]]
    assert ACTION_CONFIRM in button_ids
    assert ACTION_PTO in button_ids
    pto_button = next(
        element
        for element in actions[0]["elements"]
        if element["action_id"] == ACTION_PTO
    )
    assert pto_button["confirm"]["confirm"]["text"] == "Mark as PTO"


def test_build_draft_blocks_on_leave_omits_entries_and_actions() -> None:
    entry = StatusEntry(
        entry_id=uuid4(),
        week_ending=date(2026, 8, 14),
        person_id="pilot",
        project="EET",
        state="shipped",
        outcome="This should not be displayed.",
        source="drafted",
    )
    blocks = build_draft_blocks(
        person_id="pilot",
        display_name="Pilot User",
        week_ending=date(2026, 8, 14),
        entries=[entry],
        flags=[MagicMock(message="This flag should not be displayed.")],
        on_leave=True,
    )

    rendered = str(blocks)
    assert "On PTO" in rendered
    assert "This should not be displayed" not in rendered
    assert "This flag should not be displayed" not in rendered
    assert not any(block.get("type") == "actions" for block in blocks)


def test_build_draft_blocks_confirmed_omits_actions() -> None:
    blocks = build_draft_blocks(
        person_id="pilot",
        display_name="Pilot User",
        week_ending=date(2026, 8, 14),
        entries=[],
        flags=[],
        confirmed=True,
    )
    assert not any(b.get("type") == "actions" for b in blocks)


def test_confirm_draft_entries_sets_confirmed_at() -> None:
    session = MagicMock()
    entry = MagicMock(
        confirmed_at=None,
        confirmed_by=None,
        is_current=True,
    )
    session.scalars.return_value.all.return_value = [entry]
    session.get.return_value = None

    rows = confirm_draft_entries(
        session,
        "pilot",
        date(2026, 8, 14),
        confirmed_by="pilot",
    )

    assert len(rows) == 1
    assert entry.confirmed_at is not None
    assert entry.confirmed_by == "pilot"
    session.add.assert_called_once()


def test_record_draft_sent_creates_participation() -> None:
    session = MagicMock()
    session.get.return_value = None

    row = record_draft_sent(session, "pilot", date(2026, 8, 14))

    assert isinstance(row, Participation)
    assert row.status == "sent"
    session.add.assert_called_once()


def test_record_draft_sent_preserves_on_leave() -> None:
    session = MagicMock()
    row = Participation(
        person_id="pilot",
        week_ending=date(2026, 8, 14),
        status="on_leave",
    )
    session.get.return_value = row

    result = record_draft_sent(session, "pilot", date(2026, 8, 14))

    assert result is row
    assert row.status == "on_leave"
    session.flush.assert_not_called()


def test_mark_person_on_leave_records_participation() -> None:
    session = MagicMock()
    session.get.return_value = None

    row = mark_person_on_leave(
        session,
        "pilot",
        date(2026, 8, 14),
        marked_by="pilot",
    )

    assert isinstance(row, Participation)
    assert row.status == "on_leave"
    assert row.confirmed_at is not None
    assert row.note == "PTO recorded by pilot"
    session.add.assert_called_once()


def test_pto_records_authorized_person() -> None:
    app = FakeSlackApp()
    register_handlers(app, bot_token="xoxb-test")
    ack = MagicMock()
    client = MagicMock()
    person = Person(
        person_id="pilot",
        display_name="Pilot User",
        slack_user_id="U-PILOT",
    )

    with (
        patch("status.slack.handlers.get_session") as session_context,
        patch("status.slack.handlers._authorize_person", return_value=person),
        patch("status.slack.handlers.mark_person_on_leave") as mark_on_leave,
        patch("status.slack.handlers._update_message") as update_message,
    ):
        session = MagicMock()
        session_context.return_value.__enter__.return_value = session
        app.actions[ACTION_PTO](ack, _confirm_body(), client)

    ack.assert_called_once_with()
    mark_on_leave.assert_called_once_with(
        session,
        "pilot",
        date(2026, 8, 14),
        marked_by="pilot",
    )
    assert update_message.call_args.kwargs["on_leave"] is True
    assert "PTO recorded" in client.chat_postEphemeral.call_args.kwargs["text"]


def test_pto_rejects_unauthorized_slack_user() -> None:
    app = FakeSlackApp()
    register_handlers(app, bot_token="xoxb-test")
    ack = MagicMock()
    client = MagicMock()

    with (
        patch("status.slack.handlers.get_session") as session_context,
        patch("status.slack.handlers._authorize_person", return_value=None),
        patch("status.slack.handlers.mark_person_on_leave") as mark_on_leave,
        patch("status.slack.handlers._update_message") as update_message,
    ):
        session_context.return_value.__enter__.return_value = MagicMock()
        app.actions[ACTION_PTO](
            ack,
            _confirm_body(slack_user_id="U-OTHER"),
            client,
        )

    mark_on_leave.assert_not_called()
    update_message.assert_not_called()
    assert "not authorized" in client.chat_postEphemeral.call_args.kwargs["text"]
