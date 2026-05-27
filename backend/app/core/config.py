from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 自动加载 .env — 优先项目根目录（PaperAgent/.env），其次 backend/.env
load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env")
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


@dataclass(frozen=True)
class Settings:
    app_name: str = "PaperAgent"
    api_prefix: str = "/api"
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///data/paperagent.db")
    data_dir: Path = Path(os.getenv("DATA_DIR", "data"))
    paper_dir: Path = Path(os.getenv("PAPER_DIR", "data/papers"))
    vector_dir: Path = Path(os.getenv("VECTOR_DIR", "data/chroma"))
    dashscope_api_key: str | None = os.getenv("DASHSCOPE_API_KEY")
    dashscope_chat_model: str = os.getenv("DASHSCOPE_CHAT_MODEL", "qwen-plus")
    dashscope_embedding_model: str = os.getenv(
        "DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v3"
    )
    chroma_host: str | None = os.getenv("CHROMA_HOST")
    chroma_port: int = int(os.getenv("CHROMA_PORT", "8001"))
    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        ).split(",")
        if origin.strip()
    )

    @property
    def sqlite_path(self) -> Path:
        if self.database_url.startswith("sqlite:///"):
            return Path(self.database_url.replace("sqlite:///", "", 1))
        return Path("data/paperagent.db")


settings = Settings()

