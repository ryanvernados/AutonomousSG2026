"""Member 1 API with Member 3's coordinator injected.

    uv run uvicorn backend.coordination.server:app --host 127.0.0.1 --port 8000 --workers 1

AIRPORT_MODEL_MODE=mock (default, deterministic MockAdapter) or live (needs AIRPORT_MODEL and
OPENAI_API_KEY in the backend environment). The mode is reported in every negotiation record.
"""
from backend.api.app import create_app

from .platform_adapter import PlatformCoordinator

app = create_app(coordinator=PlatformCoordinator.from_env())
