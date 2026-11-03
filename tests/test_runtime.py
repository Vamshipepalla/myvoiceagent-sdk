import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from myagentsdk.observability import DashboardStore
from myagentsdk.runtime import (
    AgentRuntime,
    TRANSCRIPT_TOPIC,
    _tts_language_for_transcript,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeRoom:
    def __init__(self) -> None:
        self.name = "test-call"
        self.local_participant = self
        self.published: list[dict] = []

    async def publish_data(self, payload: bytes, *, reliable: bool, topic: str) -> None:
        self.published.append(
            {
                "payload": json.loads(payload.decode("utf-8")),
                "reliable": reliable,
                "topic": topic,
            }
        )


class FakeContext:
    def __init__(self) -> None:
        self.room = FakeRoom()
        self.shutdown_callback = None

    def add_shutdown_callback(self, callback) -> None:
        self.shutdown_callback = callback


class FakeSession:
    def __init__(self) -> None:
        self.handlers = {}
        self.tts = object()

    def on(self, event_name: str, handler) -> None:
        self.handlers[event_name] = handler

    async def start(self, *, agent: object, room: FakeRoom) -> None:
        self.current_agent = agent


class FakeAgent:
    def __init__(self) -> None:
        self.tts = None

    def update_options(self, *, tts: object) -> None:
        self.tts = tts


class RuntimeTranscriptTests(unittest.IsolatedAsyncioTestCase):
    def test_sarvam_language_selection_uses_telugu_script_and_english_default(self) -> None:
        self.assertEqual(_tts_language_for_transcript("నమస్కారం", "en-IN"), "te-IN")
        self.assertEqual(_tts_language_for_transcript("Hello there", "en-IN"), "en-IN")

    async def test_persists_and_publishes_final_stt_and_agent_turns(self) -> None:
        runtime = AgentRuntime(
            ROOT / "myagentrunner" / "customer" / "MyAgent" / "outbound"
        )
        context = FakeContext()
        session = FakeSession()
        agent = FakeAgent()

        with tempfile.TemporaryDirectory() as temporary_directory:
            store = DashboardStore(temporary_directory)
            with (
                patch("myagentsdk.runtime.DashboardStore", return_value=store),
                patch("myagentsdk.runtime.configure_logging"),
                patch.object(runtime.session_builder, "create", return_value=session),
                patch.object(runtime, "create_agent", return_value=agent),
                patch.object(
                    runtime.session_builder,
                    "create_tts",
                    return_value=object(),
                ) as create_tts,
                patch.object(store, "add_metrics", wraps=store.add_metrics) as add_metrics,
            ):
                await runtime.run(context)
                create_tts.assert_not_called()

                with self.assertNoLogs("myagentsdk.runtime", level="ERROR"):
                    session.handlers["conversation_item_added"](
                        SimpleNamespace(
                            item=SimpleNamespace(type="agent_handoff", id="handoff-1")
                        )
                    )

                session.handlers["user_input_transcribed"](
                    SimpleNamespace(
                        is_final=True,
                        transcript="Hi there",
                        item_id="stt-1",
                        created_at=1.0,
                    )
                )
                self.assertIs(session.current_agent.tts, session.tts)
                session.handlers["user_input_transcribed"](
                    SimpleNamespace(
                        is_final=True,
                        transcript="నమస్కారం",
                        item_id="stt-te",
                        created_at=1.5,
                    )
                )
                self.assertIs(session.current_agent.tts, session.tts)
                session.handlers["user_input_transcribed"](
                    SimpleNamespace(
                        is_final=True,
                        transcript="Good morning",
                        item_id="stt-en",
                        created_at=1.75,
                    )
                )
                self.assertIs(session.current_agent.tts, session.tts)
                session.handlers["conversation_item_added"](
                    SimpleNamespace(
                        item=SimpleNamespace(
                            role="assistant",
                            text_content="Hello!",
                            id="llm-1",
                            created_at=2.0,
                            metrics={},
                        )
                    )
                )
                add_metrics.assert_called_once_with("test-call", "assistant", {})
                session.handlers["user_input_transcribed"](
                    SimpleNamespace(
                        is_final=False,
                        transcript="ignored partial",
                        item_id="stt-partial",
                        created_at=1.5,
                    )
                )

                await context.shutdown_callback("test complete")

            transcripts = store.transcripts("test-call")
            self.assertEqual(
                [turn["role"] for turn in transcripts],
                ["user", "user", "user", "agent"],
            )
            self.assertEqual(
                [turn["text"] for turn in transcripts],
                ["Hi there", "నమస్కారం", "Good morning", "Hello!"],
            )
            self.assertEqual(
                [event["payload"] for event in context.room.published],
                transcripts,
            )
            self.assertTrue(all(event["reliable"] for event in context.room.published))
            self.assertTrue(
                all(event["topic"] == TRANSCRIPT_TOPIC for event in context.room.published)
            )
            self.assertIsNotNone(store.call("test-call")["ended_at"])


if __name__ == "__main__":
    unittest.main()
