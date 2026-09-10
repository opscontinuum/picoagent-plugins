"""Tests for the history plugin: listing the session tree and moving the branch pointer.

Offline and stdlib only. Nothing here calls a model: every test builds a session log by
appending the entries a run would have appended, then drives the commands over it, because
what is under test is the tree and the pointer rather than anything the model does.
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

# ``discover -s tests`` puts this directory on the path; ``-m unittest tests.test_history_plugin``
# does not. One line here means both spellings work, which is worth more than the symmetry.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import CaptureFrontend, ScriptedProvider, make_runtime, run, temp_dir, text
from picoagent.core.loop import AgentLoop
from picoagent.core.session import Session
from picoagent.core.types import Message, ToolCall, ToolResult
from picoagent.plugins import loader

PLUGIN = Path(__file__).resolve().parents[1] / "history"


def import_plugin():
    """Import the entry module the way the loader does, so the pure functions can be called."""
    spec = importlib.util.spec_from_file_location("picoagent_plugin_history", PLUGIN / "history.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


history = import_plugin()


class SessionFixture(unittest.TestCase):
    """A session log with two prompts, one of which ran a tool. Shared by most tests below."""

    def setUp(self):
        self.tmp = temp_dir()
        self.rt = make_runtime(self.tmp, provider=ScriptedProvider([[text("ok")]]),
                               frontend=CaptureFrontend())
        history.register(self._api())
        self.session = self.rt.session
        self.ids = self._conversation()

    def _api(self):
        from picoagent.plugins.api import PluginAPI
        return PluginAPI(self.rt, "history", PLUGIN)

    def _conversation(self):
        """user -> assistant(tool call) -> tool result -> assistant -> user -> assistant."""
        appended = {}
        appended["prompt1"] = self.session.append_message(
            Message(role="user", text="add a test for the parser"))["id"]
        appended["calls"] = self.session.append_message(
            Message(role="assistant", text="I will read the file first",
                    tool_calls=[ToolCall("c1", "read", {"path": "parser.py"})]))["id"]
        appended["results"] = self.session.append_message(
            Message(role="tool", tool_results=[ToolResult("c1", "48 lines of parser")]))["id"]
        appended["answer1"] = self.session.append_message(
            Message(role="assistant", text="the parser has no test"))["id"]
        appended["prompt2"] = self.session.append_message(
            Message(role="user", text="now fix the bug"))["id"]
        appended["answer2"] = self.session.append_message(
            Message(role="assistant", text="fixed"))["id"]
        return appended

    # ------------------------------------------------------------------ helpers
    def command(self, name, args=""):
        return run(self.rt.commands.get(name).handler(args, self.rt))

    def on_disk(self):
        """Every entry the file holds, parsed. Reads the file, not the in-memory list."""
        return [json.loads(line) for line in self.session.path.read_text().splitlines() if line.strip()]

    def branch_ids(self):
        return [entry["id"] for entry in self.session.branch()]


class ListingTests(SessionFixture):
    def test_listing_shows_a_short_id_a_role_and_a_preview_for_every_entry(self):
        listing = self.command("history")
        self.assertIn(self.ids["prompt1"][:8], listing)
        self.assertIn("user", listing)
        self.assertIn("add a test for the parser", listing)

    def test_listing_marks_the_entry_the_next_turn_will_attach_to(self):
        marked = [line for line in self.command("history").splitlines() if line.startswith("*")]
        self.assertEqual(len(marked), 1)
        self.assertIn(self.ids["answer2"][:8], marked[0])

    def test_listing_names_the_tools_an_assistant_message_called(self):
        self.assertIn("[calls read]", self.command("history"))

    def test_listing_gives_each_prompt_a_turn_marker_counting_back_from_the_newest(self):
        rows = self.command("history").splitlines()
        self.assertIn("~2", [row for row in rows if self.ids["prompt1"][:8] in row][0])
        self.assertIn("~1", [row for row in rows if self.ids["prompt2"][:8] in row][0])

    def test_long_previews_are_truncated_to_one_line(self):
        added = self.session.append_message(Message(role="user", text="x" * 400 + "\nsecond line"))
        line = [row for row in self.command("history").splitlines() if added["id"][:8] in row][0]
        self.assertLess(len(line), 100)
        self.assertNotIn("second line", line)

    def test_a_limit_hides_older_entries_and_says_how_many(self):
        listing = self.command("history", "2")
        self.assertIn("older entries", listing)
        self.assertNotIn(self.ids["prompt1"][:8], listing)
        self.assertIn(self.ids["answer2"][:8], listing)

    def test_all_shows_every_entry_on_the_branch(self):
        self.assertIn(self.ids["prompt1"][:8], self.command("history", "all"))

    def test_an_unknown_argument_is_reported_rather_than_guessed_at(self):
        self.assertIn("not 'sideways'", self.command("history", "sideways"))


class RewindByNameTests(SessionFixture):
    def test_rewind_by_full_id_moves_the_pointer(self):
        self.command("rewind", self.ids["answer1"])
        self.assertEqual(self.session.branch()[-2]["id"], self.ids["answer1"])

    def test_rewind_by_short_prefix_moves_the_pointer(self):
        self.command("rewind", self.ids["answer1"][:8])
        self.assertIn(self.ids["answer1"], self.branch_ids())
        self.assertNotIn(self.ids["prompt2"], self.branch_ids())

    def test_an_ambiguous_prefix_is_refused_and_names_the_candidates(self):
        entries = [{"kind": "message", "id": "ab11", "parent": None,
                    "message": {"role": "user", "text": "one"}},
                   {"kind": "message", "id": "ab22", "parent": "ab11",
                    "message": {"role": "assistant", "text": "two"}}]
        with self.assertRaises(history.RewindError) as raised:
            history.resolve(entries, "ab")
        self.assertIn("ab11", str(raised.exception))
        self.assertIn("ab22", str(raised.exception))

    def test_an_ambiguous_prefix_leaves_the_pointer_where_it_was(self):
        """Driven through the command, so the refusal is what a user would actually see.

        Entry ids are random, so the collision is manufactured rather than hoped for: appending
        until two ids share a first hex character terminates by pigeonhole at seventeen entries.
        """
        shared = None
        while shared is None:
            firsts = [entry["id"][0] for entry in self.session.entries]
            shared = next((c for c in firsts if firsts.count(c) > 1), None)
            if shared is None:
                self.session.append_message(Message(role="user", text="filler"))
        before = self.session.leaf
        self.assertIn("matches", self.command("rewind", shared))
        self.assertEqual(self.session.leaf, before)

    def test_an_unknown_prefix_is_refused(self):
        answer = self.command("rewind", "zzzzzz")
        self.assertIn("no entry", answer)
        self.assertEqual(self.session.leaf, self.ids["answer2"])

    def test_an_unknown_flag_is_refused(self):
        self.assertIn("--exact", self.command("rewind", "--wipe abc"))

    def test_rewinding_to_where_the_pointer_already_is_writes_nothing(self):
        before = len(self.on_disk())
        answer = self.command("rewind", self.ids["answer2"])
        self.assertIn("already at", answer)
        self.assertEqual(len(self.on_disk()), before)


class RewindByTurnsTests(SessionFixture):
    def test_one_turn_back_lands_just_before_the_last_prompt(self):
        self.command("rewind", "~1")
        self.assertIn(self.ids["answer1"], self.branch_ids())
        self.assertNotIn(self.ids["prompt2"], self.branch_ids())

    def test_a_bare_rewind_means_one_turn(self):
        self.command("rewind")
        self.assertNotIn(self.ids["prompt2"], self.branch_ids())

    def test_two_turns_back_lands_before_the_first_prompt(self):
        self.command("rewind", "~2")
        self.assertNotIn(self.ids["prompt1"], self.branch_ids())
        self.assertEqual([m.role for m in self.session.messages()], [])

    def test_a_minus_sign_reads_the_same_as_a_tilde(self):
        self.command("rewind", "-2")
        self.assertNotIn(self.ids["prompt1"], self.branch_ids())

    def test_more_turns_than_the_branch_has_is_refused_with_the_count(self):
        answer = self.command("rewind", "~9")
        self.assertIn("2 prompts", answer)
        self.assertEqual(self.session.leaf, self.ids["answer2"])

    def test_a_turn_count_that_is_not_a_number_is_refused(self):
        self.assertIn("not a number of turns", self.command("rewind", "~soon"))

    def test_injected_and_queued_user_messages_do_not_count_as_prompts(self):
        self.session.append_message(Message(role="user", text="focus on tests",
                                            meta={"custom_type": "queued"}))
        self.command("rewind", "~1")
        self.assertNotIn(self.ids["prompt2"], self.branch_ids())


class AppendOnlyTests(SessionFixture):
    """The guarantee the command's own output makes: a rewind moves a pointer and deletes nothing."""

    def test_abandoned_entries_are_still_in_the_file_after_a_rewind(self):
        before = {entry["id"] for entry in self.on_disk()}
        self.command("rewind", "~2")
        after = {entry["id"] for entry in self.on_disk()}
        self.assertTrue(before.issubset(after))
        self.assertIn(self.ids["prompt2"], after)
        self.assertNotIn(self.ids["prompt2"], self.branch_ids())

    def test_the_file_only_grows(self):
        before = len(self.session.path.read_bytes())
        self.command("rewind", "~2")
        self.assertGreater(len(self.session.path.read_bytes()), before)

    def test_the_output_says_nothing_was_deleted_and_how_to_go_back(self):
        answer = self.command("rewind", "~1")
        self.assertIn("Nothing was deleted", answer)
        self.assertIn(str(self.session.path), answer)
        self.assertIn(f"/rewind {self.ids['answer2'][:8]}", answer)

    def test_the_abandoned_branch_can_be_rewound_back_onto(self):
        self.command("rewind", "~2")
        self.command("rewind", self.ids["answer2"][:8])
        self.assertIn(self.ids["prompt2"], self.branch_ids())
        self.assertEqual([m.text for m in self.session.messages()][-1], "fixed")

    def test_appending_after_a_rewind_branches_rather_than_corrupting_the_log(self):
        self.command("rewind", "~1")
        added = self.session.append_message(Message(role="user", text="different second prompt"))
        self.assertIn(added["id"], self.branch_ids())
        self.assertNotIn(self.ids["prompt2"], self.branch_ids())
        self.assertEqual([(m.role, m.text) for m in self.session.messages()],
                         [("user", "add a test for the parser"),
                          ("assistant", "I will read the file first"),
                          ("tool", ""), ("assistant", "the parser has no test"),
                          ("user", "different second prompt")])
        self.assertEqual(history.unanswered_calls(self.session.branch()), [])
        reopened = Session(self.session.path, self.tmp, resume=True)
        self.assertEqual(len(reopened.entries), len(self.on_disk()))

    def test_both_branches_survive_a_resume(self):
        self.command("rewind", "~1")
        self.session.append_message(Message(role="user", text="different second prompt"))
        reopened = Session(self.session.path, self.tmp, resume=True)
        ids = {entry["id"] for entry in reopened.entries}
        self.assertIn(self.ids["prompt2"], ids)
        self.assertIn(self.ids["answer2"], ids)

    def test_a_rewind_survives_a_restart_with_nothing_appended_after_it(self):
        """The marker entry is what makes this true: `_load` rebuilds `leaf` from the last line."""
        self.command("rewind", "~2")
        reopened = Session(self.session.path, self.tmp, resume=True)
        self.assertEqual([entry["id"] for entry in reopened.branch()], self.branch_ids())
        self.assertEqual(reopened.messages(), [])

    def test_the_marker_records_the_move_and_none_of_the_conversation(self):
        self.command("rewind", "~1")
        marker = self.on_disk()[-1]
        self.assertEqual(marker["custom_type"], "history-rewind")
        self.assertEqual(marker["data"]["from"], self.ids["answer2"])
        self.assertEqual(marker["data"]["dropped"], 2)
        self.assertNotIn("fix the bug", json.dumps(marker))

    def test_the_marker_never_reaches_the_model(self):
        self.command("rewind", "~1")
        self.assertNotIn("history-rewind", " ".join(m.text for m in self.session.messages()))


class ToolBatchTests(SessionFixture):
    """A target inside a tool batch would leave the model calls that nothing answers."""

    def test_rewinding_onto_an_assistant_message_with_tool_calls_lands_before_it(self):
        answer = self.command("rewind", self.ids["calls"][:8])
        self.assertIn(self.ids["prompt1"], self.branch_ids())
        self.assertNotIn(self.ids["calls"], self.branch_ids())
        self.assertIn("inside a tool batch", answer)
        self.assertIn(self.ids["prompt1"][:8], answer)

    def test_the_landing_it_chooses_leaves_no_tool_call_unanswered(self):
        self.command("rewind", self.ids["calls"][:8])
        self.assertEqual(history.unanswered_calls(self.session.branch()), [])

    def test_a_target_after_the_results_is_left_alone(self):
        answer = self.command("rewind", self.ids["results"][:8])
        self.assertIn(self.ids["results"], self.branch_ids())
        self.assertNotIn("inside a tool batch", answer)

    def test_exact_lands_where_asked_and_names_the_dangling_calls(self):
        answer = self.command("rewind", f"{self.ids['calls'][:8]} --exact")
        self.assertIn(self.ids["calls"], self.branch_ids())
        self.assertIn("--exact", answer)
        self.assertIn("read", answer)
        self.assertEqual([call["id"] for call in history.unanswered_calls(self.session.branch())],
                         ["c1"])

    def test_a_non_message_entry_after_the_calls_is_still_inside_the_batch(self):
        """The check is on the branch, not on the target: a plugin entry does not answer a call."""
        self.session.set_leaf(self.ids["calls"])
        marker = self.session.append_custom("todo", {"items": []})
        self.session.set_leaf(self.ids["answer2"])
        self.command("rewind", marker["id"][:8])
        self.assertEqual(history.unanswered_calls(self.session.branch()), [])
        self.assertNotIn(marker["id"], self.branch_ids())


class CompactionTests(SessionFixture):
    def test_rewinding_past_a_compaction_says_the_model_sees_the_full_history_again(self):
        self.session.append_compaction("earlier work", keep_from=self.ids["prompt2"])
        answer = self.command("rewind", self.ids["answer1"][:8])
        self.assertIn("no longer passes through a compaction", answer)
        self.assertNotIn("[Conversation summary]", " ".join(m.text for m in self.session.messages()))

    def test_rewinding_onto_a_compaction_says_which_summary_is_in_effect(self):
        compaction = self.session.append_compaction("earlier work", keep_from=self.ids["prompt2"])
        self.command("rewind", self.ids["answer1"][:8])
        answer = self.command("rewind", compaction["id"][:8])
        self.assertIn("passes through compaction", answer)
        self.assertIn(compaction["id"][:8], answer)

    def test_a_rewind_that_does_not_cross_a_compaction_says_nothing_about_one(self):
        self.assertNotIn("compaction", self.command("rewind", "~1"))


class TipsTests(SessionFixture):
    def test_tips_lists_the_abandoned_branch_as_well_as_the_active_one(self):
        self.command("rewind", "~1")
        self.session.append_message(Message(role="user", text="different second prompt"))
        listing = self.command("history", "tips")
        self.assertIn(self.ids["answer2"][:8], listing)
        self.assertEqual(len([line for line in listing.splitlines() if line.startswith("*")]), 1)

    def test_the_active_tip_is_marked(self):
        self.assertIn(f"* {self.ids['answer2'][:8]}", self.command("history", "tips"))

    def test_the_header_is_a_tip_of_its_own_because_core_never_links_it(self):
        """A quirk of core worth pinning, because ``~N`` back to the start relies on it.

        ``Session._write`` does not advance ``leaf`` for a header, so the first message of a
        session is appended with ``parent: None``: the header has no children and is a root of
        its own. That is why it shows up here, and why rewinding to it is how an empty branch
        is reached.
        """
        header = self.session.entries[0]
        self.assertEqual(header["kind"], "header")
        self.assertIsNone(self.session.entries[1]["parent"])
        self.assertIn(header["id"][:8], self.command("history", "tips"))


class PureFunctionTests(unittest.TestCase):
    """The tree walkers, driven on hand-built entries so the shapes are exact."""

    def entries(self):
        return [{"kind": "header", "id": "root", "parent": None, "cwd": "/x"},
                {"kind": "message", "id": "a", "parent": "root",
                 "message": {"role": "user", "text": "hi"}},
                {"kind": "message", "id": "b", "parent": "a",
                 "message": {"role": "assistant", "text": "",
                             "tool_calls": [{"id": "c1", "name": "read", "args": {}}]}},
                {"kind": "message", "id": "c", "parent": "b",
                 "message": {"role": "tool", "tool_results": [{"tool_call_id": "c1", "content": "x"}]}},
                {"kind": "message", "id": "d", "parent": "a",
                 "message": {"role": "user", "text": "other branch"}}]

    def test_chain_walks_parents_back_to_the_root(self):
        self.assertEqual([e["id"] for e in history.chain(self.entries(), "c")],
                         ["root", "a", "b", "c"])

    def test_chain_of_an_unknown_id_is_empty(self):
        self.assertEqual(history.chain(self.entries(), "nope"), [])

    def test_chain_stops_rather_than_looping_on_a_cycle(self):
        cyclic = [{"kind": "custom", "id": "x", "parent": "y", "custom_type": "t", "data": 1},
                  {"kind": "custom", "id": "y", "parent": "x", "custom_type": "t", "data": 1}]
        self.assertEqual(len(history.chain(cyclic, "x")), 2)

    def test_unanswered_calls_finds_a_call_with_no_result(self):
        self.assertEqual([c["id"] for c in history.unanswered_calls(history.chain(self.entries(), "b"))],
                         ["c1"])

    def test_unanswered_calls_is_empty_once_the_results_are_on_the_branch(self):
        self.assertEqual(history.unanswered_calls(history.chain(self.entries(), "c")), [])

    def test_whole_branch_at_walks_back_out_of_a_tool_batch(self):
        self.assertEqual(history.whole_branch_at(self.entries(), "b"), "a")

    def test_whole_branch_at_leaves_a_clean_target_alone(self):
        self.assertEqual(history.whole_branch_at(self.entries(), "c"), "c")

    def test_tips_finds_every_branch(self):
        self.assertEqual({e["id"] for e in history.tips(self.entries())}, {"c", "d"})

    def test_flatten_collapses_newlines_and_truncates(self):
        self.assertEqual(history.flatten("one\n  two", width=20), "one two")
        self.assertTrue(history.flatten("y" * 90).endswith("..."))
        self.assertEqual(len(history.flatten("y" * 90)), history.PREVIEW)


class LoaderTests(unittest.TestCase):
    """Loaded through the real loader, and driven the way a person types it."""

    def setUp(self):
        self.tmp = temp_dir()
        self.rt = make_runtime(self.tmp, provider=ScriptedProvider([[text("ok")]]),
                               frontend=CaptureFrontend())
        loader.load_plugin(PLUGIN, self.rt, loader.TrustStore(self.rt.cwd / "home"),
                           allow_untrusted=True)

    def notices(self):
        return [payload["text"] for event, payload in self.rt.frontend.events if event == "notice"]

    def test_both_commands_are_registered(self):
        self.assertEqual({c.name for c in self.rt.commands.all()} & {"history", "rewind"},
                         {"history", "rewind"})

    def test_the_commands_are_owned_by_this_plugin(self):
        self.assertEqual(self.rt.commands.get("rewind").owner, "history")

    def test_a_typed_rewind_moves_the_pointer_and_reports_it(self):
        run(AgentLoop(self.rt).run("first prompt"))
        run(AgentLoop(self.rt).handle_input("/rewind ~1"))
        self.assertIn("Nothing was deleted", self.notices()[-1])
        self.assertEqual(self.rt.session.messages(), [])

    def test_the_next_run_continues_from_the_rewound_point(self):
        run(AgentLoop(self.rt).run("first prompt"))
        run(AgentLoop(self.rt).handle_input("/rewind ~1"))
        run(AgentLoop(self.rt).run("second prompt"))
        self.assertEqual([m.text for m in self.rt.session.messages()], ["second prompt", "ok"])
        self.assertIn("first prompt", self.rt.session.path.read_text())


if __name__ == "__main__":
    unittest.main()
