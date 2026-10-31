from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    auth_mode: Literal["supabase", "demo"] = "supabase"
    demo_token: str = ""
    sqlite_path: str = "airport.sqlite3"
    database_url: str = ""
    persistence_mode: Literal["postgres", "supabase_http"] = "postgres"
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    supabase_secret_key: str = ""
    scenario_dir: str = "fixtures"
    cors_origins: list[str] = ["http://localhost:5173"]
    # Default clock speed for new sessions (simulated s per wall s; 1 = real time). UI can change it.
    sim_speed: int = Field(default=1, ge=1, le=600)
