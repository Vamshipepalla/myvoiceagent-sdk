import argparse
from concurrent.futures import ThreadPoolExecutor
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from myagentsdk import (
    AgentRuntime,
    ConfigError,
    configure_logging,
    create_server as create_agent_server,
    load_environment,
)

if TYPE_CHECKING:
    from livekit.agents import AgentServer


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


def create_server(agent_dir: str | Path) -> "AgentServer":
    """Load one customer's agent through the SDK and register it with LiveKit."""
    return create_agent_server(AgentRuntime(agent_dir))


def _prepare_livekit_ssl_context() -> None:
    from livekit.agents.utils import http_context

    http_context._set_default_cert_env()
    # LiveKit builds this cached TLS context in its event loop if it is not prepared here.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="livekit-ssl") as executor:
        executor.submit(http_context._create_ssl_context).result()


def run_agent(agent_name: str, direction: str) -> None:
    from livekit.agents import cli

    repo_root = Path(__file__).resolve().parent.parent
    load_environment(repo_root / ".env")
    log_path = configure_logging("agent")
    logging.getLogger(__name__).info(
        "Starting the %s %s agent. Agent logs are saved to %s.",
        agent_name,
        direction,
        log_path,
    )
    try:
        _prepare_livekit_ssl_context()
    except Exception:
        logging.getLogger(__name__).exception(
            "The secure connection setup failed before the agent could start."
        )
        raise
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
