import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from myagentsdk.observability import (
    DashboardStore,
    SimpleLanguageFormatter,
    configure_logging,
)


class ObservabilityTests(unittest.TestCase):
    def test_store_persists_calls_and_only_supported_finite_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = DashboardStore(temporary_directory)
            store.create_call("call-1", "MyAgent", "inbound")
            store.add_metrics(
                "call-1",
                "assistant",
                {
                    "e2e_latency": 0.42,
                    "llm_node_ttft": 0.12,
                    "transcription_delay": float("nan"),
                    "provider_cost": 9.0,
                },
            )

            self.assertEqual(store.calls()[0]["id"], "call-1")
            self.assertEqual(
                store.metric_samples("MyAgent"),
                {"e2e_latency": [0.42], "llm_node_ttft": [0.12]},
            )
            self.assertEqual(store.metric_samples("OtherAgent"), {})

    def test_saves_final_turns_in_per_call_jsonl_without_creating_recordings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = DashboardStore(temporary_directory)
            store.create_call("call-1", "MyAgent", "inbound")
            user_turn = {
                "id": "stt-turn-1",
                "role": "user",
                "text": "Hello there",
                "final": True,
                "created_at": 1.0,
            }
            agent_turn = {
                "id": "llm-turn-1",
                "role": "agent",
                "text": "How can I help?",
                "final": True,
                "created_at": 2.0,
            }

            store.add_transcript("call-1", user_turn)
            store.add_transcript("call-1", agent_turn)

            self.assertEqual(store.transcripts("call-1"), [user_turn, agent_turn])
            transcript_file = store.transcript_path("call-1")
            self.assertEqual(transcript_file.parent.name, "transcripts")
            self.assertEqual(len(transcript_file.read_text(encoding="utf-8").splitlines()), 2)
            self.assertTrue(store.calls()[0]["transcript_available"])
            self.assertFalse((Path(temporary_directory) / "recordings").exists())

    def test_rejects_non_final_or_invalid_transcript_turns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = DashboardStore(temporary_directory)
            for transcript in (
                {"id": "turn", "role": "user", "text": "hello", "final": False},
                {"id": "turn", "role": "system", "text": "hello", "final": True},
            ):
                with self.subTest(transcript=transcript):
                    with self.assertRaises(ValueError):
                        store.add_transcript("call-1", transcript)

    def test_configures_rotating_logs_in_the_requested_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.dict(os.environ, {"MYVOICEAGENT_LOG_DIR": temporary_directory}):
                log_path = configure_logging("agent")
                logging.getLogger("myagentsdk.test").info("dashboard log test")
                for handler in logging.getLogger().handlers:
                    if getattr(handler, "baseFilename", None) == str(log_path):
                        handler.flush()
                self.assertTrue(log_path.is_file())
                self.assertEqual(log_path.name, "agent.log")
                self.assertIn("dashboard log test", log_path.read_text(encoding="utf-8"))
                for handler in list(logging.getLogger().handlers):
                    if getattr(handler, "baseFilename", None) == str(log_path):
                        logging.getLogger().removeHandler(handler)
                        handler.close()

    def test_simplifies_livekit_unauthorized_connection_logs(self) -> None:
        formatter = SimpleLanguageFormatter("%(message)s")
        record = logging.LogRecord(
            name="livekit.agents",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="failed to connect to livekit, retrying in 0s",
            args=(),
            exc_info=None,
        )
        record.error = "401, message='Invalid response status'"

        message = formatter.format(record)

        self.assertIn("LiveKit rejected the agent login (401)", message)
        self.assertIn("same LiveKit project into .env", message)

    def test_includes_elevenlabs_rejection_reason_without_logging_credentials(self) -> None:
        formatter = SimpleLanguageFormatter("%(message)s")
        record = logging.LogRecord(
            name="livekit.plugins.elevenlabs.tts",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="elevenlabs tts returned error",
            args=(),
            exc_info=None,
        )
        record.error = (
            "Invalid API key: secret-value; Authorization: Bearer auth-secret"
        )

        message = formatter.format(record)

        self.assertIn("ElevenLabs rejected the TTS request", message)
        self.assertIn("Invalid API key: [redacted]", message)
        self.assertIn("Authorization: [redacted]", message)
        self.assertNotIn("secret-value", message)
        self.assertNotIn("auth-secret", message)

    def test_redacts_sarvam_api_key_from_log_messages(self) -> None:
        api_key = "sarvam-test-secret"
        formatter = SimpleLanguageFormatter("%(message)s")
        record = logging.LogRecord(
            name="livekit.plugins.sarvam.tts",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg=f"Sarvam TTS API error: rejected key {api_key}",
            args=(),
            exc_info=None,
        )

        with patch.dict(os.environ, {"SARVAM_API_KEY": api_key}):
            message = formatter.format(record)

        self.assertIn("[redacted]", message)
        self.assertNotIn(api_key, message)


if __name__ == "__main__":
    unittest.main()
