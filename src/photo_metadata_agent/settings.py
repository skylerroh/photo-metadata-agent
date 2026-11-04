from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PHOTO_AGENT_", env_file=".env", extra="ignore")
    data_dir: Path = Path("data")
    provider: Literal["demo", "typesafe", "openai", "openrouter", "ollama"] = "demo"
    model: str = "demo-v1"
    ollama_url: str = "http://localhost:11434"
    typesafe_images_enabled: bool = False
    timeout_seconds: float = Field(default=120, gt=0, le=600)
    max_attempts: int = Field(default=2, ge=1, le=5)
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, gt=0)

    @property
    def database(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir / "catalog.sqlite3"
