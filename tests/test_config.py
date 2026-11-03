import json
import tempfile
import unittest
from pathlib import Path

from myagentsdk.config import ConfigError, load_config


class LoadConfigTests(unittest.TestCase):
    def write_config(self, directory: str, data: object) -> Path:
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_loads_model_and_session_values(self) -> None:
        data = {
            "name": "MyAgent",
            "models": {"stt": "vendor/stt", "llm": "vendor/llm", "tts": "vendor/tts"},
            "session": {
                "user_away_timeout": 12,
                "turn_detection": "vad",
                "interruption_mode": "vad",
                "endpointing": {
                    "mode": "dynamic",
                    "min_delay": 0.4,
                    "max_delay": 2.5,
                    "alpha": 0.8,
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(self.write_config(directory, data))

        self.assertEqual(config.name, "MyAgent")
        self.assertEqual(config.models.llm.model, "vendor/llm")
        self.assertEqual(config.models.llm.provider, "inference")
        self.assertEqual(config.session.user_away_timeout, 12.0)
        self.assertEqual(config.session.turn_detection, "vad")
        self.assertEqual(config.session.interruption_mode, "vad")
        self.assertEqual(config.session.endpointing_mode, "dynamic")
        self.assertEqual(config.session.min_endpointing_delay, 0.4)
        self.assertEqual(config.session.max_endpointing_delay, 2.5)
        self.assertEqual(config.session.endpointing_alpha, 0.8)

    def test_allows_disabling_user_away_timeout(self) -> None:
        data = {
            "name": "MyAgent",
            "models": {"stt": "vendor/stt", "llm": "vendor/llm", "tts": "vendor/tts"},
            "session": {"user_away_timeout": None},
        }
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(self.write_config(directory, data))

        self.assertIsNone(config.session.user_away_timeout)

    def test_rejects_missing_model(self) -> None:
        data = {
            "name": "MyAgent",
            "models": {"stt": "vendor/stt", "llm": "vendor/llm"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, data)
            with self.assertRaisesRegex(ConfigError, "models.tts"):
                load_config(path)

    def test_rejects_unknown_keys(self) -> None:
        data = {
            "name": "MyAgent",
            "models": {"stt": "vendor/stt", "llm": "vendor/llm", "tts": "vendor/tts"},
            "unexpected": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, data)
            with self.assertRaisesRegex(ConfigError, "Unknown config field"):
                load_config(path)

    def test_adaptive_turn_detection_uses_livekit_auto_selection(self) -> None:
        data = {
            "name": "MyAgent",
            "models": {"stt": "vendor/stt", "llm": "vendor/llm", "tts": "vendor/tts"},
            "session": {"turn_detection": "adaptive"},
        }
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(self.write_config(directory, data))

        self.assertEqual(config.session.turn_detection, "auto")

    def test_loads_provider_specific_model_settings(self) -> None:
        data = {
            "name": "MyAgent",
            "models": {
                "stt": {
                    "provider": "deepgram",
                    "model": "nova-3",
                    "language": "en-IN",
                },
                "llm": {"provider": "google", "model": "gemini-3.1-flash-lite"},
                "tts": {
                    "provider": "sarvam",
                    "model": "bulbul:v3",
                    "language": "en-IN",
                    "languages": ["en-IN", "te-IN"],
                    "speaker": "pooja",
                    "api_key_env": "SARVAM_API_KEY",
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(self.write_config(directory, data))

        self.assertEqual(config.models.stt.provider, "deepgram")
        self.assertEqual(config.models.stt.language, "en-IN")
        self.assertEqual(config.models.llm.provider, "google")
        self.assertEqual(config.models.llm.model, "gemini-3.1-flash-lite")
        self.assertEqual(config.models.tts.provider, "sarvam")
        self.assertEqual(config.models.tts.model, "bulbul:v3")
        self.assertEqual(config.models.tts.language, "en-IN")
        self.assertEqual(config.models.tts.languages, ("en-IN", "te-IN"))
        self.assertEqual(config.models.tts.speaker, "pooja")
        self.assertEqual(config.models.tts.api_key_env, "SARVAM_API_KEY")

    def test_rejects_sarvam_model_without_api_key_environment_variable(self) -> None:
        data = {
            "name": "MyAgent",
            "models": {
                "stt": "vendor/stt",
                "llm": "vendor/llm",
                "tts": {
                    "provider": "sarvam",
                    "model": "bulbul:v3",
                    "language": "en-IN",
                    "speaker": "pooja",
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, data)
            with self.assertRaisesRegex(ConfigError, "api_key_env.*required"):
                load_config(path)

    def test_rejects_endpointing_minimum_above_maximum(self) -> None:
        data = {
            "name": "MyAgent",
            "models": {"stt": "vendor/stt", "llm": "vendor/llm", "tts": "vendor/tts"},
            "session": {"endpointing": {"min_delay": 2, "max_delay": 1}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, data)
            with self.assertRaisesRegex(ConfigError, "at least min_delay"):
                load_config(path)

    def test_reports_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "Invalid JSON"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
