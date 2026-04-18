"""Synchronous HaaS client."""

from __future__ import annotations

import json
from typing import Generator, Iterator, Optional
from urllib.parse import quote

import httpx

from .exceptions import AuthenticationError, ConflictError, ForbiddenError, HaasError, NotFoundError, ServerError
from .types import Environment, ExecEvent, ExecResult, FileInfo, Snapshot


class Client:
    """Synchronous client for the HaaS REST API.

    Usage::

        client = haas.Client("https://your-haas-host", "your-api-key")
        env = client.create_environment(image="ubuntu:22.04")
        result = client.exec(env.id, ["bash", "-c", "echo hello"])
        print(result.stdout)   # "hello\\n"
        client.destroy_environment(env.id)
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http = http_client or httpx.Client(
            timeout=httpx.Timeout(timeout, read=None),  # read=None for streaming
        )
        self._http.headers["Authorization"] = f"Bearer {api_key}"

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # --- Environments -----------------------------------------------------------

    def create_environment(
        self,
        image: str = "",
        *,
        cpu: float = 0.0,
        memory_mb: int = 0,
        disk_mb: int = 0,
        network_policy: str = "",
        env_vars: Optional[dict[str, str]] = None,
        snapshot_id: str = "",
    ) -> Environment:
        """Provision a new container environment.

        Supply either *image* or *snapshot_id*. When *snapshot_id* is given the
        environment is restored from a previously-saved snapshot and *image* is
        ignored.

        Args:
            image: Docker image to use (e.g. ``"ubuntu:22.04"``).
            cpu: CPU cores to allocate (0 = server default).
            memory_mb: Memory in MB (0 = server default).
            disk_mb: Disk in MB (0 = server default).
            network_policy: ``"none"``, ``"egress-limited"``, or ``"full"``
                (empty = server default).
            env_vars: Environment variables to inject into the container.
            snapshot_id: Restore from this snapshot instead of a fresh image.

        Returns:
            The created :class:`Environment`.
        """
        body: dict = {}
        if snapshot_id:
            body["snapshot_id"] = snapshot_id
        elif image:
            body["image"] = image
        if cpu:
            body["cpu"] = cpu
        if memory_mb:
            body["memory_mb"] = memory_mb
        if disk_mb:
            body["disk_mb"] = disk_mb
        if network_policy:
            body["network_policy"] = network_policy
        if env_vars:
            body["env_vars"] = env_vars

        resp = self._request("POST", "/v1/environments", json=body)
        _raise_for_status(resp)
        data = resp.json()
        # POST /v1/environments returns {id, status, image} — fetch full record
        return self.get_environment(data["id"])

    def list_environments(self) -> list[Environment]:
        """Return all active environments owned by this API key."""
        resp = self._request("GET", "/v1/environments")
        _raise_for_status(resp)
        items = resp.json() or []
        return [Environment._from_dict(e) for e in items]

    def get_environment(self, env_id: str) -> Environment:
        """Return details of a specific environment."""
        resp = self._request("GET", f"/v1/environments/{env_id}")
        _raise_for_status(resp)
        return Environment._from_dict(resp.json())

    def destroy_environment(self, env_id: str) -> None:
        """Stop and permanently destroy an environment."""
        resp = self._request("DELETE", f"/v1/environments/{env_id}")
        _raise_for_status(resp)

    # --- Exec -------------------------------------------------------------------

    def exec(
        self,
        env_id: str,
        command: list[str],
        *,
        working_dir: str = "",
        timeout_seconds: int = 0,
    ) -> ExecResult:
        """Run a command and collect all output.

        Blocks until the command exits.

        Args:
            env_id: Environment ID.
            command: Command and arguments (e.g. ``["bash", "-c", "ls -la"]``).
            working_dir: Working directory inside the container.
            timeout_seconds: Command timeout (0 = server default).

        Returns:
            :class:`ExecResult` with ``stdout``, ``stderr``, and ``exit_code``.
        """
        result = ExecResult(stdout="", stderr="", exit_code="")
        for event in self.exec_stream(env_id, command, working_dir=working_dir, timeout_seconds=timeout_seconds):
            if event.stream == "stdout":
                result.stdout += event.data
            elif event.stream == "stderr":
                result.stderr += event.data
            elif event.stream == "exit":
                result.exit_code = event.data
        return result

    def exec_stream(
        self,
        env_id: str,
        command: list[str],
        *,
        working_dir: str = "",
        timeout_seconds: int = 0,
    ) -> Iterator[ExecEvent]:
        """Run a command and yield :class:`ExecEvent` objects as they arrive.

        Useful for streaming output in real time. The final event will have
        ``stream="exit"`` and ``data`` set to the exit code string.

        Args:
            env_id: Environment ID.
            command: Command and arguments.
            working_dir: Working directory inside the container.
            timeout_seconds: Command timeout (0 = server default).

        Yields:
            :class:`ExecEvent` for each NDJSON line from the server.
        """
        body: dict = {"command": command}
        if working_dir:
            body["working_dir"] = working_dir
        if timeout_seconds:
            body["timeout_seconds"] = timeout_seconds

        with self._http.stream("POST", self._url(f"/v1/environments/{env_id}/exec"), json=body) as resp:
            _raise_for_status(resp)
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    yield ExecEvent(stream=event["stream"], data=event["data"])
                except (json.JSONDecodeError, KeyError):
                    continue

    # --- Files ------------------------------------------------------------------

    def list_files(self, env_id: str, path: str = "/") -> list[FileInfo]:
        """List files and directories at *path* inside the environment."""
        resp = self._request("GET", f"/v1/environments/{env_id}/files", params={"path": path})
        _raise_for_status(resp)
        items = resp.json() or []
        return [FileInfo._from_dict(f) for f in items]

    def read_file(self, env_id: str, path: str) -> bytes:
        """Download a file from the environment. Returns raw bytes."""
        resp = self._request("GET", f"/v1/environments/{env_id}/files/content", params={"path": path})
        _raise_for_status(resp)
        return resp.content

    def write_file(self, env_id: str, path: str, content: bytes | str) -> None:
        """Upload *content* to *path* inside the environment.

        Parent directories are created automatically.

        Args:
            env_id: Environment ID.
            path: Destination path inside the container.
            content: File content as bytes or str (str is UTF-8 encoded).
        """
        if isinstance(content, str):
            content = content.encode()
        resp = self._request(
            "PUT",
            f"/v1/environments/{env_id}/files/content",
            params={"path": path},
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        _raise_for_status(resp)

    # --- Snapshots --------------------------------------------------------------

    def create_snapshot(self, env_id: str, *, label: str = "") -> Snapshot:
        """Snapshot a running environment's filesystem.

        Args:
            env_id: Environment ID of the running container.
            label: Optional human-readable label for the snapshot.

        Returns:
            The created :class:`Snapshot`.

        Raises:
            ConflictError: If the environment is not in the *running* state.
        """
        body: dict = {}
        if label:
            body["label"] = label
        resp = self._request("POST", f"/v1/environments/{env_id}/snapshots", json=body)
        _raise_for_status(resp)
        return Snapshot._from_dict(resp.json())

    def list_snapshots(self) -> list[Snapshot]:
        """Return all snapshots owned by this API key."""
        resp = self._request("GET", "/v1/snapshots")
        _raise_for_status(resp)
        items = resp.json() or []
        return [Snapshot._from_dict(s) for s in items]

    def get_snapshot(self, snapshot_id: str) -> Snapshot:
        """Return details of a specific snapshot."""
        resp = self._request("GET", f"/v1/snapshots/{snapshot_id}")
        _raise_for_status(resp)
        return Snapshot._from_dict(resp.json())

    def delete_snapshot(self, snapshot_id: str) -> None:
        """Delete a snapshot and its underlying Docker image."""
        resp = self._request("DELETE", f"/v1/snapshots/{snapshot_id}")
        _raise_for_status(resp)

    # --- internal ---------------------------------------------------------------

    def _url(self, path: str) -> str:
        return self._base_url + path

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        return self._http.request(method, self._url(path), **kwargs)


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    try:
        body = resp.json()
        message = body.get("error") or body.get("message") or str(resp.status_code)
        detail = body.get("detail", "")
        if detail:
            message = f"{message}: {detail}"
    except Exception:
        message = f"HTTP {resp.status_code}"

    if resp.status_code == 401:
        raise AuthenticationError(message, resp.status_code)
    if resp.status_code == 403:
        raise ForbiddenError(message, resp.status_code)
    if resp.status_code == 404:
        raise NotFoundError(message, resp.status_code)
    if resp.status_code == 409:
        raise ConflictError(message, resp.status_code)
    if resp.status_code >= 500:
        raise ServerError(message, resp.status_code)
    raise HaasError(message, resp.status_code)
