from pathlib import Path


def load_environment(env_file: str | Path) -> None:
    """Load environment values from a dotenv file without replacing existing values."""
    from dotenv import load_dotenv

    load_dotenv(Path(env_file), override=False)


def require_secret(name: str) -> str:
    """Return a configured secret or raise an actionable error."""
    import os

    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable '{name}' is not set")
    return value
