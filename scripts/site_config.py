"""Read generator inputs from the same project configuration Quarto uses."""

from pathlib import Path

import yaml


def load_settings(path: Path | None = None) -> dict:
    config_path = path or Path(__file__).resolve().parents[1] / "docs/_quarto.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    settings = config.get("starr") if isinstance(config, dict) else None
    if not isinstance(settings, dict):
        raise ValueError(f"{config_path}: missing starr generator settings")
    return settings


SETTINGS = load_settings()
