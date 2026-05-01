"""Shared test fixtures — jira-emulator server for integration tests."""
import base64
import json
import os
import socket
import threading
import time
import urllib.request

import pytest


SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")


def _find_free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _jira_request(base_url, method, path, body=None):
    """Make a request to the jira-emulator."""
    url = f"{base_url}{path}"
    data = json.dumps(body).encode() if body is not None else None
    creds = base64.b64encode(b"admin:admin").decode()
    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        if resp.status == 204:
            return None
        body_bytes = resp.read()
        return json.loads(body_bytes) if body_bytes else None


@pytest.fixture(scope="session")
def jira_emu():
    """Start a jira-emulator server for the test session."""
    port = _find_free_port()

    os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
    os.environ["AUTH_MODE"] = "none"
    os.environ["SEED_DATA"] = "true"

    from jira_emulator.config import get_settings
    get_settings.cache_clear()
    from jira_emulator.database import reset_engine
    reset_engine()
    from jira_emulator.app import create_app
    import uvicorn

    app = create_app()

    # In-memory remote links store (the emulator doesn't support them)
    _remote_links: dict[str, list] = {}

    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.post("/rest/api/3/issue/{key}/remotelink")
    @app.post("/rest/api/2/issue/{key}/remotelink")
    async def _create_remote_link(key: str, request: Request):
        body = await request.json()
        _remote_links.setdefault(key, []).append(body)
        return JSONResponse({"id": len(_remote_links[key])}, status_code=201)

    @app.get("/rest/api/3/issue/{key}/remotelink")
    @app.get("/rest/api/2/issue/{key}/remotelink")
    async def _get_remote_links(key: str):
        return JSONResponse(_remote_links.get(key, []))

    # Expose the store so per-test reset can clear it
    app.state.remote_links = _remote_links

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{base_url}/")
            break
        except Exception:
            time.sleep(0.05)

    yield base_url, _remote_links
    server.should_exit = True


@pytest.fixture
def jira(jira_emu):
    """Per-test fixture: resets emulator state and provides helpers."""
    base_url, remote_links = jira_emu

    from jira_emulator.services import seed_service, import_service
    _extra_link_types = [
        {"name": "Cloners",
         "inward_description": "is cloned by",
         "outward_description": "clones"},
        {"name": "Issue split",
         "inward_description": "is split from",
         "outward_description": "split to"},
    ]
    _orig = seed_service.LINK_TYPES
    seed_service.LINK_TYPES = _orig + [
        lt for lt in _extra_link_types
        if lt["name"] not in {x["name"] for x in _orig}
    ]

    if "git_pull_request" not in import_service.CUSTOM_FIELD_MAP:
        import_service.CUSTOM_FIELD_MAP["git_pull_request"] = (
            "customfield_10875", "json"
        )

    req = urllib.request.Request(
        f"{base_url}/api/admin/reset", method="POST", data=b"")
    urllib.request.urlopen(req)
    remote_links.clear()

    class JiraHelper:
        url = base_url

        @staticmethod
        def create(key, summary, description, labels=None, components=None,
                   git_pull_request=None):
            """Import an issue with a specific key."""
            issue = {
                "key": key,
                "summary": summary,
                "project": key.split("-")[0],
                "issue_type": "Feature Request",
                "description": description,
            }
            if labels:
                issue["labels"] = labels
            if components:
                issue["components"] = [{"name": c} for c in components]
            if git_pull_request is not None:
                issue["git_pull_request"] = git_pull_request
            _jira_request(base_url, "POST", "/api/admin/import",
                          {"issues": [issue]})

        @staticmethod
        def add_remote_link(key, url, title):
            """Add a remote link (web link) to an issue."""
            _jira_request(base_url, "POST",
                          f"/rest/api/3/issue/{key}/remotelink",
                          {"object": {"url": url, "title": title}})

        @staticmethod
        def get(key):
            """GET an issue, return parsed JSON."""
            return _jira_request(base_url, "GET",
                                 f"/rest/api/3/issue/{key}")

        @staticmethod
        def search(jql, fields="key,description,labels"):
            """JQL search, return list of issues."""
            from urllib.parse import quote
            path = (f"/rest/api/3/search/jql"
                    f"?jql={quote(jql, safe='')}&fields={fields}")
            data = _jira_request(base_url, "GET", path)
            return data.get("issues", [])

        @staticmethod
        def request(method, path, body=None):
            """Make an arbitrary API request to the emulator."""
            return _jira_request(base_url, method, path, body)

    return JiraHelper()


@pytest.fixture
def art_dir(tmp_path):
    """Create artifact directory structure in a temp dir and chdir there."""
    (tmp_path / "artifacts").mkdir(parents=True)
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(old_cwd)


@pytest.fixture
def scripts_dir():
    """Return the path to the scripts/ directory."""
    return os.path.abspath(SCRIPTS_DIR)
