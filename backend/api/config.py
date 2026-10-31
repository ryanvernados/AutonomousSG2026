from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    auth_mode: Literal["supabase", "demo"] = "supabase"
    demo_token: str = ""
    sqlite_path: str = "airport.sqlite3"
    database_url: str = ""
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    scenario_dir: str = "fixtures"
    cors_origins: list[str] = ["http://localhost:5173"]
