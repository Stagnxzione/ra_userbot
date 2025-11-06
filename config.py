# config.py (при первом запуске)
from __future__ import annotations
import os
from pydantic import BaseModel, Field, ValidationError

class Settings(BaseModel):
    BOT_TOKEN: str = Field(...)
    MANAGED_BOT_USERNAME: str = Field(...)
    API_ID: int = Field(...)
    API_HASH: str = Field(...)
    USERBOT_SESSION: str = Field("") 

def load_settings() -> Settings:
    raw = {
        "BOT_TOKEN": os.getenv("BOT_TOKEN", ""),
        "MANAGED_BOT_USERNAME": os.getenv("MANAGED_BOT_USERNAME", ""),
        "API_ID": os.getenv("API_ID", ""),
        "API_HASH": os.getenv("API_HASH", ""),
        "USERBOT_SESSION": os.getenv("USERBOT_SESSION", ""),
    }
    try:
        if raw.get("API_ID"):
            raw["API_ID"] = int(raw["API_ID"])
        return Settings(**raw)
    except (ValidationError, ValueError) as e:
        raise SystemExit(f"Config error: {e}")
