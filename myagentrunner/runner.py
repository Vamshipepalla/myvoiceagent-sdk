import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable

from myagentsdk import AgentConfig, ConfigError, load_config

if TYPE_CHECKING:
    from livekit.agents import AgentServer, JobContext


def _load_module(module_name: str, file_path: Path) -> ModuleType:
    if not file_path.is_file():
        raise ConfigError(f"Agent module not found: '{file_path}'")
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ConfigError(f"Cannot load agent module: '{file_path}'")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def resolve_agent_dir(agent_name: str, direction: str) -> Path:
    if not agent_name or Path(agent_name).name != agent_name or agent_name in {".", ".."}:
        raise ConfigError("Agent name must be a single folder name")
    if direction not in {"inbound", "outbound"}:
        raise ConfigError("Agent direction must be 'inbound' or 'outbound'")

    customer_root = Path(__file__).resolve().parent / "customer"
    agent_dir = (customer_root / agent_name / direction).resolve()
    if not agent_dir.is_relative_to(customer_root.resolve()):
        raise ConfigError("Agent path must be inside the runner customer folder")
    if not agent_dir.is_dir():
        raise ConfigError(f"Agent folder not found: '{agent_dir}'")
    return agent_dir


def _load_agent_hooks(
    agent_dir: Path,
) -> tuple[AgentConfig, Callable[[AgentConfig, str], Any], Callable[..., Any]]:
    config = load_config(agent_dir / "config.json")
    module_prefix = (
        f"myagentrunner_customer_{agent_dir.parent.parent.name}_"
        f"{agent_dir.parent.name}_{agent_dir.name}"
    )
    agent_module = _load_module(f"{module_prefix}_agent", agent_dir / "agent.py")
    runtime_module = _load_module(f"{module_prefix}_runtime", agent_dir / "_runtime.py")

    create_agent = getattr(agent_module, "create_agent", None)
    start_runtime = getattr(runtime_module, "start", None)
    if not callable(create_agent):
        raise ConfigError(f"'{agent_dir / 'agent.py'}' must define create_agent(config, instructions)")
    if not callable(start_runtime):
        raise ConfigError(
            f"'{agent_dir / '_runtime.py'}' must define async start(ctx, create_agent, config)"
        )
    return config, create_agent, start_runtime


def create_server(agent_dir: str | Path) -> "AgentServer":
    """Load one customer's agent and register its runtime with LiveKit."""
    from livekit.agents import AgentServer

    resolved_dir = Path(agent_dir).resolve()
    config, create_agent, start_runtime = _load_agent_hooks(resolved_dir)
    server = AgentServer()

    async def entrypoint(ctx: "JobContext") -> None:
        await start_runtime(ctx, create_agent, config)

    server.rtc_session()(entrypoint)
    return server


def run_agent(agent_name: str, direction: str) -> None:
    from dotenv import load_dotenv
    from livekit.agents import cli

    repo_root = Path(__file__).resolve().parent.parent
    load_dotenv(repo_root / ".env")
    cli.run_app(create_server(resolve_agent_dir(agent_name, direction)))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a customer agent from myagentrunner/customer."
    )
    parser.add_argument("agent_name", help="Folder name under myagentrunner/customer")
    parser.add_argument("direction", choices=("inbound", "outbound"))
    args, livekit_args = parser.parse_known_args()
    sys.argv = [sys.argv[0], *livekit_args]
    run_agent(args.agent_name, args.direction)


if __name__ == "__main__":
    main()
