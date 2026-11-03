import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from myagentsdk import AgentBase, AgentRuntime, AgentSessionBuilder
from myagentsdk.app import create_server as create_sdk_server
from myagentrunner.runner import (
    _prepare_livekit_ssl_context,
    create_server,
    resolve_agent_dir,
)


class RunnerTests(unittest.TestCase):
    def test_resolves_customer_agent_direction(self) -> None:
        agent_dir = resolve_agent_dir("MyAgent", "outbound")

        self.assertEqual(agent_dir.name, "outbound")
        self.assertEqual(agent_dir.parent.name, "MyAgent")
        self.assertTrue((agent_dir / "config.json").is_file())
        self.assertTrue((agent_dir / "prompt" / "prompt.md").is_file())
        self.assertTrue(agent_dir.parent.joinpath("inbound").is_dir())

    def test_resolves_outbound_agent(self) -> None:
        agent_dir = resolve_agent_dir("MyAgent", "outbound")

        self.assertTrue((agent_dir / "agent.py").is_file())
        self.assertTrue((agent_dir / "prompt" / "prompt.md").is_file())

    def test_rejects_agent_path_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "single folder name"):
            resolve_agent_dir("..", "inbound")

    def test_loads_prompt_and_creates_base_agent(self) -> None:
        agent_dir = resolve_agent_dir("MyAgent", "outbound")
        runtime = AgentRuntime(agent_dir)
        agent = runtime.create_agent()

        self.assertEqual(runtime.config.name, "MyAgent")
        self.assertEqual(runtime.config.prompt, "prompt/prompt.md")
        self.assertEqual(
            runtime.instructions,
            (agent_dir / "prompt" / "prompt.md").read_text(encoding="utf-8").strip(),
        )
        self.assertIsInstance(agent, AgentBase)

    def test_runner_registers_selected_agent_with_livekit(self) -> None:
        server = create_server(resolve_agent_dir("MyAgent", "outbound"))

        self.assertEqual(type(server).__name__, "AgentServer")

    def test_worker_setup_marks_a_warm_process_ready(self) -> None:
        runtime = AgentRuntime(resolve_agent_dir("MyAgent", "outbound"))
        server = create_sdk_server(runtime)
        with tempfile.TemporaryDirectory() as temporary_directory:
            ready_file = Path(temporary_directory) / "worker-ready"
            setup_errors: list[Exception] = []

            def setup_worker() -> None:
                try:
                    server.setup_fnc(None)
                except Exception as error:
                    setup_errors.append(error)

            with (
                patch.dict(os.environ, {"MYVOICEAGENT_WORKER_READY_FILE": str(ready_file)}),
                patch.object(runtime.session_builder, "load_vad") as load_vad,
            ):
                setup_thread = threading.Thread(target=setup_worker)
                setup_thread.start()
                setup_thread.join()

            load_vad.assert_called_once_with()
            self.assertEqual(setup_errors, [])
            self.assertTrue(ready_file.is_file())

    def test_prepares_livekit_ssl_context_before_agent_loop_on_worker_thread(self) -> None:
        from livekit.agents.utils import http_context

        calls: list[tuple[str, int]] = []
        main_thread = threading.get_ident()

        def set_cert_env() -> None:
            calls.append(("cert_env", threading.get_ident()))

        def create_ssl_context() -> object:
            calls.append(("ssl_context", threading.get_ident()))
            return object()

        with (
            patch.object(http_context, "_set_default_cert_env", side_effect=set_cert_env),
            patch.object(http_context, "_create_ssl_context", side_effect=create_ssl_context),
        ):
            _prepare_livekit_ssl_context()

        self.assertEqual(calls[0], ("cert_env", main_thread))
        self.assertEqual(calls[1][0], "ssl_context")
        self.assertNotEqual(calls[1][1], main_thread)

    def test_session_builder_applies_session_config(self) -> None:
        agent_dir = resolve_agent_dir("MyAgent", "outbound")
        config = AgentRuntime(agent_dir).config
        credentials = {
            "LIVEKIT_API_KEY": "test-api-key-long-enough-for-hmac-validation",
            "LIVEKIT_API_SECRET": "test-api-secret-long-enough-for-hmac-validation",
            "DEEPGRAM_API_KEY": "test-deepgram-key",
            "GOOGLE_API_KEY": "test-google-key",
            "SARVAM_API_KEY": "test-sarvam-key",
        }

        builder = AgentSessionBuilder(config)
        vad = object()
        with patch.dict(os.environ, credentials), patch(
            "livekit.plugins.silero.VAD.load",
            return_value=vad,
        ) as load_vad:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                builder.register_plugins()
                builder.load_vad()
                session = builder.create()
            finally:
                asyncio.set_event_loop(None)
                loop.close()

        load_vad.assert_called_once()
        self.assertIs(session._vad, vad)
        self.assertEqual(session._turn_detection, "vad")
        self.assertEqual(session._opts.endpointing["mode"], "dynamic")
        self.assertEqual(session._opts.endpointing["min_delay"], 0.5)
        self.assertEqual(session._opts.endpointing["max_delay"], 3.0)
        self.assertEqual(session._opts.interruption["mode"], "adaptive")

    def test_rejects_prompt_path_outside_agent_folder(self) -> None:
        agent_dir = resolve_agent_dir("MyAgent", "outbound")
        config_path = agent_dir / "config.json"
        original = config_path.read_text(encoding="utf-8")
        try:
            config_path.write_text(
                original.replace("prompt/prompt.md", "../../outside.md"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "inside the agent folder"):
                AgentRuntime(agent_dir)
        finally:
            config_path.write_text(original, encoding="utf-8")


class OutboundAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_outbound_agent_speaks_a_greeting_on_enter(self) -> None:
        agent = AgentRuntime(resolve_agent_dir("MyAgent", "outbound")).create_agent()
        session = SimpleNamespace(say=AsyncMock())

        with patch.object(
            agent,
            "_get_activity_or_raise",
            return_value=SimpleNamespace(session=session),
        ):
            await agent.on_enter()

        session.say.assert_awaited_once_with(
            "Hello! I'm MyAgent, your AI assistant. How can I help you today?"
        )


if __name__ == "__main__":
    unittest.main()
