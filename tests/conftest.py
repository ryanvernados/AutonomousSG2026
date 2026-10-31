import pytest
from fastapi.testclient import TestClient

from backend.api.app import DEMO_PROJECT, DEMO_USER, create_app
from backend.api.config import Settings
from backend.api.service import PlatformService
from backend.contracts import CreateSession
from backend.engine.planning import select_fallback
from backend.engine.scenarios import ScenarioRepository, initial_snapshot, load_scenario
from backend.persistence import SQLiteStore


@pytest.fixture
def snapshot():
    return initial_snapshot(load_scenario("sfo-synthetic-v1"))


@pytest.fixture
def plan(snapshot):
    result = select_fallback(snapshot, "TEST")
    assert result.plan is not None
    return result.plan


@pytest.fixture
def store(tmp_path):
    value = SQLiteStore(str(tmp_path / "test.sqlite3"))
    value.add_member(DEMO_PROJECT, DEMO_USER)
    yield value
    value.close()


@pytest.fixture
def service(store):
    return PlatformService(store, ScenarioRepository())


@pytest.fixture
def session(service):
    return service.create(CreateSession(command_id="create", base_state_version=0,
                                        scenario_id="sfo-synthetic-v1"), DEMO_USER)["session_id"]


@pytest.fixture
def client(store):
    app = create_app(Settings(auth_mode="demo", demo_token="test-token", _env_file=None), store=store)
    with TestClient(app) as value:
        value.headers["Authorization"] = "Bearer test-token"
        yield value
