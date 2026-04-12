"""Public types for the HaaS API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class EnvironmentSpec:
    image: str
    cpu: float
    memory_mb: int
    disk_mb: int
    network_policy: str
    env_vars: dict[str, str] = field(default_factory=dict)


@dataclass
class Environment:
    id: str
    status: str
    spec: EnvironmentSpec
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    container_id: str = ""

    @classmethod
    def _from_dict(cls, data: dict) -> "Environment":
        spec_data = data.get("spec", {})
        spec = EnvironmentSpec(
            image=spec_data.get("image", ""),
            cpu=spec_data.get("cpu", 0.0),
            memory_mb=spec_data.get("memory_mb", 0),
            disk_mb=spec_data.get("disk_mb", 0),
            network_policy=spec_data.get("network_policy", ""),
            env_vars=spec_data.get("env_vars") or {},
        )
        return cls(
            id=data["id"],
            status=data["status"],
            spec=spec,
            container_id=data.get("container_id", ""),
            created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")),
            last_used_at=datetime.fromisoformat(data["last_used_at"].replace("Z", "+00:00")),
            expires_at=datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")),
        )


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: str  # string to match the API ("0", "1", etc.)

    @property
    def ok(self) -> bool:
        """True if the command exited with code 0."""
        return self.exit_code == "0"


@dataclass
class ExecEvent:
    stream: str  # "stdout", "stderr", or "exit"
    data: str


@dataclass
class FileInfo:
    name: str
    path: str
    size: int
    is_dir: bool
    mod_time: str

    @classmethod
    def _from_dict(cls, data: dict) -> "FileInfo":
        return cls(
            name=data["name"],
            path=data["path"],
            size=data["size"],
            is_dir=data["is_dir"],
            mod_time=data["mod_time"],
        )
