from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
API_URL = "http://127.0.0.1:8000"
FRONTEND_URL = "http://127.0.0.1:5173"
AGENT_ENV = (
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "DEEPGRAM_API_KEY",
    "GOOGLE_API_KEY",
    "SARVAM_API_KEY",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start the MYvoiceagent API, frontend, and local agent worker."
    )
    parser.add_argument(
        "--agent",
        default="MyAgent",
        help="Agent folder under myagentrunner/customer (default: MyAgent)",
    )
    parser.add_argument(
        "--skip-worker",
        action="store_true",
        help="Start only the dashboard API and frontend",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the dashboard automatically",
    )
    return parser


def _start_process(
    command: Sequence[str],
    *,
    cwd: Path,
    label: str,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    print(f"Starting {label}: {' '.join(command)}")
    return subprocess.Popen(
        list(command),
        cwd=cwd,
        env=env,
    )


def _wait_for_service(
    process: subprocess.Popen, url: str, label: str, timeout: float = 45
) -> None:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"{label} stopped during startup (exit code {process.returncode})."
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    print(f"{label} is ready at {url}")
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
        time.sleep(0.5)
    raise RuntimeError(
        f"{label} did not become ready at {url} within {timeout:g} seconds. "
        f"Last connection error: {last_error}"
    )


def _stop_processes(processes: list[subprocess.Popen]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _open_chrome(url: str) -> None:
    candidates = [
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
    ]
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(variable)
        if root:
            candidates.append(
                str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
            )
    executable = next(
        (candidate for candidate in candidates if candidate and Path(candidate).is_file()),
        None,
    )
    if executable is None:
        raise RuntimeError(
            "Could not find Google Chrome. Install Chrome or add chrome.exe to PATH."
        )
    subprocess.Popen([executable, "--new-window", url], close_fds=True)


def _missing_worker_environment() -> list[str]:
    return [name for name in AGENT_ENV if not os.environ.get(name, "").strip()]


def _wait_for_worker(
    process: subprocess.Popen,
    ready_file: Path,
    timeout: float = 90,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"The agent worker stopped during startup (exit code {process.returncode})."
            )
        if ready_file.is_file():
            print("Agent worker's warm process is ready.")
            return
        time.sleep(0.25)
    raise RuntimeError(
        f"The agent worker did not become ready within {timeout:g} seconds. "
        "Check logs/agent.log for provider, VAD, or LiveKit errors."
    )


def main() -> int:
    args = _parser().parse_args()
    processes: list[subprocess.Popen] = []
    ready_file = Path(tempfile.gettempdir()) / f"myvoiceagent-worker-ready-{os.getpid()}"
    try:
        if not (FRONTEND / "node_modules" / "vite" / "bin" / "vite.js").is_file():
            raise RuntimeError(
                "Frontend dependencies are missing. Run `npm install` from the frontend folder."
            )

        if not args.skip_worker:
            from myagentrunner.runner import resolve_agent_dir

            resolve_agent_dir(args.agent, "outbound")
            from myagentsdk import load_environment

            load_environment(ROOT / ".env")
            missing = _missing_worker_environment()
            if missing:
                raise RuntimeError(
                    f"Cannot start the {args.agent} outbound worker. "
                    "Set these variables in the repository .env file: "
                    + ", ".join(missing)
                    + ". Values are not shown."
                )

        api = _start_process(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "myagentrunner.dashboard:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            cwd=ROOT,
            label="dashboard API",
        )
        processes.append(api)

        node = os.environ.get("NODE_EXE") or "node"
        frontend = _start_process(
            [
                node,
                str(FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"),
                "--host",
                "127.0.0.1",
                "--strictPort",
            ],
            cwd=FRONTEND,
            label="frontend",
        )
        processes.append(frontend)

        if not args.skip_worker:
            ready_file.unlink(missing_ok=True)
            worker = _start_process(
                [
                    sys.executable,
                    "-m",
                    "myagentrunner",
                    args.agent,
                    "outbound",
                    "dev",
                ],
                cwd=ROOT,
                label=f"{args.agent} outbound worker",
                env={
                    **os.environ,
                    "MYVOICEAGENT_WORKER_READY_FILE": str(ready_file),
                },
            )
            processes.append(worker)

        _wait_for_service(api, f"{API_URL}/api/health", "Dashboard API")
        _wait_for_service(frontend, FRONTEND_URL, "Frontend")
        if not args.skip_worker:
            _wait_for_worker(worker, ready_file)

        print(f"\nDashboard is ready: {FRONTEND_URL}")
        print("Keep this launcher running while you use the dashboard.")
        print("Press Ctrl+C here to stop the local services.")
        if not args.no_browser:
            _open_chrome(FRONTEND_URL)

        while True:
            for process in processes:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"A local service stopped (exit code {process.returncode}). "
                        "Check its console window for details."
                    )
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping local services...")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Could not start the local project: {error}", file=sys.stderr)
        return 1
    finally:
        _stop_processes(processes)
        ready_file.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())