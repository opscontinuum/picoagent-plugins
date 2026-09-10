"""Behavioural tests for the todo plugin, driven through a real AgentLoop.

The claim the plugin makes is about *persistence and re-reading*, and neither half can be checked
by calling a function: the list has to be written by a model call, survive a second session
opened over the same log, and turn up in the request body of the turn after that. So a scripted
model makes the calls, the session file is reopened the way ``picoagent -r`` reopens one, and the
assertions are on what the provider was handed and on what the log contains.

``ScriptedProvider`` records every request, which is what makes the injection testable at all:
``provider.calls[n]["messages"]`` is the history that turn was sent, and the ``<todos>`` block
either is or is not the last message in it.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

from helpers import CaptureFrontend, ScriptedProvider, call, make_runtime, run, temp_dir, text
from picoagent.core.config import load_config
from picoagent.core.loop import AgentLoop, Runtime
from picoagent.core.session import Session
from picoagent.core.tools import BUILTIN_TOOLS
from picoagent.plugins import loader

PLUGIN = Path(__file__).resolve().parents[1] / "todo"

_spec = importlib.util.spec_from_file_location("picoagent_plugin_todo", PLUGIN / "todo.py")
todo = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = todo
_spec.loader.exec_module(todo)

PLAN = [{"content": "read the middleware", "status": "completed"},
        {"content": "move validation behind an interface", "status": "in_progress"},
        {"content": "port the two callers", "status": "pending"}]

REVISED = [{"content": "read the middleware", "status": "completed"},
           {"content": "move validation behind an interface", "status": "completed"},
           {"content": "port the two callers", "status": "in_progress"}]


class ValidationTests(unittest.TestCase):
    """``validate`` is the whole of what the tool will accept; pin each refusal separately.

    Each message has to name the item and say what was expected, because the caller is a model
    that is about to retry. The assertions are on those two facts rather than on the sentence, so
    the wording can be improved without rewriting the suite.
    """

    def test_a_well_formed_list_comes_back_normalised(self):
        self.assertEqual(todo.validate(PLAN), PLAN)

    def test_an_empty_list_is_valid_and_means_finished(self):
        self.assertEqual(todo.validate([]), [])

    def test_something_that_is_not_a_list_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            todo.validate("read the middleware")
        self.assertIn("must be a list", str(caught.exception))

    def test_an_item_that_is_not_an_object_is_refused_by_position(self):
        with self.assertRaises(ValueError) as caught:
            todo.validate(["read the middleware"])
        self.assertIn("item 1", str(caught.exception))

    def test_an_unknown_status_is_refused_and_the_allowed_ones_are_named(self):
        with self.assertRaises(ValueError) as caught:
            todo.validate([{"content": "port the callers", "status": "done"}])
        self.assertIn("item 1", str(caught.exception))
        for status in todo.STATUSES:
            self.assertIn(status, str(caught.exception))

    def test_missing_or_blank_content_is_refused(self):
        for item in ({"status": "pending"}, {"content": "   ", "status": "pending"}):
            with self.assertRaises(ValueError) as caught:
                todo.validate([item])
            self.assertIn("content", str(caught.exception))

    def test_a_list_past_the_cap_is_refused_and_the_cap_is_named(self):
        long = [{"content": f"step {n}", "status": "pending"} for n in range(4)]
        with self.assertRaises(ValueError) as caught:
            todo.validate(long, max_items=3)
        self.assertIn("3", str(caught.exception))

    def test_content_longer_than_the_limit_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            todo.validate([{"content": "x" * (todo.MAX_CONTENT_CHARS + 1), "status": "pending"}])
        self.assertIn(str(todo.MAX_CONTENT_CHARS), str(caught.exception))

    def test_whitespace_in_content_is_collapsed_so_one_item_stays_one_line(self):
        """A newline would render as a second entry in the injected block."""
        items = todo.validate([{"content": "port\nthe  callers", "status": "pending"}])
        self.assertEqual(items[0]["content"], "port the callers")
        self.assertEqual(len(todo.render(items).splitlines()), 1)


class StoredItemsTests(unittest.TestCase):
    """The tolerant reader. A log entry is already written, so there is nobody left to correct."""

    def test_well_formed_items_are_returned(self):
        self.assertEqual(todo.stored_items({"items": PLAN}), PLAN)

    def test_junk_in_an_entry_shortens_the_list_rather_than_emptying_the_prompt(self):
        data = {"items": [PLAN[0], {"content": "port the callers", "status": "abandoned"}, 7]}
        self.assertEqual(todo.stored_items(data), [PLAN[0]])

    def test_an_entry_of_the_wrong_shape_reads_as_no_list(self):
        self.assertEqual(todo.stored_items({"items": "everything"}), [])
        self.assertEqual(todo.stored_items(None), [])


class _Api:
    """The two methods :class:`todo.Todos` uses, over a runtime a test already has.

    ``PluginAPI`` needs the loader's manifest to build, and these tests want to ask the store
    what it currently holds without going through a slash command. Both methods are one-liners
    delegating to the session, which is the only state involved.
    """

    def __init__(self, rt):
        self.rt = rt

    def entries(self, custom_type):
        return self.rt.session.custom(custom_type)

    def append_entry(self, custom_type, data):
        self.rt.session.append_custom(custom_type, data)


class TodoPluginTests(unittest.TestCase):
    """The plugin in a session: what gets written, what gets sent, and what the person sees."""

    def setUp(self):
        self.tmp = temp_dir()

    # ------------------------------------------------------------------ fixtures
    def _rt(self, turns, frontend=None):
        self.provider = ScriptedProvider(turns)
        rt = make_runtime(self.tmp, provider=self.provider, frontend=frontend or CaptureFrontend())
        loader.load_plugin(PLUGIN, rt, loader.TrustStore(self.tmp / "home"), allow_untrusted=True)
        return rt

    def _resumed_rt(self, turns, frontend=None):
        """A second runtime over the same session file, as ``picoagent -r`` opens one.

        Built by hand rather than through ``make_runtime``: that helper opens its session with
        ``resume=False``, which appends a second header to the file it finds, and a header has no
        parent - ``Session._load`` then makes it the leaf and the active branch is one entry long.
        The point of the test is the branch, so the session has to be opened the way the CLI
        opens it.
        """
        os.environ["PICOAGENT_HOME"] = str(self.tmp / "home")
        cfg = load_config(self.tmp, {"model": "test", "provider": "scripted"})
        self.provider = ScriptedProvider(turns)
        rt = Runtime(cfg, self.tmp, Session(self.tmp / "session.jsonl", self.tmp, resume=True))
        for builtin in BUILTIN_TOOLS:
            rt.tools.register(builtin())
        rt.providers.register(self.provider)
        rt.frontend = frontend or CaptureFrontend()
        loader.load_plugin(PLUGIN, rt, loader.TrustStore(self.tmp / "home"), allow_untrusted=True)
        return rt

    @staticmethod
    def _entries(rt) -> list[dict]:
        return [entry["data"] for entry in rt.session.custom(todo.ENTRY_TYPE)]

    @staticmethod
    def _sent(rt_provider, turn: int) -> str:
        """Everything the model was sent on ``turn``, flattened for a substring check."""
        request = rt_provider.calls[turn]
        return request["system"] + "\n" + "\n".join(m.text or "" for m in request["messages"])

    @staticmethod
    def _notices(rt) -> list[str]:
        return [payload["text"] for event, payload in rt.frontend.events if event == "notice"]

    def _write_plan(self, items=None, extra_turns=()):
        """One turn in which the model writes ``items``, then whatever ``extra_turns`` says."""
        turns = [[call("todo_write", items=PLAN if items is None else items)], *extra_turns,
                 [text("ok")]]
        rt = self._rt(turns)
        run(AgentLoop(rt).run("plan the refactor"))
        return rt

    # ------------------------------------------------------------------ writing
    def test_a_written_list_is_recorded_as_a_custom_session_entry(self):
        rt = self._write_plan()
        self.assertEqual(self._entries(rt), [{"items": PLAN}])

    def test_the_tool_answers_with_a_count_rather_than_the_list_it_was_just_given(self):
        rt = self._write_plan()
        answer = rt.frontend.tool_results()[0]
        self.assertFalse(answer.is_error)
        self.assertIn("3 todos", answer.content)
        self.assertNotIn("port the two callers", answer.content)

    def test_a_second_write_replaces_the_first(self):
        rt = self._rt([[call("todo_write", items=PLAN)],
                       [call("todo_write", items=REVISED)],
                       [text("ok")]])
        run(AgentLoop(rt).run("work through it"))
        self.assertEqual(self._entries(rt), [{"items": PLAN}, {"items": REVISED}])
        self.assertEqual(todo.Todos(_Api(rt)).current(), REVISED)

    def test_an_empty_list_clears_the_plan(self):
        rt = self._rt([[call("todo_write", items=PLAN)],
                       [call("todo_write", items=[])],
                       [text("ok")]])
        run(AgentLoop(rt).run("finish it"))
        self.assertEqual(todo.Todos(_Api(rt)).current(), [])

    def test_the_list_is_never_part_of_the_model_facing_history(self):
        """``custom`` entries are the whole reason this survives compaction."""
        rt = self._write_plan()
        conversation = "\n".join(m.text or "" for m in rt.session.messages())
        self.assertNotIn("port the two callers", conversation)

    # ------------------------------------------------------------------ malformed input
    def test_a_malformed_list_is_an_error_result_and_writes_nothing(self):
        rt = self._write_plan(items=[{"content": "port the callers", "status": "done"}])
        answer = rt.frontend.tool_results()[0]
        self.assertTrue(answer.is_error)
        self.assertIn("item 1", answer.content)
        self.assertIn("Nothing was changed", answer.content)
        self.assertEqual(self._entries(rt), [])

    def test_a_malformed_write_leaves_the_previous_list_standing(self):
        rt = self._rt([[call("todo_write", items=PLAN)],
                       [call("todo_write", items="port the callers")],
                       [text("ok")]])
        run(AgentLoop(rt).run("work through it"))
        self.assertTrue(rt.frontend.tool_results()[1].is_error)
        self.assertEqual(todo.Todos(_Api(rt)).current(), PLAN)

    def test_the_session_survives_a_malformed_write(self):
        """The tool must not raise: a refusal is a result the model can read and retry from."""
        rt = self._write_plan(items=[7])
        self.assertTrue(rt.frontend.tool_results()[0].is_error)
        self.assertEqual(rt.frontend.text, "ok")

    # ------------------------------------------------------------------ context injection
    def test_the_list_is_in_the_request_on_the_turn_after_it_was_written(self):
        rt = self._write_plan()
        sent = self._sent(self.provider, 1)
        self.assertIn("<todos>", sent)
        self.assertIn("[~] move validation behind an interface", sent)

    def test_the_block_is_the_last_thing_the_model_reads(self):
        self._write_plan()
        self.assertIn("<todos>", self.provider.calls[1]["messages"][-1].text)

    def test_nothing_is_injected_before_a_list_exists(self):
        rt = self._rt([[text("nothing to plan")]])
        run(AgentLoop(rt).run("say hello"))
        self.assertNotIn("<todos>", self._sent(self.provider, 0))

    def test_nothing_is_injected_once_the_list_is_cleared(self):
        rt = self._rt([[call("todo_write", items=PLAN)],
                       [call("todo_write", items=[])],
                       [text("ok")]])
        run(AgentLoop(rt).run("finish it"))
        self.assertIn("<todos>", self._sent(self.provider, 1))
        self.assertNotIn("<todos>", self._sent(self.provider, 2))

    def test_the_injected_block_never_reaches_the_session_log(self):
        """It is re-rendered per request, so one copy in the prompt and none in the transcript."""
        rt = self._write_plan()
        self.assertNotIn("<todos>", "\n".join(m.text or "" for m in rt.session.messages()))

    def test_the_injection_does_not_grow_with_the_turns(self):
        rt = self._rt([[call("todo_write", items=PLAN)],
                       [call("read", path="missing.txt")],
                       [text("ok")]])
        run(AgentLoop(rt).run("work through it"))
        self.assertEqual(self._sent(self.provider, 2).count("<todos>"), 1)

    # ------------------------------------------------------------------ resume
    def test_a_resumed_session_recovers_the_list_from_the_log(self):
        self._write_plan()
        resumed = self._resumed_rt([[text("carrying on")]])
        run(AgentLoop(resumed).run("where were we"))
        self.assertIn("[~] move validation behind an interface", self._sent(self.provider, 0))

    def test_a_resumed_session_shows_the_list_to_the_person_too(self):
        self._write_plan()
        resumed = self._resumed_rt([[text("ok")]])
        run(AgentLoop(resumed).handle_input("/todo"))
        self.assertIn("port the two callers", self._notices(resumed)[0])

    def test_a_resume_recovers_the_newest_list_not_the_first(self):
        rt = self._rt([[call("todo_write", items=PLAN)],
                       [call("todo_write", items=REVISED)],
                       [text("ok")]])
        run(AgentLoop(rt).run("work through it"))
        resumed = self._resumed_rt([[text("ok")]])
        run(AgentLoop(resumed).handle_input("/todo"))
        self.assertIn("[~] port the two callers", self._notices(resumed)[0])
        self.assertNotIn("[~] move validation behind an interface", self._notices(resumed)[0])

    # ------------------------------------------------------------------ the command
    def test_the_command_shows_the_list_and_how_it_stands(self):
        rt = self._write_plan()
        run(AgentLoop(rt).handle_input("/todo"))
        shown = self._notices(rt)[0]
        self.assertIn("[x] read the middleware", shown)
        self.assertIn("[~] move validation behind an interface", shown)
        self.assertIn("[ ] port the two callers", shown)
        self.assertIn("3 todos: 1 completed, 1 in progress, 1 pending", shown)

    def test_the_command_answers_when_there_is_no_list(self):
        """The person asked, so silence would be an answer they have to interpret."""
        rt = self._rt([[text("ok")]])
        run(AgentLoop(rt).handle_input("/todo"))
        self.assertEqual(self._notices(rt), ["no todos"])

    def test_the_command_writes_nothing(self):
        rt = self._write_plan()
        run(AgentLoop(rt).handle_input("/todo"))
        self.assertEqual(self._entries(rt), [{"items": PLAN}])


if __name__ == "__main__":
    unittest.main()
