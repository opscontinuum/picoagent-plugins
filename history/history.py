"""history - see the session tree and move the branch pointer, without deleting anything.

The session log is already a tree. Every entry names its ``parent``, the active branch is the
chain from ``Session.leaf`` back to the root, and ``Session.set_leaf`` moves that pointer;
``docs/architecture.md`` says in as many words that undo, rewind and fork are plugin work on
top of that file. The capability has been there since the first commit. What has never been
there is a way for a person to *use* it: nothing prints an entry id, so there is no id to hand
``set_leaf``, and the only answer to a session that went wrong three turns ago is ``/new``,
which keeps the file and abandons the conversation in it.

This plugin is that missing surface and nothing else. It adds no state of its own and needs no
change to core:

* ``/history`` prints the active branch, one line per entry, with a short id you can copy.
* ``/rewind`` points ``leaf`` at one of those entries, so the next turn continues from there.

Nothing is deleted
------------------
This is the part to be plain about, because "rewind" means "throw away" to most people and
here it truthfully does not. The log is append-only, and that is load-bearing: it is what
makes the file a record of what happened rather than a record of what somebody last wanted to
have happened. A rewind moves one pointer. Every entry that was on the branch before is still
in the file, byte for byte, and ``/rewind`` on the old tip walks straight back onto it. So the
command says so in its output, and so does the README, rather than letting a user infer a
deletion that did not occur.

The consequence to know is the other side of the same coin: a rewind is not a redaction. If
what you want gone is a secret a tool result caught, this is the wrong tool and there isn't a
right one here - the entry stays on disk, and the answer is the file, not the pointer.

How the move survives a restart
-------------------------------
``leaf`` is in memory. ``Session._load`` rebuilds it on resume from the *last line of the
file*, not from the tree, so a rewind followed immediately by a quit would be silently undone
by the next ``-r``.

So the rewind appends one ``custom`` entry - kind ``custom``, custom_type ``history-rewind`` -
whose parent is the entry you rewound to. That entry is now the last line, so a resume rebuilds
``leaf`` onto the rewound branch, and it is a truthful record of a thing that did happen: at
this moment the pointer moved from here to there. Custom entries never reach the model
(``Session.messages`` selects ``kind == "message"``), so this costs the conversation nothing.
It carries the two ids and a count, and none of the text of any entry, so the marker cannot
become a second copy of something the log already holds once.

Landing mid tool batch
----------------------
The loop appends an assistant message and the results of its tool calls as *two* entries. So an
entry id in the middle of that pair is a perfectly good rewind target that leaves the model
context ending on tool calls nothing answers. That is a real failure, not an aesthetic one: a
strict OpenAI-dialect server rejects the request with a 400 on every subsequent turn, and
``picoagent.core.provider._stand_in_results`` exists to patch exactly that shape when it is
caused by a crash - filling each unanswered call with ``INTERRUPTED_TOOL_RESULT``, which says
the outcome is unknown.

That repair is right for a crash and wrong here. After a crash the outcome genuinely is unknown.
After a rewind it is not: the tools ran, we have their results on disk, and we are choosing to
leave them behind. Telling the model "whether it ran at all is unknown" would be a false
statement made by the one component that knows better. It is also only the built-in dialect's
answer - a provider plugin maps messages itself and may have no such repair, so a target that
depends on it is a target that works on one provider.

So ``/rewind`` does not land on a point that leaves a tool call unanswered. It walks the target
back to the nearest ancestor whose branch is whole - in practice, to just before the model made
those calls - and says which entry it actually used and why. Rewinding to the middle of a batch
almost always means "have that turn again, differently", and that is where this lands you.
``--exact`` overrides it, lands where you asked, and names the calls it left dangling, because
a rule with no way past it is a rule that will be wrong for somebody.

Compaction
----------
``messages()`` applies the newest ``compaction`` entry *on the active branch*, so rewinding past
one puts the full history back in the model's context and rewinding onto a different branch can
pick up a different summary. Neither is a corruption - the original entries were never removed,
which is what compaction was designed around - but it does change the size and the shape of the
next request, so a rewind that crosses a compaction boundary says so.
"""
from __future__ import annotations

import json
import time
from typing import Any

#: Characters of an entry id shown in listings. Ids are ``uuid4().hex[:12]``; eight is short
#: enough to read in a column and long enough that a collision inside one session needs about
#: 2**32 entries. Prefixes shorter than this still resolve - the listing is a convenience, not
#: the syntax - and an ambiguous one is reported rather than picked between.
SHORT_ID = 8

#: Width of the preview column. A session line has to fit an ordinary terminal beside an id, a
#: role and a turn marker; anything longer is a transcript, and ``/history`` is an index.
PREVIEW = 58

#: Entries printed by a bare ``/history``. Older ones are counted, not hidden: the line that
#: replaces them says how many there are and how to see them.
DEFAULT_LIMIT = 40

#: custom_type of the marker a rewind appends. Namespaced with the plugin name so that a reader
#: of somebody's log can tell which plugin wrote it.
REWIND_TYPE = "history-rewind"

#: Message meta that marks user-role text nobody typed - a plugin's injection or a queued steer.
#: ``~N`` counts prompts, and these are not prompts. Set by ``AgentLoop._start_prompt``.
NOT_A_PROMPT = ("injected", "queued")


class RewindError(Exception):
    """Something the user can fix by typing a different argument. Shown, never raised at them."""


# --------------------------------------------------------------------------- reading the tree
def chain(entries: list[dict], entry_id: str | None) -> list[dict]:
    """The branch ending at ``entry_id``, root first. ``Session.branch()`` for an arbitrary id.

    Session only ever walks from its own ``leaf``, and every question here is about some other
    point: what the branch would look like if the pointer moved there, what an abandoned tip
    still leads back to. Same walk, one argument.
    """
    by_id = {entry["id"]: entry for entry in entries}
    walked, current = [], entry_id
    seen: set[str] = set()
    while current and current in by_id and current not in seen:
        seen.add(current)
        walked.append(by_id[current])
        current = by_id[current]["parent"]
    return list(reversed(walked))


def unanswered_calls(branch: list[dict]) -> list[dict]:
    """Tool calls on ``branch`` that no message on it answers.

    Answers are collected from every message rather than only the one that should follow the
    call, for the reason ``_stand_in_results`` gives: a result recorded further down is still an
    answer, and counting it twice would trade one malformed request for another.
    """
    answered = {result["tool_call_id"]
                for entry in branch if entry["kind"] == "message"
                for result in entry["message"].get("tool_results", [])}
    return [call for entry in branch if entry["kind"] == "message"
            for call in entry["message"].get("tool_calls", [])
            if call.get("id") not in answered]


def whole_branch_at(entries: list[dict], entry_id: str) -> str:
    """``entry_id``, or the nearest ancestor whose branch leaves no tool call unanswered.

    Returns the id unchanged when it is already whole, which is every case but landing inside a
    tool batch. The walk terminates at the header: a branch of one header entry has no messages,
    so it has no unanswered calls.
    """
    by_id = {entry["id"]: entry for entry in entries}
    current: str | None = entry_id
    while current and unanswered_calls(chain(entries, current)):
        current = by_id[current]["parent"]
    return current or entry_id


def prompts(branch: list[dict]) -> list[dict]:
    """The entries that start a turn: user-role messages the person actually typed.

    Text a plugin injected and text that was queued are user-role messages too, and they are
    excluded because ``~2`` is meant to count the user's prompts. The limit worth knowing: a
    ``steer`` the loop appends between tool batches is written without that meta, so it counts as
    a prompt here. It is a message the user asked for, one turn late, and treating it as a turn
    boundary is wrong by a line rather than wrong by a turn.
    """
    return [entry for entry in branch
            if entry["kind"] == "message"
            and entry["message"].get("role") == "user"
            and entry["message"].get("meta", {}).get("custom_type") not in NOT_A_PROMPT]


def tips(entries: list[dict]) -> list[dict]:
    """Entries no other entry claims as a parent - one per branch, including abandoned ones.

    This is how you get back. A rewind leaves the old branch complete on disk but unreachable by
    name, and a name is the whole difference between "still there" and "still usable".
    """
    parents = {entry["parent"] for entry in entries}
    return [entry for entry in entries if entry["id"] not in parents]


def newest_compaction(branch: list[dict]) -> dict | None:
    """The compaction ``messages()`` would apply to ``branch``, or ``None``."""
    return next((entry for entry in reversed(branch) if entry["kind"] == "compaction"), None)


# --------------------------------------------------------------------------- naming an entry
def resolve(entries: list[dict], token: str) -> str:
    """Turn a full id or an id prefix into exactly one entry id.

    Ambiguity is reported, never resolved by picking. The two entries a two-character prefix
    matches are usually turns apart, so guessing would move the pointer somewhere the user did
    not ask for and the only evidence would be a line they had no reason to re-read.

    The search covers the whole file rather than the active branch, which is what makes an
    abandoned branch reachable: after a rewind, its tip is off-branch by definition, and it is
    the id you need to undo the rewind.
    """
    matches = [entry for entry in entries if entry["id"].startswith(token)]
    if not matches:
        raise RewindError(f"no entry in this session starts with {token!r} - /history lists them")
    if len(matches) > 1:
        listed = ", ".join(f"{short(entry['id'])} ({label(entry)})" for entry in matches[:6])
        more = f", and {len(matches) - 6} more" if len(matches) > 6 else ""
        raise RewindError(f"{token!r} matches {len(matches)} entries: {listed}{more}. "
                          "Type more of the id.")
    return matches[0]["id"]


def target_from(session: Any, argument: str) -> str:
    """The entry a ``/rewind`` argument names, before the whole-branch check moves it.

    Two spellings, kept apart by shape rather than by inference. ``~2`` and ``-2`` count
    prompts; anything else is an id or the start of one. An id is hex, so a bare ``3`` is a
    legitimate prefix as well as a plausible count, and a command that guessed between them
    would be wrong silently. The prefix is the one that keeps its bare spelling because it is
    the one ``/history`` prints.
    """
    argument = argument.strip()
    if not argument:
        argument = "~1"
    if argument[0] in "~-":
        return _prompt_before(session, argument)
    return resolve(session.entries, argument.lower())


def _prompt_before(session: Any, argument: str) -> str:
    """``~N`` -> the entry that was the leaf just before the Nth-from-last prompt was sent.

    The parent, not the prompt itself: "go back one turn" means the session is as it was before
    you typed, so that the next thing you type replaces it rather than following it.
    """
    count = argument[1:].strip()
    if not count.isdigit() or int(count) < 1:
        raise RewindError(f"{argument!r} is not a number of turns - try ~1, or an id from /history")
    branch = session.branch()
    starts = prompts(branch)
    wanted = int(count)
    if wanted > len(starts):
        have = f"{len(starts)} prompt" + ("" if len(starts) == 1 else "s")
        raise RewindError(f"this branch has {have}, so ~{wanted} is further back than it goes")
    entry = starts[len(starts) - wanted]
    return entry["parent"] or session_root(session)


def session_root(session: Any) -> str:
    """Where "before the first prompt" lands: the header entry.

    Core writes the header through ``Session._write``, which deliberately does not advance
    ``leaf`` for a header, so the *first* message of a session is appended with ``parent: None``
    and the header ends up a root of its own with no children. Landing there is still the right
    answer for ``~N`` that reaches the start: the branch becomes the header alone, ``messages()``
    is empty, and the marker this rewind appends parents the new branch to the header the way
    every other entry in the file is parented.
    """
    header = next((entry["id"] for entry in session.entries if entry["kind"] == "header"), None)
    if not header:
        raise RewindError("that prompt is the start of this branch and the log has no header "
                          "entry to land on; pick an id from /history instead")
    return header


# --------------------------------------------------------------------------- rendering
def short(entry_id: str | None) -> str:
    return (entry_id or "root")[:SHORT_ID]


def label(entry: dict) -> str:
    """What to call an entry in a listing: its role if it is a message, its kind otherwise."""
    if entry["kind"] == "message":
        return entry["message"].get("role", "message")
    if entry["kind"] == "custom" and entry.get("custom_type") == REWIND_TYPE:
        return "rewind"
    return entry["kind"]


def flatten(text: str, width: int = PREVIEW) -> str:
    """One line, at most ``width`` characters. A preview, so it may cut a word in half."""
    collapsed = " ".join(str(text).split())
    return collapsed if len(collapsed) <= width else collapsed[:width - 3] + "..."


def preview(entry: dict) -> str:
    """A line's worth of what an entry holds, chosen per kind rather than dumping the JSON."""
    kind = entry["kind"]
    if kind == "message":
        return _message_preview(entry["message"])
    if kind == "header":
        return flatten(f"cwd {entry.get('cwd', '?')}")
    if kind == "compaction":
        return flatten(f"keep from {short(entry.get('keep_from'))}: {entry.get('summary', '')}")
    if kind == "shutdown":
        return flatten(f"reason {entry.get('reason', '?')}")
    if kind == "custom":
        if entry.get("custom_type") == REWIND_TYPE:
            data = entry.get("data") or {}
            return flatten(f"leaf moved {short(data.get('from'))} -> {short(data.get('to'))}")
        return flatten(f"{entry.get('custom_type', '?')}: {json.dumps(entry.get('data'))}")
    return flatten(kind)


def _message_preview(message: dict) -> str:
    """Assistant tool calls and tool results are named, because that is what a batch looks like.

    An assistant message that only called tools has no text at all, and a line reading as empty
    is the one a person scanning for the start of a bad batch needs to see.
    """
    if message.get("role") == "tool":
        results = message.get("tool_results", [])
        first = flatten(results[0].get("content", "")) if results else "(no results)"
        return f"{first} (+{len(results) - 1} more)" if len(results) > 1 else first
    body = flatten(message.get("text", ""))
    calls = message.get("tool_calls", [])
    if not calls:
        return body
    named = f"[calls {', '.join(call.get('name', '?') for call in calls)}]"
    return f"{body} {named}" if body else named


def render(branch: list[dict], leaf: str | None, limit: int | None) -> str:
    """The listing: one line per entry, newest last, ``*`` on the entry the pointer is at.

    Newest last so that it reads in the direction the conversation ran and ends where the
    prompt will, which is also where the interesting entries are on a long branch.
    """
    if not branch:
        return "this session has no entries"
    starts = prompts(branch)
    marks = {entry["id"]: f"~{len(starts) - index}" for index, entry in enumerate(starts)}
    shown = branch if limit is None or len(branch) <= limit else branch[-limit:]
    lines = [f"{'*' if entry['id'] == leaf else ' '} {short(entry['id'])}  "
             f"{label(entry):<9}  {marks.get(entry['id'], ''):<4}  {preview(entry)}"
             for entry in shown]
    if len(shown) < len(branch):
        hidden = len(branch) - len(shown)
        lines.insert(0, f"  ... {hidden} older entries (/history all)")
    lines.append(f"({len(branch)} entries on this branch; * = where the next turn attaches)")
    lines.append("/rewind <id> moves the pointer there, /rewind ~N undoes that many prompts.")
    return "\n".join(lines)


def render_tips(entries: list[dict], leaf: str | None) -> str:
    """Every branch tip in the file, so an abandoned branch has a name you can rewind to."""
    found = tips(entries)
    lines = []
    for entry in found:
        length = len(chain(entries, entry["id"]))
        lines.append(f"{'*' if entry['id'] == leaf else ' '} {short(entry['id'])}  "
                     f"{label(entry):<9}  {length} entr{'y' if length == 1 else 'ies':<3}  "
                     f"{preview(entry)}")
    plural = "tip" if len(found) == 1 else "tips"
    return "\n".join(lines + [f"({len(found)} branch {plural}, * = active; "
                              "/rewind <id> switches to one)"])


# --------------------------------------------------------------------------- the move
def rewind(session: Any, argument: str, exact: bool = False) -> str:
    """Move ``leaf``, append the marker that makes the move survive a restart, and report it.

    The report is most of the point. A user who types ``/rewind ~3`` has just done something
    that looks destructive and is not, to a session whose earlier shape they can no longer see,
    so the answer names the entry it landed on, how many entries left the branch, the fact that
    those entries are still in the file, and the id that puts them back.
    """
    before = session.branch()
    asked = target_from(session, argument)
    landed = asked if exact else whole_branch_at(session.entries, asked)
    if landed == session.leaf:
        return (f"already at {short(landed)} ({label(_entry(session, landed))}); "
                "nothing moved and nothing was written")

    previous_leaf = session.leaf
    after = chain(session.entries, landed)
    dropped = [entry for entry in before if entry["id"] not in {e["id"] for e in after}]

    session.set_leaf(landed)
    # Appending here is what makes the move outlive the process: `_load` rebuilds `leaf` from the
    # last line of the file, so without a line on the new branch a resume would walk back onto
    # the branch this call just left. It also leaves the move itself in the record.
    session.append_custom(REWIND_TYPE, {"from": previous_leaf, "to": landed,
                                        "asked": argument.strip(), "dropped": len(dropped),
                                        "at": time.time()})

    lines = [f"rewound to {short(landed)} ({label(_entry(session, landed))}): "
             f"{preview(_entry(session, landed))}"]
    if landed != asked:
        lines.append(_batch_note(asked, landed))
    elif exact and unanswered_calls(after):
        names = ", ".join(call.get("name", "?") for call in unanswered_calls(after))
        lines.append(f"--exact: this branch ends with tool calls nothing answers ({names}). The "
                     "next request carries them, and a strict server may refuse it.")
    lines.append(f"{len(dropped)} entries are no longer on the active branch. Nothing was "
                 "deleted - the log is append-only, they are all still in "
                 f"{session.path}, and /rewind {short(previous_leaf)} puts you back on them.")
    note = _compaction_note(before, after)
    if note:
        lines.append(note)
    return "\n".join(lines)


def _batch_note(asked: str, landed: str) -> str:
    return (f"{short(asked)} is inside a tool batch: stopping there would leave the model tool "
            f"calls nothing answers, so this landed on {short(landed)}, the entry before the "
            "model made them. Pass --exact to stop where you asked instead.")


def _compaction_note(before: list[dict], after: list[dict]) -> str | None:
    """Say when the summary ``messages()`` applies has changed, because the next request will.

    Not a warning about damage. Compaction never removed the entries it summarised, so a branch
    on either side of one is equally intact; what changes is how much of it the model is sent.
    """
    was, now = newest_compaction(before), newest_compaction(after)
    if (was or {}).get("id") == (now or {}).get("id"):
        return None
    if now is None:
        return ("This branch no longer passes through a compaction, so the model sees the full "
                "history again and the next request will be larger.")
    if was is None:
        return f"This branch passes through compaction {short(now['id'])}; earlier turns reach "\
               "the model as its summary."
    return (f"The compaction in effect changed from {short(was['id'])} to {short(now['id'])}, so "
            "the model sees a different summary of the earlier turns.")


def _entry(session: Any, entry_id: str) -> dict:
    return next(entry for entry in session.entries if entry["id"] == entry_id)


# --------------------------------------------------------------------------- commands
async def history_command(args: str, rt: Any) -> str:
    """``/history`` - the active branch. ``all`` for the whole of it, ``tips`` for every branch."""
    argument = args.strip().lower()
    if argument == "tips":
        return render_tips(rt.session.entries, rt.session.leaf)
    if argument in ("", "all") or argument.isdigit():
        limit = None if argument == "all" else (max(1, int(argument)) if argument else DEFAULT_LIMIT)
        return render(rt.session.branch(), rt.session.leaf, limit)
    return f"/history takes nothing, a number of entries, 'all' or 'tips' - not {args.strip()!r}"


async def rewind_command(args: str, rt: Any) -> str:
    """``/rewind`` - move the branch pointer. ``~N`` counts prompts, anything else is an id.

    A ``RewindError`` is returned rather than raised. The dispatcher would catch it and print
    ``/rewind failed: ...``, which reads like a fault in the command; every one of these is a
    sentence about the argument, and the user is the one who can fix it.
    """
    tokens = args.split()
    flags = [token for token in tokens if token.startswith("--")]
    rest = [token for token in tokens if not token.startswith("--")]
    unknown = [flag for flag in flags if flag != "--exact"]
    if unknown:
        return f"/rewind does not take {unknown[0]} - the only flag is --exact"
    if len(rest) > 1:
        return f"/rewind takes one entry id or ~N, not {len(rest)} arguments"
    try:
        return rewind(rt.session, rest[0] if rest else "", exact="--exact" in flags)
    except RewindError as exc:
        return str(exc)


def register(api: Any) -> None:
    api.register_command("history", history_command,
                         "list the session tree (all | tips | <n>)")
    api.register_command("rewind", rewind_command,
                         "move the branch pointer to an earlier entry (~N or an id)")
