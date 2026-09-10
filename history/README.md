# history

See the session tree and move the branch pointer. `/history` lists it, `/rewind` moves it.
Standard library only, no third-party dependencies, and no change to picoagent core.

## Why this exists

A picoagent session log is an append-only JSONL **tree**. Every entry carries an `id` and a
`parent`, `Session.leaf` points at the newest entry on the active branch, and `Session.branch()`
walks parents back to the root. `Session.set_leaf(entry_id)` moves that pointer, and its
docstring says what it is for: rewind and fork.

All of that has been in core from the start. What was missing was the surface. Nothing printed
an entry id, so there was no id to hand `set_leaf`, and a session that went wrong three turns
ago had exactly one answer: `/new`, which keeps the file and abandons the conversation inside
it. This plugin adds the two commands that make the existing capability reachable, and nothing
else. It stores no state of its own.

## Commands

| Command | What it does |
|---|---|
| `/history` | the active branch, one line per entry, with a short id you can copy |
| `/history all` | the same without the recent-entries limit |
| `/history 10` | the last 10 entries |
| `/history tips` | every branch tip in the file, including the ones a rewind left behind |
| `/rewind` | go back one prompt (same as `/rewind ~1`) |
| `/rewind ~3` | go back three prompts, to just before you typed the third-from-last one |
| `/rewind -3` | the same; `-` and `~` read alike |
| `/rewind 1c63b212` | move the pointer to that entry; a shorter prefix works too |
| `/rewind 1c63b212 --exact` | land exactly there, even mid tool batch (see below) |

```
  9e0931a1  user       ~2    add a test for the parser, it has none and the bug in i...
  0ef8c6c7  assistant        I'll read the file first [calls read, grep]
  a2c839e5  tool             48 lines of parser source with another line (+1 more)
  1c63b212  assistant        the parser has no test at all
  5a9be48b  user       ~1    now fix the bug
* 2ecebfc4  assistant        fixed
(6 entries on this branch; * = where the next turn attaches)
/rewind <id> moves the pointer there, /rewind ~N undoes that many prompts.
```

The `~N` column is on the prompts, and it is what you pass to `/rewind` to undo that prompt
**and everything after it**. Ids are `uuid4().hex[:12]`; the listing shows the first eight
characters, and any prefix that names one entry works. A prefix that names two is reported with
both candidates and nothing moves - it is never resolved by picking, because the two entries a
short prefix matches are usually turns apart and the only evidence of a wrong guess would be a
line you had no reason to re-read.

## Nothing is deleted

This is the part to be plain about, because "rewind" means "throw away" to most people and here
it truthfully does not.

A rewind moves one pointer. The log is append-only, which is what makes the file a record of
what happened rather than a record of what somebody last wanted to have happened, so every entry
that was on the branch before the rewind is still in the file, byte for byte. The command says
so, and tells you the id that walks straight back onto them:

```
rewound to 1c63b212 (assistant): the parser has no test at all
2 entries are no longer on the active branch. Nothing was deleted - the log is append-only,
they are all still in /home/you/.picoagent/sessions/.../2026-01-31.jsonl, and
/rewind 2ecebfc4 puts you back on them.
```

`/history tips` lists every branch in the file, so an abandoned one keeps a name you can reach
it by. Rewinding is reversible in both directions, indefinitely.

The other side of the same coin, said once so it is not inferred: **a rewind is not a
redaction.** If what you want gone is a secret a tool result caught, this is the wrong tool and
there is no right one here. The entry stays on disk, readable by anything that reads the file,
and the answer is the file itself.

## Rewinding is forking; there is no `/fork`

There deliberately is no fork command, because on an append-only tree there is nothing for it
to do that `/rewind` does not already do.

`set_leaf` plus an append *is* a fork: the moment you type your next prompt after a rewind, the
new entry hangs off the entry you rewound to, and the entries that used to follow it are a
sibling branch with their own tip. Both branches are complete, neither is preferred, and
`/rewind <tip>` switches between them. A separate `/fork` would have to either do the identical
pointer move under a second name, or invent a distinction the file format does not have.

So the vocabulary here is one operation and one reading command:

* **rewind** - move the pointer. `/rewind`
* **fork** - what the tree looks like afterwards, once you append. Not a command; `/history tips`
  is how you see it.

The one thing a fork command could have added is naming a branch, and a name is state this
plugin would then have to store and keep in step with a file it does not own. Ids are already
names. That trade was not worth making.

## Landing inside a tool batch

The loop appends an assistant message and the results of its tool calls as **two** entries. So
an id in the middle of that pair is a perfectly good rewind target that would leave the model
context ending on tool calls that nothing answers.

That is a real failure, not an aesthetic one. A strict OpenAI-dialect server rejects a request
in that shape with a 400, on every subsequent turn rather than only the one that produced it.
Core knows about the shape - `picoagent.core.provider._stand_in_results` patches it when a crash
causes it, filling each unanswered call with `INTERRUPTED_TOOL_RESULT`, which says the outcome is
unknown.

That repair is right for a crash and wrong for a rewind. After a crash the outcome genuinely is
unknown. After a rewind it is not: the tools ran, their results are on disk, and you are choosing
to leave them behind. Telling the model "whether it ran at all is unknown" would be a false
statement made by the one component that knows better. It is also only the built-in dialect's
answer; a provider plugin maps messages itself and may have no such repair, so a target that
relies on it works on one provider and not the next.

So `/rewind` does not land on a point that leaves a tool call unanswered. It walks the target
back to the nearest ancestor whose branch is whole - in practice, to just before the model made
the calls - and says which entry it used and why:

```
rewound to 9e0931a1 (user): add a test for the parser, it has none and the bug in i...
0ef8c6c7 is inside a tool batch: stopping there would leave the model tool calls nothing
answers, so this landed on 9e0931a1, the entry before the model made them. Pass --exact to
stop where you asked instead.
```

Note where that lands: on your prompt, not before it. Your question is still in the history and
the model's attempt at it is not, which is what "have that turn again" means. Use `~1` when you
want the prompt gone as well.

`--exact` overrides the walk, lands where you asked, and names the calls it left dangling. A
rule with no way past it is a rule that will be wrong for somebody.

## Compaction

`Session.messages()` applies the newest `compaction` entry **on the active branch**, so moving
the pointer changes which summary is in effect - or whether one is:

* rewinding to before a compaction puts the full history back in the model's context, so the next
  request is larger, and can be large enough to trip the overflow the compaction was made to avoid;
* switching to a branch that carries a different compaction means the model sees a different
  summary of the same earlier turns.

Neither is a corruption. Compaction never removed the entries it summarised - that is the whole
design - so a branch on either side of one is equally intact. But the size and shape of the next
request do change, so a rewind that crosses a compaction boundary says which way:

```
This branch no longer passes through a compaction, so the model sees the full history again
and the next request will be larger.
```

What this plugin does **not** do is re-summarise, or move a compaction's `keep_from` onto the new
branch. A compaction entry belongs to the branch it was written on; forcing it onto another one
would mean claiming a summary describes turns it never saw. If you rewind past a compaction and
the context is now too large, run `/compact` again on the branch you are actually on.

## How the move survives a restart

`leaf` lives in memory, and `Session._load` rebuilds it on resume from the **last line of the
file**, not from the tree. A rewind followed immediately by a quit would therefore be undone by
the next `-r`.

So a rewind appends one entry: kind `custom`, custom_type `history-rewind`, parent set to the
entry you rewound to. It is now the last line, so a resume rebuilds `leaf` onto the rewound
branch. Custom entries never reach the model (`messages()` selects `kind == "message"`), so this
costs the conversation nothing, and it is a truthful record of something that did happen - at
this moment the pointer moved from here to there. It carries the two ids, the argument you typed
and a count, and none of the text of any entry, so the marker cannot become a second copy of
something the log already holds once.

You will see those markers in `/history`, labelled `rewind`. That is deliberate: they are the
audit trail of the thing this plugin does.

## Known limits

* **`~N` counts a mid-run steer as a prompt.** Text a plugin injected or queued is excluded by
  its `meta.custom_type`, but the `steer` text `AgentLoop._turns` appends between tool batches is
  written without that marker, so it looks like something you typed. The effect is being wrong by
  a line, not by a turn.
* **A rewind is not a redaction.** Said above; repeated because it is the one thing somebody
  might reach for this plugin to do.
* **The header is not an ancestor of anything.** `Session._write` deliberately does not advance
  `leaf` for a header entry, so the first message of a session has `parent: None` and the header
  is a root with no children. `/history tips` shows it, and `~N` back past the first prompt lands
  on it, which is how you reach an empty branch with an id you can name.

## Use

```bash
picoagent -e path/to/picoagent-plugins/history
```

or add it to `.picoagent/config.toml`:

```toml
[plugins]
enabled = ["./picoagent-plugins/history"]
```

There is nothing to configure.

## Tests

```bash
python3 -m unittest discover -s tests -q
```

`tests/test_history_plugin.py` covers the listing, both ways of naming a target, the ambiguous
and unknown prefix refusals, the tool-batch landing rule and its `--exact` override, the
compaction notices, and the append-only guarantee itself - that the abandoned entries are still
in the file after a rewind, that appending afterwards branches rather than corrupting the log,
that both branches survive a resume, and that a rewind with nothing appended after it survives
one too.
