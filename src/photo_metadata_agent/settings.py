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

    max_video_upload_bytes: int = Field(default=250 * 1024 * 1024, gt=0)
    video_mode: Literal["auto", "native", "frames"] = "auto"
    model_supports_video: bool = False
    video_frame_count: int = Field(default=8, ge=1, le=32)
    video_max_side: int = Field(default=768, ge=280, le=1568)
    video_start_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    video_max_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    video_fps: int = Field(default=4, ge=1, le=30)
    video_max_bytes: int = Field(default=20 * 1024 * 1024, gt=0)

    @property
    def database(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir / "catalog.sqlite3"
