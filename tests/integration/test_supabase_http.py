import json

import httpx
import pytest

from backend.api.errors import PlatformError
from backend.contracts import SessionDocument
from backend.persistence.supabase_http import SupabaseHTTPStore


def test_https_commit_uses_one_atomic_rpc(snapshot):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["apikey"] == "private-backend-test-key"
        assert request.url.path == "/rest/v1/rpc/commit_simulation_session"
        body = json.loads(request.content)
        assert body["p_expected_revision"] == 3
        assert body["p_document"]["snapshot"]["state_version"] == 0
        assert body["p_actor"] == "actor"
        return httpx.Response(200, json={"state_version": 1})

    store = SupabaseHTTPStore("https://project.supabase.co", "private-backend-test-key",
                              transport=httpx.MockTransport(handler))
    doc = SessionDocument(project_id="project", snapshot=snapshot, initial_snapshot=snapshot)
    assert store.commit(doc, 3, [], "actor", "once", "hash", {}) == {"state_version": 1}
    assert len(calls) == 1
    store.close()


@pytest.mark.parametrize("sqlstate,status", [("40001", 409), ("42501", 403), ("PGRST202", 503)])
def test_https_database_failures_preserve_error_boundary(sqlstate, status):
    store = SupabaseHTTPStore("https://project.supabase.co", "private-key", transport=httpx.MockTransport(
        lambda request: httpx.Response(status, json={"code": sqlstate, "message": "private details"})))
    with pytest.raises(PlatformError) as exc:
        store.all_documents()
    assert exc.value.status == status
    assert "private details" not in str(exc.value)
    store.close()


def test_https_membership_and_command_identity():
    def handler(request):
        if request.url.path.endswith("project_members"):
            assert request.url.params["user_id"] == "eq.user"
            return httpx.Response(200, json=[{"role": "controller"}])
        return httpx.Response(200, json=[{"fingerprint": "original", "response": {"state_version": 2}}])
    store = SupabaseHTTPStore("https://project.supabase.co", "private-key", transport=httpx.MockTransport(handler))
    assert store.role("project", "user") == "controller"
    assert store.command("session", "id", "original") == {"state_version": 2}
    with pytest.raises(PlatformError) as exc:
        store.command("session", "id", "changed")
    assert exc.value.body.code == "COMMAND_REUSED"
    store.close()
