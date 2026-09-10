"""todo - the plan for a multi-step task, kept out of the transcript and put back in front of it.

picoagent records what happened. It has nowhere to record what is still outstanding, so a model
working through five steps re-derives its plan from the conversation on every turn. That works
while the conversation is complete, and it is exactly what stops working when the conversation
is not: the ``compaction`` plugin replaces older messages with a summary, and a summary is
written to preserve decisions, files touched and current state. Steps nobody has started yet
produced no events, so they are the part a summariser has nothing to say about and drops. The
failure that follows is a task that ends two steps early with the model sounding finished,
which reads to the user exactly like a task that is done.

So the plan gets a home outside the transcript, where compaction cannot reach it:

* ``todo_write`` replaces the list - the whole list, every call.
* Each accepted write is a ``custom`` entry in the session log. Those are never sent to the
  model and never summarised, and they survive ``picoagent -r``.
* A ``context`` handler puts the current list back at the end of the history before every model
  call, so the model reads its own plan rather than reconstructing one.
* ``/todo`` shows the same list to the person.

What it does not do is make the model keep the list honest. Nothing here checks that a
``completed`` item was completed; ``todo_write`` writes down what the model claims. The value is
that the claim persists and is re-read, so an abandoned step stays visible instead of being
forgotten - not that the record is true.

Configuration (``[plugins.todo]``)::

    max_items = 30      # a longer list is refused; every item is paid for on every turn
"""
from __future__ import annotations

from picoagent.core.tools import tool_result
from picoagent.core.types import Message

#: ``custom_type`` of the session entries this plugin writes and reads.
ENTRY_TYPE = "todo"

#: The three states an item may be in. A fourth (``blocked``, ``cancelled``) was left out: the
#: model would have to be told when to use it, and a status nobody can act on is a token paid
#: for on every turn to say the same thing ``pending`` already says.
STATUSES = ("pending", "in_progress", "completed")

#: How each status is drawn in the injected block and in ``/todo``. Three characters rather than
#: the status word, because this rendering is re-sent on every model call and ``in_progress`` is
#: four tokens wherever it appears.
MARKERS = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}

#: Items allowed in one list, unless ``[plugins.todo].max_items`` says otherwise. A cap exists
#: because the cost is per turn for the rest of the session, not per call: a forty-step list is
#: not a plan the model is following, it is a plan it is paying rent on.
DEFAULT_MAX_ITEMS = 30

#: Characters allowed in one item. An item is a step, not its description.
MAX_CONTENT_CHARS = 200


def validate(raw, max_items: int = DEFAULT_MAX_ITEMS) -> list[dict]:
    """The list ``todo_write`` was handed, or a :class:`ValueError` saying what is wrong with it.

    Every message names the offending item by position and says what was expected, because the
    caller is a model that is about to retry. "item 2: status must be one of pending,
    in_progress, completed" is a correction it can apply; "invalid input" is a guess it has to
    make, and a guess costs another turn.

    An empty list is valid and means the work is finished. It is the only way to stop paying for
    a list, so refusing it would leave a stale plan in the prompt until the session ended.

    Whitespace inside an item is collapsed to single spaces. The injected block is one line per
    item, so a newline in ``content`` would render as a second entry, and an item could write a
    line the model reads as a sibling of itself. This is tidiness rather than a boundary: the
    content came from the model in the first place, and nothing here is defending the model
    against its own text.
    """
    if not isinstance(raw, list):
        raise ValueError(f"items must be a list of objects, got {type(raw).__name__}")
    if len(raw) > max_items:
        raise ValueError(f"{len(raw)} items is more than the {max_items} allowed; the whole list "
                         "is re-sent on every turn, so keep it to the steps of this task")
    items: list[dict] = []
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"item {position} must be an object with content and status, "
                             f"got {type(item).__name__}")
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"item {position}: content must be a non-empty string")
        content = " ".join(content.split())
        if len(content) > MAX_CONTENT_CHARS:
            raise ValueError(f"item {position}: content is {len(content)} characters, over the "
                             f"{MAX_CONTENT_CHARS} allowed; an item is a step, not its description")
        status = item.get("status")
        if status not in STATUSES:
            raise ValueError(f"item {position}: status must be one of {', '.join(STATUSES)}, "
                             f"got {status!r}")
        items.append({"content": content, "status": status})
    return items


def stored_items(data) -> list[dict]:
    """Well-formed items out of a session entry, ignoring anything else in it.

    The strict reading is :func:`validate`, and it belongs on the way in, where there is somebody
    to tell what to fix. On the way out there is not: the entry is already written, and a
    ``context`` handler that raises is logged and skipped by the bus, which would silently take
    the list out of the prompt for the rest of the session. A hand-edited log, or one written by
    a version of this plugin that spelled an item differently, degrades to a shorter list here
    instead of to no list at all.
    """
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [{"content": " ".join(item["content"].split()), "status": item["status"]}
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("content"), str) and item["content"].strip()
            and item.get("status") in STATUSES]


def render(items: list[dict]) -> str:
    """One line per item, in the order the model gave them."""
    return "\n".join(f"{MARKERS[item['status']]} {item['content']}" for item in items)


def context_block(items: list[dict]) -> str:
    """What the model is shown before each turn: the list in a named envelope, and nothing else.

    No instruction travels with it. Telling the model to keep the list current is standing
    advice, it never changes, and this text is re-sent on every model call for the rest of the
    session - so the advice lives in ``todo_write``'s description, which the provider is already
    sending every turn as part of the tool schema. Saying it in both places is paying twice for
    one sentence.
    """
    return f"<todos>\n{render(items)}\n</todos>"


def summarise(items: list[dict]) -> str:
    """The one line ``todo_write`` answers with: how many items, and how they stand.

    The list itself is deliberately not echoed back. It reaches the model at the top of the next
    turn anyway, through the ``context`` handler, and a tool result repeating it would put the
    same text in the transcript permanently - which is the cost this plugin exists to avoid.
    """
    if not items:
        return "todo list cleared"
    counts = {status: sum(1 for item in items if item["status"] == status) for status in STATUSES}
    return (f"{len(items)} todos: {counts['completed']} completed, "
            f"{counts['in_progress']} in progress, {counts['pending']} pending")


class Todos:
    """The current list, read back from the session log on every access rather than cached.

    Re-reading looks wasteful beside an attribute holding the last write, and it is what makes
    this plugin correct when something moves the branch. ``Session.custom`` walks the *active*
    branch, so a rewind plugin that moves ``leaf`` to an older entry moves this list back with
    it, for free and with no coordination between the two plugins. A cached copy would go on
    describing a plan the session no longer contains, and nothing would report the disagreement.

    It is also what makes a resume work without a ``session_start`` handler: a reader that always
    asks the log cannot start a session out of step with it.

    The cost is one walk of the session's entries per model call, which is a dict build beside a
    network round trip.
    """

    def __init__(self, api):
        self.api = api

    def current(self) -> list[dict]:
        """The newest list on the active branch; an empty list when nothing has been written."""
        latest = None
        for entry in self.api.entries(ENTRY_TYPE):
            latest = entry
        return stored_items(latest["data"]) if latest else []

    def replace(self, items: list[dict]) -> None:
        """Record ``items`` as the whole list from now on."""
        self.api.append_entry(ENTRY_TYPE, {"items": items})


class TodoWriteTool:
    """``todo_write`` - replace the list.

    The name says the whole contract. ``todo`` alone would not: the one thing a caller has to
    understand is that this *writes over* what was there, and a call carrying only the item that
    changed deletes the rest. The alternative was an editing API - add, complete, reorder - which
    needs stable item ids, and every id is one more thing for a model to get wrong between turns.
    A whole-list write has no state to keep in step, and a model that has drifted corrects itself
    by sending what it now believes rather than by patching what it no longer remembers.

    The verb also leaves ``todo_read`` a name it can have later. Nothing needs one today, since
    the list is in front of the model every turn, but a tool called ``todo`` would have to be
    renamed to make room and a rename costs every caller.
    """

    name = "todo_write"
    description = (
        "Record the plan for a multi-step task. Send the WHOLE list every time: it replaces "
        "what was there, so a call carrying only the item you changed deletes the others. "
        "The list is shown back to you in a <todos> block before every turn and it is the only "
        "record of the plan that survives the conversation being summarised, so update it as "
        "work moves rather than at the end. Keep one item in_progress. Send an empty list when "
        "the work is done."
    )
    parameters = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": "the complete list, in the order you mean to work through it",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "one step, in a few words"},
                        "status": {"type": "string", "enum": list(STATUSES)},
                    },
                    "required": ["content", "status"],
                },
            },
        },
        "required": ["items"],
    }

    def __init__(self, todos: Todos, max_items: int):
        self.todos, self.max_items = todos, max_items

    async def execute(self, args: dict, ctx):
        """Validate, then persist. A malformed list is an error result, never an exception.

        Nothing is written when validation fails, and the result says so. A model that reads
        "refused" and cannot tell whether half of its list landed has to read the list back
        before it can do anything, and it has no way to; saying that nothing changed is what
        makes retrying the call the whole answer.
        """
        try:
            items = validate(args.get("items"), self.max_items)
        except ValueError as exc:
            return tool_result(ctx, f"refused: {exc}. Nothing was changed - send the whole list "
                                    "again with that corrected.", is_error=True)
        self.todos.replace(items)
        return tool_result(ctx, summarise(items), items=items)


async def on_context(todos: Todos, event: dict, rt) -> dict | None:
    """Put the current list at the end of the history for this one model call.

    At the end rather than in the system prompt, for two reasons that point the same way. The
    system prompt is the prefix of every request, so rewriting it whenever the list changes
    invalidates whatever the provider had cached of the conversation; appending to the tail
    leaves that prefix alone. And position is the point - a plan the model reads immediately
    before it acts is doing the job a plan buried under a hundred thousand tokens of history is
    not.

    ``context`` hands out a deep copy and the loop uses the returned list for that request only,
    so this text never reaches the session log. That is what keeps the reminder from
    accumulating: one copy in the prompt, not one per turn in the transcript.

    An empty list returns ``None`` and injects nothing at all. "No todos" is a sentence paid for
    on every turn of every session that never uses this plugin, in exchange for telling the model
    something it can already see is true.
    """
    items = todos.current()
    if not items:
        return None
    return {"messages": event["messages"] + [Message(role="user", text=context_block(items))]}


async def on_command(todos: Todos, argstr: str, rt) -> str:
    """``/todo`` - the list as it stands, for the person.

    Read-only, and it answers when the list is empty. The person asked, so silence would be an
    answer they have to interpret; the model did not ask, which is why the injected block says
    nothing in the same situation.
    """
    items = todos.current()
    if not items:
        return "no todos"
    return f"{render(items)}\n\n{summarise(items)}"


def register(api) -> None:
    max_items = max(1, int(api.plugin_config().get("max_items", DEFAULT_MAX_ITEMS)))
    todos = Todos(api)
    api.register_tool(TodoWriteTool(todos, max_items))
    api.on("context", lambda event, rt: on_context(todos, event, rt))
    api.register_command("todo", lambda argstr, rt: on_command(todos, argstr, rt),
                         "show the agent's current todo list")
    # `max_items` is how much of the user's own context window every turn spends. A repository
    # that sets it is told the value was ignored rather than left wondering.
    api.warn_about_project_config()
