import base64
import io
import os
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from myagentsdk.runtime import AgentRuntime
from myagentsdk.app import create_server


ROOT = Path(__file__).resolve().parents[1]


class AgentSessionBuilderTests(unittest.TestCase):
    def test_google_provider_requires_google_api_key(self) -> None:
        runtime = AgentRuntime(ROOT / "myagentrunner" / "customer" / "MyAgent" / "outbound")
        credentials = {
            "DEEPGRAM_API_KEY": "test-deepgram-key",
            "GOOGLE_API_KEY": "",
            "SARVAM_API_KEY": "test-sarvam-key",
        }

        with patch.dict(os.environ, credentials):
            runtime.session_builder._vad = object()
            with self.assertRaisesRegex(RuntimeError, "GOOGLE_API_KEY"):
                runtime.session_builder.create()

    def test_sarvam_tts_uses_configured_speaker_and_language(self) -> None:
        runtime = AgentRuntime(ROOT / "myagentrunner" / "customer" / "MyAgent" / "outbound")
        with patch.dict(os.environ, {"SARVAM_API_KEY": "test-sarvam-key"}):
            tts_model = runtime.session_builder._build_model(
                runtime.config.models.tts,
                "TTS",
            )
            english_tts = runtime.session_builder.create_tts("en-IN")

        agent = runtime.create_agent()
        agent.update_options(tts=english_tts)
        self.assertEqual(tts_model.provider, "Sarvam")
        self.assertEqual(tts_model.model, "bulbul:v4-flash")
        self.assertEqual(tts_model._opts.speaker, "ishita_en_customer")
        self.assertEqual(tts_model._opts.target_language_code, "en-IN")
        self.assertEqual(english_tts._opts.speaker, "ishita_en_customer")
        self.assertEqual(english_tts._opts.target_language_code, "en-IN")
        self.assertIs(agent.tts, english_tts)

    def test_plugins_register_before_worker_setup_and_vad_loads_during_setup(self) -> None:
        runtime = AgentRuntime(ROOT / "myagentrunner" / "customer" / "MyAgent" / "outbound")

        with patch("livekit.plugins.silero.VAD.load", return_value=object()) as load_vad:
            server = create_server(runtime)
            server.setup_fnc(None)

        from livekit.plugins import google

        self.assertTrue(callable(google.LLM))
        load_vad.assert_called_once()


class FakeSarvamResponse:
    status = 200

    def __init__(self, audio: bytes) -> None:
        self.audio = audio

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self):
        return {
            "request_id": "mock-request",
            "audios": [base64.b64encode(self.audio).decode("ascii")],
        }


class FakeSarvamHTTPSession:
    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self.request = None

    def post(self, **request):
        self.request = request
        return FakeSarvamResponse(self.audio)


class SarvamSynthesisTests(unittest.IsolatedAsyncioTestCase):
    async def test_sarvam_synthesis_emits_mock_audio_without_network(self) -> None:
        from livekit.plugins import sarvam

        audio_file = io.BytesIO()
        with wave.open(audio_file, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(bytes(22050))
        http_session = FakeSarvamHTTPSession(audio_file.getvalue())

        tts_model = sarvam.TTS(
            target_language_code="en-IN",
            model="bulbul:v4-flash",
            speaker="ishita_en_customer",
            api_key="test-sarvam-key",
            output_audio_codec="wav",
            http_session=http_session,
        )
        audio_frame = await tts_model.synthesize("Hello, this is a test.").collect()

        self.assertEqual(http_session.request["json"]["text"], "Hello, this is a test.")
        self.assertEqual(http_session.request["json"]["model"], "bulbul:v4-flash")
        self.assertEqual(http_session.request["json"]["speaker"], "ishita_en_customer")
        self.assertEqual(http_session.request["json"]["target_language_code"], "en-IN")
        self.assertEqual(http_session.request["headers"]["api-subscription-key"], "test-sarvam-key")
        self.assertEqual(audio_frame.sample_rate, 22050)
        self.assertEqual(audio_frame.num_channels, 1)
        self.assertGreater(len(audio_frame.data), 0)


if __name__ == "__main__":
    unittest.main()
