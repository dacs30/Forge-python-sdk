"""Unit tests for the HaaS sync client using pytest-httpx."""

import json
import pytest
from pytest_httpx import HTTPXMock

import haas
from haas.exceptions import AuthenticationError, ConflictError, ForbiddenError, NotFoundError, ServerError


BASE = "http://localhost:8080"
KEY = "test-key"

ENV_DETAIL = {
    "id": "env_abc123",
    "status": "running",
    "container_id": "container_xyz",
    "spec": {
        "image": "ubuntu:22.04",
        "cpu": 1.0,
        "memory_mb": 2048,
        "disk_mb": 4096,
        "network_policy": "none",
        "env_vars": {},
    },
    "created_at": "2024-01-01T00:00:00Z",
    "last_used_at": "2024-01-01T00:00:00Z",
    "expires_at": "2024-01-01T01:00:00Z",
}

SNAPSHOT_DETAIL = {
    "id": "snap_a1b2c3d4e5f6",
    "environment_id": "env_abc123",
    "image_id": "sha256:abc123",
    "label": "after-setup",
    "size": 0,
    "created_at": "2024-01-01T00:30:00Z",
}


@pytest.fixture
def client(httpx_mock: HTTPXMock) -> haas.Client:
    return haas.Client(BASE, KEY)


# --- create_environment -------------------------------------------------------

def test_create_environment(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/v1/environments",
        status_code=201,
        json={"id": "env_abc123", "status": "running", "image": "ubuntu:22.04"},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/environments/env_abc123",
        status_code=200,
        json=ENV_DETAIL,
    )

    env = client.create_environment("ubuntu:22.04", cpu=1.0, memory_mb=2048)

    assert env.id == "env_abc123"
    assert env.status == "running"
    assert env.spec.image == "ubuntu:22.04"
    assert env.spec.cpu == 1.0


def test_create_environment_forbidden(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/v1/environments",
        status_code=403,
        json={"error": "image not allowed: badimage:latest", "code": 403},
    )
    with pytest.raises(ForbiddenError, match="image not allowed"):
        client.create_environment("badimage:latest")


# --- list_environments --------------------------------------------------------

def test_list_environments(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/environments",
        status_code=200,
        json=[ENV_DETAIL],
    )

    envs = client.list_environments()

    assert len(envs) == 1
    assert envs[0].id == "env_abc123"


def test_list_environments_empty(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/environments",
        status_code=200,
        json=[],
    )
    assert client.list_environments() == []


# --- get_environment ----------------------------------------------------------

def test_get_environment(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/environments/env_abc123",
        status_code=200,
        json=ENV_DETAIL,
    )

    env = client.get_environment("env_abc123")

    assert env.id == "env_abc123"
    assert env.spec.network_policy == "none"


def test_get_environment_not_found(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/environments/env_missing",
        status_code=404,
        json={"error": "environment not found", "code": 404},
    )
    with pytest.raises(NotFoundError):
        client.get_environment("env_missing")


# --- destroy_environment ------------------------------------------------------

def test_destroy_environment(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="DELETE",
        url=f"{BASE}/v1/environments/env_abc123",
        status_code=204,
    )
    client.destroy_environment("env_abc123")  # should not raise


def test_destroy_environment_not_found(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="DELETE",
        url=f"{BASE}/v1/environments/env_gone",
        status_code=404,
        json={"error": "environment not found", "code": 404},
    )
    with pytest.raises(NotFoundError):
        client.destroy_environment("env_gone")


# --- exec ---------------------------------------------------------------------

def _ndjson(*events: dict) -> bytes:
    return b"\n".join(json.dumps(e).encode() for e in events) + b"\n"


def test_exec_collects_output(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/v1/environments/env_abc123/exec",
        status_code=200,
        content=_ndjson(
            {"stream": "stdout", "data": "hello\n"},
            {"stream": "stderr", "data": "warn\n"},
            {"stream": "exit", "data": "0"},
        ),
    )

    result = client.exec("env_abc123", ["echo", "hello"])

    assert result.stdout == "hello\n"
    assert result.stderr == "warn\n"
    assert result.exit_code == "0"
    assert result.ok is True


def test_exec_nonzero_exit(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/v1/environments/env_abc123/exec",
        status_code=200,
        content=_ndjson({"stream": "exit", "data": "1"}),
    )

    result = client.exec("env_abc123", ["false"])

    assert result.exit_code == "1"
    assert result.ok is False


def test_exec_not_found(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/v1/environments/env_missing/exec",
        status_code=404,
        json={"error": "environment not found", "code": 404},
    )
    with pytest.raises(NotFoundError):
        client.exec("env_missing", ["ls"])


# --- files --------------------------------------------------------------------

FILE_LIST = [
    {"name": "etc", "path": "/etc", "size": 0, "is_dir": True, "mod_time": "2024-01-01T00:00:00Z"},
    {"name": "hosts", "path": "/etc/hosts", "size": 224, "is_dir": False, "mod_time": "2024-01-01T00:00:00Z"},
]


def test_list_files(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/environments/env_abc123/files?path=%2F",
        status_code=200,
        json=FILE_LIST,
    )

    files = client.list_files("env_abc123", "/")

    assert len(files) == 2
    assert files[0].name == "etc"
    assert files[0].is_dir is True
    assert files[1].size == 224


def test_read_file(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/environments/env_abc123/files/content?path=%2Fetc%2Fhosts",
        status_code=200,
        content=b"127.0.0.1 localhost\n",
    )

    data = client.read_file("env_abc123", "/etc/hosts")

    assert data == b"127.0.0.1 localhost\n"


def test_write_file(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="PUT",
        url=f"{BASE}/v1/environments/env_abc123/files/content?path=%2Ftmp%2Ftest.txt",
        status_code=204,
    )

    client.write_file("env_abc123", "/tmp/test.txt", "hello world")  # should not raise


def test_write_file_bytes(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="PUT",
        url=f"{BASE}/v1/environments/env_abc123/files/content?path=%2Ftmp%2Fbin",
        status_code=204,
    )

    client.write_file("env_abc123", "/tmp/bin", b"\x00\x01\x02")  # should not raise


# --- auth errors --------------------------------------------------------------

def test_unauthorized(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/environments",
        status_code=401,
        json={"error": "invalid or missing API key", "code": 401},
    )
    with pytest.raises(AuthenticationError):
        client.list_environments()


def test_server_error(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/environments",
        status_code=500,
        json={"error": "internal server error", "code": 500},
    )
    with pytest.raises(ServerError):
        client.list_environments()


# --- context manager ----------------------------------------------------------

def test_client_context_manager(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/environments",
        status_code=200,
        json=[],
    )
    with haas.Client(BASE, KEY) as client:
        envs = client.list_environments()
    assert envs == []


# --- create_environment with snapshot_id --------------------------------------

def test_create_environment_from_snapshot(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/v1/environments",
        status_code=201,
        json={"id": "env_restored", "status": "running", "image": "snap_a1b2c3d4e5f6"},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/environments/env_restored",
        status_code=200,
        json={**ENV_DETAIL, "id": "env_restored"},
    )

    env = client.create_environment(snapshot_id="snap_a1b2c3d4e5f6")

    assert env.id == "env_restored"
    assert env.status == "running"


# --- snapshots ----------------------------------------------------------------

def test_create_snapshot(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/v1/environments/env_abc123/snapshots",
        status_code=201,
        json=SNAPSHOT_DETAIL,
    )

    snap = client.create_snapshot("env_abc123", label="after-setup")

    assert snap.id == "snap_a1b2c3d4e5f6"
    assert snap.environment_id == "env_abc123"
    assert snap.image_id == "sha256:abc123"
    assert snap.label == "after-setup"


def test_create_snapshot_conflict(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE}/v1/environments/env_abc123/snapshots",
        status_code=409,
        json={"error": "environment must be running to create a snapshot", "code": 409},
    )
    with pytest.raises(ConflictError, match="must be running"):
        client.create_snapshot("env_abc123")


def test_list_snapshots(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/snapshots",
        status_code=200,
        json=[SNAPSHOT_DETAIL],
    )

    snaps = client.list_snapshots()

    assert len(snaps) == 1
    assert snaps[0].id == "snap_a1b2c3d4e5f6"


def test_list_snapshots_empty(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/snapshots",
        status_code=200,
        json=[],
    )
    assert client.list_snapshots() == []


def test_get_snapshot(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/snapshots/snap_a1b2c3d4e5f6",
        status_code=200,
        json=SNAPSHOT_DETAIL,
    )

    snap = client.get_snapshot("snap_a1b2c3d4e5f6")

    assert snap.id == "snap_a1b2c3d4e5f6"
    assert snap.label == "after-setup"


def test_get_snapshot_not_found(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE}/v1/snapshots/snap_missing",
        status_code=404,
        json={"error": "snapshot not found", "code": 404},
    )
    with pytest.raises(NotFoundError):
        client.get_snapshot("snap_missing")


def test_delete_snapshot(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="DELETE",
        url=f"{BASE}/v1/snapshots/snap_a1b2c3d4e5f6",
        status_code=204,
    )
    client.delete_snapshot("snap_a1b2c3d4e5f6")  # should not raise


def test_delete_snapshot_not_found(httpx_mock: HTTPXMock, client: haas.Client):
    httpx_mock.add_response(
        method="DELETE",
        url=f"{BASE}/v1/snapshots/snap_gone",
        status_code=404,
        json={"error": "snapshot not found", "code": 404},
    )
    with pytest.raises(NotFoundError):
        client.delete_snapshot("snap_gone")
