from __future__ import annotations

import json
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

from status.db.edit import persist_edited_entries
from status.db.models import EntrySource, Flag, Person, StatusEntry
from status.slack.blocks import (
    ACTION_EDIT,
    EDIT_MODAL_MAX_ENTRIES,
    build_edit_modal,
    parse_edit_submission_values,
)
from status.slack.handlers import register_handlers


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


def _entry(
    *,
    epic_key: str | None = "EET-1",
    outcome: str = "Original outcome.",
    ask: str | None = None,
) -> StatusEntry:
    return StatusEntry(
        entry_id=uuid4(),
        week_ending=date(2026, 8, 14),
        person_id="pilot",
        epic_key=epic_key,
        epic_name_snapshot="Pipeline" if epic_key else None,
        project="EET",
        state="progressing",
        outcome=outcome,
        ask=ask,
        source=EntrySource.DRAFTED.value,
        is_current=True,
        revision=1,
    )


def test_build_edit_modal_respects_slack_input_block_limit() -> None:
    entries = [_entry(epic_key=f"EET-{idx}") for idx in range(EDIT_MODAL_MAX_ENTRIES + 4)]
    modal = build_edit_modal(
        person_id="pilot",
        week_ending=date(2026, 8, 14),
        entries=entries,
        flags=[],
        channel="C123",
        message_ts="1234.5678",
    )
    input_blocks = [block for block in modal["blocks"] if block["type"] == "input"]
    assert len(input_blocks) <= 10
    assert len(input_blocks) == EDIT_MODAL_MAX_ENTRIES + 1


def test_build_edit_modal_does_not_prefill_additional_work_from_flag() -> None:
    flag = Flag(
        flag_id=uuid4(),
        week_ending=date(2026, 8, 14),
        person_id="pilot",
        flag_type="unticketed",
        message="Meetings and design reviews.",
        acknowledged=False,
    )
    modal = build_edit_modal(
        person_id="pilot",
        week_ending=date(2026, 8, 14),
        entries=[_entry()],
        flags=[flag],
        channel="C123",
        message_ts="1234.5678",
    )
    unticketed_block = next(
        block for block in modal["blocks"] if block.get("block_id") == "unticketed_work"
    )
    assert "initial_value" not in unticketed_block["element"]


def test_build_edit_modal_keeps_generated_unticketed_entry_editable() -> None:
    unticketed_entry = _entry(
        epic_key=None,
        outcome="Merged repository automation improvements.",
    )
    modal = build_edit_modal(
        person_id="pilot",
        week_ending=date(2026, 8, 14),
        entries=[unticketed_entry],
        flags=[],
        channel="C123",
        message_ts="1234.5678",
    )

    entry_block = next(
        block
        for block in modal["blocks"]
        if block.get("block_id") == f"entry_{unticketed_entry.entry_id}"
    )
    additional_block = next(
        block for block in modal["blocks"] if block.get("block_id") == "unticketed_work"
    )
    assert entry_block["element"]["initial_value"] == unticketed_entry.outcome
    assert "initial_value" not in additional_block["element"]


def test_parse_edit_submission_values_treats_cleared_field_as_drop() -> None:
    entry_id = str(uuid4())
    values = {
        f"entry_{entry_id}": {"outcome_value": {}},
    }
    edited, _ = parse_edit_submission_values(values, entry_ids=[entry_id])
    assert edited[entry_id] == ""


def test_parse_edit_submission_values_treats_missing_block_as_drop() -> None:
    entry_id = str(uuid4())
    edited, _ = parse_edit_submission_values({}, entry_ids=[entry_id])
    assert edited[entry_id] == ""


def test_parse_edit_submission_values_maps_entry_ids() -> None:
    entry_id = str(uuid4())
    values = {
        f"entry_{entry_id}": {"outcome_value": {"value": "Updated outcome."}},
        "unticketed_work": {"unticketed_value": {"value": "Side project"}},
    }
    edited, unticketed = parse_edit_submission_values(values, entry_ids=[entry_id])
    assert edited[entry_id] == "Updated outcome."
    assert unticketed == "Side project"


def test_build_edit_modal_has_missed_work_field() -> None:
    modal = build_edit_modal(
        person_id="pilot",
        week_ending=date(2026, 8, 14),
        entries=[_entry()],
        flags=[],
        channel="C123",
        message_ts="1234.5678",
    )
    missed_block = next(
        block for block in modal["blocks"] if block.get("block_id") == "unticketed_work"
    )
    assert missed_block["label"]["text"] == "Missed or additional work this week"
    assert "leadership_asks" not in str(modal)


def test_build_edit_modal_allows_manual_work_when_draft_is_empty() -> None:
    modal = build_edit_modal(
        person_id="pilot",
        week_ending=date(2026, 8, 14),
        entries=[],
        flags=[],
        channel="C123",
        message_ts="1234.5678",
    )

    input_blocks = [block for block in modal["blocks"] if block["type"] == "input"]
    metadata = json.loads(modal["private_metadata"])
    assert [block["block_id"] for block in input_blocks] == ["unticketed_work"]
    assert "no draft entries were generated" in modal["blocks"][0]["text"]["text"]
    assert metadata["entry_ids"] == []
    assert metadata["total_entries"] == 0


def test_edit_action_opens_modal_when_draft_is_empty() -> None:
    app = FakeSlackApp()
    register_handlers(app, bot_token="xoxb-test")
    ack = MagicMock()
    client = MagicMock()
    client.views_open.return_value = {"ok": True}
    person = Person(
        person_id="pilot",
        display_name="Pilot User",
        slack_user_id="U-PILOT",
    )
    body = {
        "actions": [
            {
                "value": json.dumps(
                    {"person_id": "pilot", "week_ending": "2026-08-14"}
                )
            }
        ],
        "user": {"id": "U-PILOT"},
        "channel": {"id": "D123"},
        "message": {"ts": "1234.5678"},
        "trigger_id": "trigger-123",
    }

    with (
        patch("status.slack.handlers.get_session") as session_context,
        patch("status.slack.handlers._authorize_person", return_value=person),
        patch("status.slack.handlers.get_current_drafts", return_value=[]),
        patch("status.slack.handlers.get_unacknowledged_flags", return_value=[]),
    ):
        session_context.return_value.__enter__.return_value = MagicMock()
        app.actions[ACTION_EDIT](ack, body, client)

    ack.assert_called_once_with()
    client.views_open.assert_called_once()
    modal = client.views_open.call_args.kwargs["view"]
    assert json.loads(modal["private_metadata"])["entry_ids"] == []
    client.chat_postEphemeral.assert_not_called()


def test_persist_edited_entries_creates_drafted_edited_revision() -> None:
    entry = _entry()
    session = MagicMock()
    session.flush = MagicMock()

    with patch("status.db.edit.get_current_drafts", return_value=[entry]):
        new_rows = persist_edited_entries(
            session,
            "pilot",
            date(2026, 8, 14),
            edited_outcomes={str(entry.entry_id): "Edited outcome."},
            unticketed_work=None,
            existing_unticketed_entry_id=None,
        )

    assert entry.is_current is False
    assert len(new_rows) == 1
    assert new_rows[0].source == EntrySource.DRAFTED_EDITED.value
    assert new_rows[0].outcome == "Edited outcome."
    assert new_rows[0].supersedes_entry_id == entry.entry_id
    assert new_rows[0].revision == 2
    session.add.assert_called_once()


def test_persist_edited_entries_drop_epic_supersedes_without_reinsert() -> None:
    entry = _entry()
    session = MagicMock()
    session.flush = MagicMock()

    with patch("status.db.edit.get_current_drafts", return_value=[entry]):
        new_rows = persist_edited_entries(
            session,
            "pilot",
            date(2026, 8, 14),
            edited_outcomes={str(entry.entry_id): ""},
            unticketed_work=None,
            existing_unticketed_entry_id=None,
        )

    assert entry.is_current is False
    assert new_rows == []
    session.add.assert_not_called()


def test_persist_edited_entries_adds_missed_work() -> None:
    entry = _entry()
    session = MagicMock()
    session.flush = MagicMock()

    with patch("status.db.edit.get_current_drafts", return_value=[entry]):
        new_rows = persist_edited_entries(
            session,
            "pilot",
            date(2026, 8, 14),
            edited_outcomes={str(entry.entry_id): entry.outcome},
            unticketed_work="Partner sync and design review.",
            existing_unticketed_entry_id=None,
        )

    assert len(new_rows) == 1
    assert new_rows[0].epic_key is None
    assert new_rows[0].outcome == "Partner sync and design review."
    assert new_rows[0].source == EntrySource.HUMAN_WRITTEN.value
    session.add.assert_called_once()


def test_persist_edited_entries_adds_manual_work_when_draft_is_empty() -> None:
    session = MagicMock()
    session.flush = MagicMock()

    with patch("status.db.edit.get_current_drafts", return_value=[]):
        new_rows = persist_edited_entries(
            session,
            "pilot",
            date(2026, 8, 14),
            edited_outcomes={},
            unticketed_work="Partner sync and design review.",
            existing_unticketed_entry_id=None,
        )

    assert len(new_rows) == 1
    assert new_rows[0].outcome == "Partner sync and design review."
    assert new_rows[0].source == EntrySource.HUMAN_WRITTEN.value
    session.add.assert_called_once_with(new_rows[0])
    session.flush.assert_called_once_with()


def test_persist_edited_entries_leaves_unchanged_rows_current() -> None:
    entry = _entry()
    session = MagicMock()
    session.flush = MagicMock()

    with patch("status.db.edit.get_current_drafts", return_value=[entry]):
        new_rows = persist_edited_entries(
            session,
            "pilot",
            date(2026, 8, 14),
            edited_outcomes={str(entry.entry_id): entry.outcome},
            unticketed_work=None,
            existing_unticketed_entry_id=None,
        )

    assert entry.is_current is True
    assert new_rows == []
    session.add.assert_not_called()
