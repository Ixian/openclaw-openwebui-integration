"""Activity must remain presentation-only, ordered, bounded and session scoped."""
import asyncio
import json
import unittest
from unittest import mock

import conftest  # noqa: F401
from _test_stubs import install_stubs

install_stubs()
import openclaw_pipe as p
import openclaw_status_action as a

SESSION = "agent:marvin:openwebui-00000000-0000-0000-0000-000000000001-00000000-0000-0000-0000-000000000002"


def event(phase="start", *, seq=1, run="run1", stream="tool", **data):
    return {"sessionKey": SESSION, "runId": run, "seq": seq, "stream": stream,
            "_bridgeActivity": True, "data": {"phase": phase, "name": "exec", "toolCallId": "call1", **data}}


def card(revision=1):
    return {"sessionKey": SESSION, "revision": revision, "updatedAt": 1793539200000,
            "markdown": "Checking logs", "steps": [{"step": "Read logs", "status": "in_progress"}]}


class ActivityStateTests(unittest.TestCase):
    def test_partial_output_is_not_a_tool_result(self):
        r = p._TurnRenderer()
        r.feed(event(args={"title": "Inspect logs"}))
        events = r.feed(event("update", seq=2, partialResult={"content": [{"type": "text", "text": "first line"}]}))
        self.assertTrue(any("first line" in str(e) for e in events))
        self.assertFalse(any(i["type"] == "function_call_output" for i in r.output))
        self.assertEqual(r._tool_items["call1"]["status"], "in_progress")

    def test_result_wins_over_late_partial_and_duplicate(self):
        state = p._ActivityState()
        state.feed(event())
        state.feed(event("result", seq=3, result={"content": [{"type": "text", "text": "final"}]}))
        self.assertIsNone(state.feed(event("update", seq=2, partialResult={"text": "stale"})))
        self.assertEqual(state.calls["call1"]["output"], "final")
        self.assertEqual(state.calls["call1"]["status"], "completed")

    def test_snapshots_replace_rather_than_append(self):
        state = p._ActivityState()
        state.feed(event(), now=0)
        state.feed(event("update", seq=2, partialResult={"text": "one"}), now=1)
        state.feed(event("update", seq=3, partialResult={"text": "one\ntwo"}), now=2)
        self.assertEqual(state.calls["call1"]["output"], "one\ntwo")

    def test_throttled_event_still_updates_state_and_final_flushes(self):
        state = p._ActivityState()
        state.feed(event(), now=0)
        state.feed(event("update", seq=2, partialResult={"text": "one"}), now=.1)
        self.assertIsNone(state.feed(event("update", seq=3, partialResult={"text": "two"}), now=.2))
        self.assertEqual(state.calls["call1"]["output"], "two")
        self.assertIsNotNone(state.feed(event("result", seq=4, result={"text": "final"}), now=.3))

    def test_output_and_labels_cannot_inject_html_or_history(self):
        state = p._ActivityState()
        state.feed(event(args={"title": "<script>alert(1)</script>"}))
        state.feed(event("update", seq=2, partialResult={"text": "</details><script>bad()</script>"}), now=2)
        text = state.markdown()
        self.assertEqual(text.count("</details>"), 1)
        self.assertNotIn("<script>", text)
        seed = p._history_seed([{"role": "assistant", "content": text + "\nActual answer"}])
        self.assertIn("Actual answer", seed)
        self.assertNotIn("bad()", seed)

    def test_wrong_session_and_older_card_rejected(self):
        state = p._ActivityState()
        state.feed(event(stream=p.ACTIVITY_STREAM, card=card(2)))
        state.feed(event(seq=2, stream=p.ACTIVITY_STREAM, card=card(1)))
        self.assertEqual(state.card["revision"], 2)
        bad = {**card(), "sessionKey": "agent:other:private"}
        with self.assertRaises(ValueError):
            p._activity_card(bad, SESSION)
        self.assertIsNone(state.feed({**event(), "sessionKey": "another"}))

    def test_clear_does_not_complete_unfinished_work(self):
        state = p._ActivityState()
        state.feed(event(stream=p.ACTIVITY_STREAM, card=card()))
        state.feed(event(seq=2, stream="lifecycle", phase="end"))
        self.assertIn("unfinished", state.markdown())
        state.feed(event(seq=3, stream=p.ACTIVITY_STREAM, card=None))
        self.assertIsNone(state.card)

    def test_cancel_updates_note_and_native_result_without_duplicate(self):
        r = p._TurnRenderer()
        r.feed(event())
        r.close_open_calls("Stopped")
        self.assertEqual(r.activity.calls["call1"]["status"], "interrupted")
        self.assertEqual(sum(i["type"] == "function_call_output" for i in r.output), 1)
        rebuilt = p._TurnRenderer()
        p._keep_pipe_notes(r.output, rebuilt)
        p._keep_pipe_notes(r.output, rebuilt)
        self.assertEqual(len(rebuilt.output), 1)
        self.assertIn("Interrupted", str(rebuilt.output))

    def test_bounds_and_no_shell_arguments_in_title(self):
        state = p._ActivityState()
        state.feed(event(args={"command": "TOKEN=secret python -c 'private'"}))
        state.feed(event("update", seq=2, partialResult={"text": "x" * 9000}), now=2)
        self.assertEqual(len(state.calls["call1"]["output"]), 8000)
        self.assertNotIn("secret", state.markdown())
        self.assertIn("Preview limited", state.markdown())
        self.assertEqual(len(p._activity_result_preview("x" * 9000)), 8000)
        self.assertIn("clipped", p._activity_result_preview("x" * 9000))

    def test_no_activity_is_created_for_plain_text_or_empty_end(self):
        state = p._ActivityState()
        self.assertIsNone(state.feed(event(stream="assistant", delta="hello")))
        self.assertIsNone(state.feed(event(stream="lifecycle", phase="end", seq=2)))


class ActivityReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_result_requires_matching_tool_identity(self):
        conn = mock.Mock()
        conn.send_request = mock.AsyncMock(return_value={"ok": True, "message": {
            "toolCallId": "call1", "content": [{"type": "text", "text": "retained"}]}})
        result = await p._activity_load_output(conn, SESSION, "message1", "call1")
        self.assertEqual(result, {"available": True, "text": "retained", "truncated": False})
        result = await p._activity_load_output(conn, SESSION, "message1", "other")
        self.assertFalse(result["available"])

    async def test_snapshot_recovers_inflight_and_result_source(self):
        class Conn:
            async def send_request(self, method, params, timeout):
                if method == "progressCard.get":
                    return {"card": card()}
                return {"messages": [{"role": "toolResult", "toolCallId": "old", "toolName": "read",
                                      "content": [{"type": "text", "text": "old output"}],
                                      "__openclaw": {"id": "result1", "runId": "run1"}}],
                        "inFlightRun": {"runId": "run1", "events": [event(args={"title": "Live"}),
                            event("update", seq=2, partialResult={"text": "output so far"})]}}
        result = await p._activity_snapshot(Conn(), SESSION)
        self.assertEqual(result["card"]["revision"], 1)
        self.assertTrue(any(c["output"] == "output so far" for c in result["calls"]))

    async def test_closed_or_foreign_dialog_never_reads_gateway(self):
        action = a.Action()
        emitter = mock.AsyncMock()
        with mock.patch.object(a, "_get_action_connection", new=mock.AsyncMock()) as get:
            result = await action._run_activity({"dialogId": "absent", "chat_id": "chat"}, {"id": "user"}, emitter)
        self.assertEqual(result["status"], "error")
        get.assert_not_awaited()

    async def test_output_lookup_rejects_forged_reference(self):
        action = a.Action()
        did, dialog = a._dialog_open("user", SESSION)
        dialog["chat_id"] = "chat"
        conn = mock.Mock(marvin_release=a.MARVIN_RELEASE)
        conn.send_request = mock.AsyncMock()
        try:
            with mock.patch.object(a, "_owui_chat_title", new=mock.AsyncMock(return_value="Chat")), \
                 mock.patch.object(a, "_get_action_connection", new=mock.AsyncMock(return_value=(conn, "pipe"))):
                result = await action._run_activity({"mode": "activity-output", "dialogId": did,
                    "chat_id": "chat", "callId": "forged", "messageId": "private"}, {"id": "user"}, mock.AsyncMock())
            self.assertEqual(result["status"], "error")
            conn.send_request.assert_not_awaited()
        finally:
            a._open_dialogs.pop(did, None)

    def test_browser_payload_escapes_script_terminators(self):
        code = a._render_activity_view({"dialogId": "d", "error": "</script><script>evil</script>"})
        self.assertIn("\\u003c", code)
        self.assertNotIn("</script>", code)
