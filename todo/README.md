# todo

The plan for a multi-step task, kept in the session log and put back in front of the model every
turn. Standard library only, no third-party dependencies.

```
> break the auth refactor into steps and start on it

[x] read the current session middleware
[~] move token validation behind an interface
[ ] port the two callers
[ ] delete the old helper

4 todos: 1 completed, 1 in progress, 2 pending
```

## The gap this closes

picoagent records what happened. Nothing in it records what is still outstanding, so a model
working through five steps re-derives its plan from the conversation on every turn.

That holds while the conversation is complete. It stops holding when the conversation is not.
The `compaction` plugin replaces older messages with a summary, and a summary is written to
preserve the goal, the decisions, the files touched and the current state. Steps nobody has
started produced no events, so they are precisely the part a summariser has nothing to say about
and leaves out. What follows is a task that ends two steps early with the model sounding
finished, which reads to the user exactly like a task that is done.

A `custom` entry in the session log is neither sent to the model nor summarised, and it survives
`picoagent -r`. That is where the plan goes.

## How it works

| Piece | What it does |
|---|---|
| `todo_write` tool | Replaces the list. Whole list, every call. |
| `custom` session entry | Every accepted write, appended to the log. Never sent to the model. |
| `context` handler | Puts the current list at the end of the history before each model call. |
| `/todo` | Shows the same list to the person. Read-only. |

## Design notes

**Whole-list writes, not edits.** `todo_write` is named for the thing a caller has to understand:
it writes over what was there, and a call carrying only the item that changed deletes the rest.
The alternative was an editing API - add, complete, reorder - and every one of those verbs needs
stable item ids, which is one more thing for a model to get wrong between turns. A whole-list
write has no state to keep in step, and a model that has drifted corrects itself by sending what
it now believes rather than by patching what it no longer remembers.

**Read from the log, never cached.** `Todos.current()` walks the session on every access instead
of holding the last write in an attribute. That is what makes the plugin correct when something
moves the branch: `Session.custom` reads the *active* branch, so a rewind plugin that moves
`leaf` to an older entry moves this list back with it, for free and with no coordination between
the two plugins. It is also why there is no `session_start` handler - a reader that always asks
the log cannot start a session out of step with it. The cost is one walk of the session's entries
per model call, beside a network round trip.

**The tail of the history, not the system prompt.** Two reasons pointing the same way. The system
prompt is the prefix of every request, so rewriting it whenever the list changes invalidates
whatever the provider had cached of the conversation; appending to the tail leaves that prefix
alone. And position is the point: a plan the model reads immediately before it acts is doing the
job that a plan buried under a hundred thousand tokens of history is not.

The `context` event hands out a deep copy and the loop uses the returned list for that request
only, so the injected block never reaches the session log. One copy in the prompt, rather than
one per turn accumulating in the transcript.

**An empty list injects nothing.** Not "no todos" - nothing. That sentence would be paid for on
every turn of every session that never uses this plugin, in exchange for telling the model
something it can already see. `/todo` does answer "no todos", because a person asked and silence
is an answer they would have to interpret.

**The block carries no instruction.** Just the list inside a `<todos>` envelope. Telling the model
to keep it current is standing advice that never changes, and it lives in `todo_write`'s
description, which the provider already sends every turn as part of the tool schema. Saying it in
both places is paying twice for one sentence.

**Malformed input is an error result, and nothing is written.** Each message names the item by
position and says what was expected, because the caller is a model about to retry: "item 2:
status must be one of pending, in_progress, completed" is a correction it can apply, and "invalid
input" is a guess that costs another turn. The result says nothing changed, which is what makes
re-sending the whole list the entire fix.

## What this does not do

* **It does not make the list true.** Nothing checks that a `completed` item was completed;
  `todo_write` writes down what the model claims. What the plugin guarantees is that the claim
  persists and is re-read, so an abandoned step stays visible instead of being forgotten. That is
  worth having and it is not the same as an accurate plan.
* **It does not enforce one `in_progress` item.** The tool description asks for it; nothing
  refuses a list with three. Rejecting a whole write over bookkeeping would throw away the update
  that was the point of the call, and a list with two items in flight is still legible - less
  crisp, not wrong. Instruction here, not mechanism, and said plainly because the difference
  matters.
* **It does not survive the plugin being switched off.** The entries stay in the log, but nothing
  reads them, so the model goes back to re-deriving its plan. That is the safe direction: no
  stale list in the prompt.
* **A delegated child agent writes into its parent's list.** The `agents` plugin hands a child
  every tool the parent has active, and `todo_write` closes over the parent's runtime, so a child
  that calls it replaces the parent's plan with its own. That is the stateful-handler hazard the
  `agents` plugin's own docstring describes, arriving through a tool rather than an event: the
  mark it forwards (`delegated: True`) is on the `tool_call` payload, which a tool's `execute`
  never sees. The damage is bookkeeping and it lands in the transcript, but if you run both
  plugins, do not delegate a step and expect the parent's list to be untouched when the child
  returns.
* **The `<todos>` envelope is not a boundary.** Item content comes from the model itself, so
  there is no untrusted party to defend against here, and the whitespace collapsing in `validate`
  is tidiness - it keeps one item from rendering as two lines - rather than a control.

## Configuration

```toml
[plugins.todo]
max_items = 30      # a longer list is refused
```

`max_items` bounds what one turn spends of the user's own context window, so it is read from your
config only. A repository that sets it is told the value was ignored.

## Use

```bash
picoagent -e path/to/picoagent-plugins/todo
```

or add it to `.picoagent/config.toml`:

```toml
[plugins]
enabled = ["./picoagent-plugins/todo"]
```

## Tests

```bash
python3 -m unittest discover -s tests -q
```

`tests/test_todo_plugin.py` drives the plugin through a real `AgentLoop` with a scripted model:
a list written and then replaced, malformed lists refused without changing anything, the list
recovered from the log by a second session over the same file, the `<todos>` block present in the
request when a list exists and absent when it does not, and `/todo` in both states.
