"""What the model is offered; the handlers live beside it."""

from __future__ import annotations

_RECALL = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": (
            "Search what was actually said in your past conversations with "
            "this person — whenever they ask what they told you, what you "
            "agreed, or when something came up."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The words to look for — the subject, "
                                   "not the whole question.",
                },
                "when": {
                    "type": "string",
                    "description": "Their own words about when, if they said "
                                   "any: «вчора», «минулого тижня». Leave out "
                                   "if they did not.",
                },
            },
            "required": ["query"],
        },
    },
}

_STOP = {
    "type": "function",
    "function": {
        "name": "stop_work",
        "description": (
            "Stop all the work that is running now. Whenever the person asks "
            "to stop, cancel or call off what the agents are doing, in any "
            "words. Never say it is stopped without calling this."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_DELEGATE = {
    "type": "function",
    "function": {
        "name": "delegate",
        "description": (
            "Hand work to an agent. Everything outside this conversation "
            "goes through here: files, code, the shell, the web, other "
            "programs. You cannot see or touch any of it yourself, so never "
            "say you cannot before you have asked. Returns at once: say in "
            "one short sentence that you are on it, then stop. The answer "
            "arrives later as its own message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "What the person asked for, as they said it.",
                },
            },
            "required": ["task"],
        },
    },
}

_REMIND = {
    "type": "function",
    "function": {
        "name": "remind",
        "description": (
            "Set a one-off reminder. Use the current date and time already in "
            "your prompt to turn the person's words into seconds from now."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "What to remind about, as the person said it.",
                },
                "in_seconds": {
                    "type": "integer",
                    "description": "Seconds from now, 1..2592000.",
                },
            },
            "required": ["text", "in_seconds"],
        },
    },
}

_LIST_REMINDERS = {
    "type": "function",
    "function": {
        "name": "list_reminders",
        "description": (
            "List the reminders the person has set, with their ids and how "
            "soon they are due. Use it when they ask what is planned or what "
            "you are holding."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_CANCEL_REMINDER = {
    "type": "function",
    "function": {
        "name": "cancel_reminder",
        "description": "Cancel a reminder by its id from list_reminders.",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "The id shown by list_reminders.",
                },
            },
            "required": ["id"],
        },
    },
}
