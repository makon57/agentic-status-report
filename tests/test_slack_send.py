from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from status.db.models import Participation, Person
from status.slack.send import send_status_review


def test_send_status_review_preserves_on_leave_view() -> None:
    person = Person(
        person_id="pilot",
        display_name="Pilot User",
        slack_user_id="U-PILOT",
    )
    participation = Participation(
        person_id="pilot",
        week_ending=date(2026, 8, 14),
        status="on_leave",
    )
    client = MagicMock()
    client.conversations_open.return_value = {"channel": {"id": "D123"}}
    client.chat_postMessage.return_value = {"channel": "D123", "ts": "1234.5678"}

    with (
        patch("status.slack.send._require_slack_client", return_value=MagicMock(return_value=client)),
        patch("status.slack.send.get_session") as session_context,
        patch("status.slack.send.get_person", return_value=person),
        patch("status.slack.send.get_current_drafts") as get_drafts,
        patch("status.slack.send.get_unacknowledged_flags") as get_flags,
        patch("status.slack.send.record_draft_sent") as record_sent,
        patch("status.slack.send.build_draft_blocks", return_value=[]) as build_blocks,
    ):
        session = MagicMock()
        session.get.return_value = participation
        session_context.return_value.__enter__.return_value = session
        result = send_status_review(
            "pilot",
            date(2026, 8, 14),
            bot_token="xoxb-test",
        )

    get_drafts.assert_not_called()
    get_flags.assert_not_called()
    record_sent.assert_not_called()
    assert build_blocks.call_args.kwargs["on_leave"] is True
    assert result["channel"] == "D123"
