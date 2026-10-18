import os
import unittest
from unittest.mock import patch

from myagentsdk import AgentBase, AgentSessionBuilder
from myagentrunner.runner import _load_agent_hooks, resolve_agent_dir


class RunnerTests(unittest.TestCase):
    def test_resolves_customer_agent_direction(self) -> None:
        agent_dir = resolve_agent_dir("MyAgent", "inbound")

        self.assertEqual(agent_dir.name, "inbound")
        self.assertEqual(agent_dir.parent.name, "MyAgent")
        self.assertTrue((agent_dir / "config.json").is_file())
        self.assertTrue((agent_dir / "prompt.md").is_file())

    def test_rejects_agent_path_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "single folder name"):
            resolve_agent_dir("..", "inbound")

    def test_loads_agent_hooks_and_base_agent(self) -> None:
        agent_dir = resolve_agent_dir("MyAgent", "inbound")
        config, create_agent, start_runtime = _load_agent_hooks(agent_dir)
        instructions = (agent_dir / "prompt.md").read_text(encoding="utf-8")

        agent = create_agent(config, instructions)

        self.assertEqual(config.name, "MyAgent")
        self.assertIsInstance(agent, AgentBase)
        self.assertTrue(callable(start_runtime))

    def test_session_builder_applies_session_config(self) -> None:
        agent_dir = resolve_agent_dir("MyAgent", "inbound")
        config, _, _ = _load_agent_hooks(agent_dir)
        credentials = {
            "LIVEKIT_API_KEY": "test-api-key-long-enough-for-hmac-validation",
            "LIVEKIT_API_SECRET": "test-api-secret-long-enough-for-hmac-validation",
        }

        with patch.dict(os.environ, credentials):
            session = AgentSessionBuilder(config).create()

        self.assertEqual(session._opts.endpointing["mode"], "dynamic")
        self.assertEqual(session._opts.endpointing["min_delay"], 0.5)
        self.assertEqual(session._opts.endpointing["max_delay"], 3.0)
        self.assertEqual(session._opts.interruption["mode"], "adaptive")


if __name__ == "__main__":
    unittest.main()
