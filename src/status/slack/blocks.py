"""Slack Block Kit builders for draft review messages."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from status.db.models import Flag, StatusEntry
from status.skills.evidence import markdown_links_to_slack

ACTION_CONFIRM = "status_confirm"
ACTION_EDIT = "status_edit"
ACTION_REGENERATE = "status_regenerate"
ACTION_PTO = "status_pto"

# Slack allows at most 10 input blocks per modal view.
EDIT_MODAL_MAX_ENTRIES = 9  # reserve one input for missed/additional work

REGENERATE_REASON_LABELS: dict[str, str] = {
    "missed_work": "Draft missed important work",
    "wrong_grouping": "Epic grouping was wrong",
    "inaccurate": "Outcomes were inaccurate",
    "new_activity": "New Jira/GitHub activity since draft",
    "other": "Other reason",
}

STATE_LABELS: dict[str, str] = {
    "shipped": "Shipped",
    "progressing": "In progress",
    "slipped": "Slipped",
    "blocked": "Blocked",
    "quiet": "Quiet",
}

INTERNAL_FLAG_PHRASES = (
    "lacked jira link",
    "no corresponding jira",
    "no linked jira",
    "recorded only as reporter",
    "status conflicted",
    "current status in this payload",
    "not inferred",
    "supplied data",
    "supplied history",
)


def _action_value(person_id: str, week_ending: date) -> str:
    return json.dumps({"person_id": person_id, "week_ending": week_ending.isoformat()})


def _entry_title(entry: StatusEntry) -> str:
    if entry.epic_key and entry.epic_name_snapshot:
        return f"{entry.epic_key} · {entry.epic_name_snapshot}"
    if entry.epic_name_snapshot:
        return entry.epic_name_snapshot
    if entry.epic_key:
        return entry.epic_key
    return entry.project


def format_entry_text(entry: StatusEntry) -> str:
    label = STATE_LABELS.get(entry.state, entry.state.title())
    outcome = markdown_links_to_slack(entry.outcome)
    lines = [f"*{label}* · {_entry_title(entry)}", outcome]
    if entry.blocker:
        lines.append(f"_Blocker:_ {markdown_links_to_slack(entry.blocker)}")
    if entry.ask:
        lines.append(f"_Ask:_ {markdown_links_to_slack(entry.ask)}")
    if entry.needs_human:
        lines.append("_Needs your review_")
    return "\n".join(lines)


def build_flag_blocks(flags: list[Flag]) -> list[dict[str, Any]]:
    messages = list(
        dict.fromkeys(
            message
            for flag in flags
            if (message := flag.message.strip())
            and not any(phrase in message.lower() for phrase in INTERNAL_FLAG_PHRASES)
        )
    )
    if not messages:
        return []
    questions = "\n".join(f"• {message}" for message in messages[:3])
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*A few things to check*\n{questions}",
            },
        }
    ]


def build_draft_blocks(
    *,
    person_id: str,
    display_name: str,
    week_ending: date,
    entries: list[StatusEntry],
    flags: list[Flag],
    confirmed: bool = False,
    on_leave: bool = False,
) -> list[dict[str, Any]]:
    week_label = week_ending.strftime("%b %d, %Y")
    if on_leave:
        status_label = "PTO recorded"
        review_hint = "No weekly update is expected."
    else:
        status_label = "Confirmed status" if confirmed else "Draft status"
        review_hint = "Saved for this week." if confirmed else "Review each entry below."
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Week ending {week_label}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{status_label} for *{display_name}*. {review_hint}",
            },
        },
    ]

    if on_leave:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":palm_tree: *On PTO* — reminders and this week's status are skipped.",
                },
            }
        )
        return blocks

    if not entries:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "_No draft entries for this week._",
                },
            }
        )
    else:
        for entry in entries[:20]:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": format_entry_text(entry)},
                }
            )
        if len(entries) > 20:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"_Showing 20 of {len(entries)} entries._",
                        }
                    ],
                }
            )

    blocks.extend(build_flag_blocks(flags))

    if confirmed:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":white_check_mark: *Confirmed* — thanks!",
                },
            }
        )
        return blocks

    value = _action_value(person_id, week_ending)
    blocks.append(
        {
            "type": "actions",
            "block_id": "status_review_actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": ACTION_CONFIRM,
                    "text": {"type": "plain_text", "text": "Looks right"},
                    "style": "primary",
                    "value": value,
                },
                {
                    "type": "button",
                    "action_id": ACTION_EDIT,
                    "text": {"type": "plain_text", "text": "Edit"},
                    "value": value,
                },
                {
                    "type": "button",
                    "action_id": ACTION_REGENERATE,
                    "text": {"type": "plain_text", "text": "Regenerate"},
                    "value": value,
                },
                {
                    "type": "button",
                    "action_id": ACTION_PTO,
                    "text": {"type": "plain_text", "text": "On PTO"},
                    "value": value,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Mark this week as PTO?"},
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                "This draft will be left out of the weekly report, "
                                "and reminders will stop."
                            ),
                        },
                        "confirm": {"type": "plain_text", "text": "Mark as PTO"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                },
            ],
        }
    )
    return blocks


def draft_fallback_text(
    display_name: str,
    week_ending: date,
    *,
    confirmed: bool = False,
    on_leave: bool = False,
) -> str:
    prefix = "PTO" if on_leave else ("Confirmed" if confirmed else "Draft")
    return f"{prefix} status for {display_name}, week ending {week_ending.isoformat()}"


def build_edit_modal(
    *,
    person_id: str,
    week_ending: date,
    entries: list[StatusEntry],
    flags: list[Flag],
    channel: str,
    message_ts: str,
    page_offset: int = 0,
) -> dict[str, Any]:
    """Build a Slack modal for editing draft status entries.

    One input block per generated entry (blank outcome removes the entry).
    Slack allows at most 10 input blocks per modal.
    """
    page_entries = entries[page_offset : page_offset + EDIT_MODAL_MAX_ENTRIES]

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Week ending {week_ending.strftime('%b %d, %Y')}* — "
                    "edit outcomes below. Leave a field blank to remove that entry. "
                    "Use the field at the bottom to add work the draft missed."
                ),
            },
        }
    ]

    entry_ids: list[str] = []
    for entry in page_entries:
        entry_id = str(entry.entry_id)
        entry_ids.append(entry_id)
        entry_title = _entry_title(entry)
        blocks.append(
            {
                "type": "input",
                "block_id": f"entry_{entry_id}",
                "label": {
                    "type": "plain_text",
                    "text": f"{entry_title} ({entry.state})"[:75],
                },
                "element": {
                    "type": "plain_text_input",
                    "action_id": "outcome_value",
                    "multiline": True,
                    "initial_value": entry.outcome[:3000],
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Describe what happened this week...",
                    },
                },
                "optional": True,
            }
        )

    if len(entries) > page_offset + len(page_entries):
        remaining = len(entries) - page_offset - len(page_entries)
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"_{remaining} more entr{'y' if remaining == 1 else 'ies'} on the next page. "
                            "Save, then click Edit again to continue._"
                        ),
                    }
                ],
            }
        )
    elif page_offset > 0:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"_Editing entries {page_offset + 1}–"
                            f"{page_offset + len(page_entries)} of {len(entries)}._"
                        ),
                    }
                ],
            }
        )

    unticketed_element: dict[str, Any] = {
        "type": "plain_text_input",
        "action_id": "unticketed_value",
        "multiline": True,
        "placeholder": {
            "type": "plain_text",
            "text": "Meetings, side projects, epics not listed above — add Jira links if you have them",
        },
    }
    blocks.append(
        {
            "type": "input",
            "block_id": "unticketed_work",
            "label": {
                "type": "plain_text",
                "text": "Missed or additional work this week",
            },
            "element": unticketed_element,
            "optional": True,
        }
    )

    return {
        "type": "modal",
        "callback_id": "edit_status_modal",
        "private_metadata": json.dumps(
            {
                "person_id": person_id,
                "week_ending": week_ending.isoformat(),
                "channel": channel,
                "message_ts": message_ts,
                "page_offset": page_offset,
                "entry_ids": entry_ids,
                "existing_unticketed_entry_id": None,
                "total_entries": len(entries),
            }
        ),
        "title": {"type": "plain_text", "text": "Edit Status"},
        "submit": {"type": "plain_text", "text": "Save Changes"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }


def parse_edit_submission_values(
    values: dict[str, Any],
    *,
    entry_ids: list[str],
) -> tuple[dict[str, str], str | None]:
    """Return edited outcomes and missed/additional work from modal state."""
    edited_outcomes: dict[str, str] = {}
    for entry_id in entry_ids:
        block = values.get(f"entry_{entry_id}")
        if block is None:
            # Optional inputs cleared in Slack may omit the whole block from state.values.
            edited_outcomes[entry_id] = ""
            continue
        # Cleared optional inputs may omit "value" or send null.
        edited_outcomes[entry_id] = block["outcome_value"].get("value") or ""

    unticketed_work = None
    unticketed_block = values.get("unticketed_work")
    if unticketed_block:
        unticketed_work = unticketed_block["unticketed_value"].get("value")

    return edited_outcomes, unticketed_work


def build_regenerate_modal(
    *,
    person_id: str,
    week_ending: date,
    channel: str,
    message_ts: str,
) -> dict[str, Any]:
    """Build a Slack modal for regenerating draft status entries.

    User selects a reason for regeneration, then we re-run the drafter skill.
    """
    modal = {
        "type": "modal",
        "callback_id": "regenerate_status_modal",
        "private_metadata": json.dumps({
            "person_id": person_id,
            "week_ending": week_ending.isoformat(),
            "channel": channel,
            "message_ts": message_ts,
        }),
        "title": {
            "type": "plain_text",
            "text": "Regenerate Draft",
        },
        "submit": {
            "type": "plain_text",
            "text": "Regenerate",
        },
        "close": {
            "type": "plain_text",
            "text": "Cancel",
        },
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Why do you want to regenerate this draft?",
                },
            },
            {
                "type": "input",
                "block_id": "regenerate_reason",
                "label": {
                    "type": "plain_text",
                    "text": "Reason",
                },
                "element": {
                    "type": "static_select",
                    "action_id": "reason_select",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select a reason",
                    },
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": "Draft missed important work"},
                            "value": "missed_work",
                        },
                        {
                            "text": {"type": "plain_text", "text": "Epic grouping is wrong"},
                            "value": "wrong_grouping",
                        },
                        {
                            "text": {"type": "plain_text", "text": "Outcomes are inaccurate"},
                            "value": "inaccurate",
                        },
                        {
                            "text": {"type": "plain_text", "text": "New Jira/GitHub activity since draft"},
                            "value": "new_activity",
                        },
                        {
                            "text": {"type": "plain_text", "text": "Other"},
                            "value": "other",
                        },
                    ],
                },
            },
            {
                "type": "input",
                "block_id": "regenerate_notes",
                "label": {
                    "type": "plain_text",
                    "text": "Additional notes (optional)",
                },
                "element": {
                    "type": "plain_text_input",
                    "action_id": "notes_value",
                    "multiline": True,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Any specific guidance for the regeneration...",
                    },
                },
                "optional": True,
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "⚠️ This will discard your current draft and create a new one from fresh data.",
                    }
                ],
            },
        ],
    }

    return modal
