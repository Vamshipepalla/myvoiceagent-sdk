from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator


def data_directory() -> Path:
    configured = os.getenv("MYVOICEAGENT_DATA_DIR")
    return Path(configured).expanduser().resolve() if configured else (
        Path(__file__).resolve().parent.parent / "data"
    )


def logs_directory() -> Path:
    configured = os.getenv("MYVOICEAGENT_LOG_DIR")
    return Path(configured).expanduser().resolve() if configured else (
        Path(__file__).resolve().parent.parent / "logs"
    )


class SimpleLanguageFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if (
            record.name.startswith("livekit.plugins.elevenlabs")
            and message == "elevenlabs tts returned error"
        ):
            error = record.__dict__.get("error")
            if isinstance(error, str) and error.strip():
                safe_error = re.sub(
                    r"""(?i)(api[_ -]?key|authorization|token)(["']?\s*[:=]\s*["']?|\s+)(?:bearer\s+)?[^,\s"'}]+""",
                    r"\1\2[redacted]",
                    error.strip(),
                )
                message = f"ElevenLabs rejected the TTS request: {safe_error[:500]}"
        if (
            record.name.startswith("livekit.agents")
            and "failed to connect to livekit" in message.lower()
        ):
            error = str(record.__dict__.get("error", ""))
            if "401" in message or "401" in error:
                message = (
                    "LiveKit rejected the agent login (401). Copy the URL, API key, "
                    "and API secret from the same LiveKit project into .env."
                )
            else:
                message = (
                    "The agent could not connect to LiveKit. Check the project URL "
                    "and your internet connection."
                )
        formatted = super().format(
            logging.makeLogRecord({**record.__dict__, "msg": message, "args": ()})
        )
        sarvam_api_key = os.getenv("SARVAM_API_KEY")
        if sarvam_api_key:
            formatted = formatted.replace(sarvam_api_key, "[redacted]")
        return formatted


def configure_logging(service: str = "application") -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", service):
        raise ValueError("Log service name must contain lowercase letters, numbers, '_' or '-'")
    log_dir = logs_directory()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{service}.log"
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if not any(
        isinstance(handler, RotatingFileHandler)
        and Path(handler.baseFilename).resolve() == log_path.resolve()
        for handler in root_logger.handlers
    ):
        handler = RotatingFileHandler(
            log_path,
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            SimpleLanguageFormatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root_logger.addHandler(handler)
    return log_path


class DashboardStore:
    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory).resolve() if directory else data_directory()
        self.transcripts_dir = self.directory / "transcripts"
        self.logs_dir = logs_directory()
        self.database_path = self.directory / "dashboard.sqlite3"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS calls (
                    id TEXT PRIMARY KEY,
                    agent_name TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT
                );
                CREATE TABLE IF NOT EXISTS latency_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
                    metric TEXT NOT NULL,
                    value_seconds REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS latency_samples_call_metric
                    ON latency_samples(call_id, metric);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_call(self, call_id: str, agent_name: str, direction: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO calls (id, agent_name, direction, started_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (call_id, agent_name, direction, self._now()),
            )

    def end_call(self, call_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE calls SET ended_at = COALESCE(ended_at, ?) WHERE id = ?
                """,
                (self._now(), call_id),
            )

    def transcript_path(self, call_id: str) -> Path:
        filename = hashlib.sha256(call_id.encode("utf-8")).hexdigest()
        return self.transcripts_dir / f"{filename}.jsonl"

    def add_transcript(self, call_id: str, transcript: dict[str, Any]) -> None:
        role = transcript.get("role")
        text = transcript.get("text")
        transcript_id = transcript.get("id")
        if role not in {"user", "agent"} or not isinstance(text, str) or not text.strip():
            raise ValueError("A transcript needs a speaker role and non-empty text")
        if not isinstance(transcript_id, str) or not transcript_id:
            raise ValueError("A transcript needs a non-empty ID")
        if transcript.get("final") is not True:
            raise ValueError("Only final transcript turns can be stored")
        path = self.transcript_path(call_id)
        with path.open("a", encoding="utf-8", newline="\n") as transcript_file:
            transcript_file.write(json.dumps(transcript, ensure_ascii=False) + "\n")

    def transcripts(self, call_id: str) -> list[dict[str, Any]]:
        path = self.transcript_path(call_id)
        if not path.is_file():
            return []
        with path.open("r", encoding="utf-8") as transcript_file:
            return [json.loads(line) for line in transcript_file if line.strip()]

    def add_metrics(self, call_id: str, role: str, metrics: Any) -> None:
        if role not in {"user", "assistant"} or not isinstance(metrics, dict):
            return
        allowed = (
            ("transcription_delay", "end_of_turn_delay")
            if role == "user"
            else ("llm_node_ttft", "tts_node_ttfb", "e2e_latency")
        )
        samples = [
            (call_id, name, float(metrics[name]), self._now())
            for name in allowed
            if isinstance(metrics.get(name), (int, float))
            and not isinstance(metrics.get(name), bool)
            and 0 <= float(metrics[name]) < float("inf")
        ]
        if not samples:
            return
        with self._connection() as connection:
            connection.executemany(
                """
                INSERT INTO latency_samples (call_id, metric, value_seconds, created_at)
                VALUES (?, ?, ?, ?)
                """,
                samples,
            )

    def calls(
        self, agent_name: str | None = None, direction: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM calls"
        conditions = []
        parameters: list[str] = []
        if agent_name:
            conditions.append("agent_name = ?")
            parameters.append(agent_name)
        if direction:
            conditions.append("direction = ?")
            parameters.append(direction)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY started_at DESC LIMIT 200"
        with self._connection() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        result = []
        for row in rows:
            call = dict(row)
            call["transcript_available"] = self.transcript_path(call["id"]).is_file()
            result.append(call)
        return result

    def call(self, call_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM calls WHERE id = ?", (call_id,)
            ).fetchone()
        return dict(row) if row else None

    def metric_samples(
        self, agent_name: str | None = None, direction: str | None = None
    ) -> dict[str, list[float]]:
        query = """
            SELECT latency_samples.metric, latency_samples.value_seconds
            FROM latency_samples
            JOIN calls ON calls.id = latency_samples.call_id
        """
        conditions = []
        parameters: list[str] = []
        if agent_name:
            conditions.append("calls.agent_name = ?")
            parameters.append(agent_name)
        if direction:
            conditions.append("calls.direction = ?")
            parameters.append(direction)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        with self._connection() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        samples: dict[str, list[float]] = {}
        for row in rows:
            samples.setdefault(row["metric"], []).append(row["value_seconds"])
        return samples
