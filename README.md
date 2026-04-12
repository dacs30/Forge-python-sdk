# haas-client

Python SDK for the [HaaS](https://github.com/dacs30/haas) (Harness as a Service) API.

Spin up isolated Docker containers, run commands, and manage files — all from Python.

## Installation

```bash
pip install haas-client
```

## Quick start

```python
import haas

client = haas.Client("https://your-haas-host", "your-api-key")

# Create an environment
env = client.create_environment(image="ubuntu:22.04")

# Run a command
result = client.exec(env.id, ["bash", "-c", "echo hello"])
print(result.stdout)   # "hello\n"
print(result.ok)       # True

# Clean up
client.destroy_environment(env.id)
```

Use as a context manager to close the HTTP connection automatically:

```python
with haas.Client("https://your-haas-host", "your-api-key") as client:
    env = client.create_environment(image="python:3.12")
    result = client.exec(env.id, ["python", "-c", "print(2 + 2)"])
    print(result.stdout)  # "4\n"
```

## API reference

### `Client(base_url, api_key, *, timeout=30.0)`

All methods map 1-to-1 to the HaaS REST API. Errors raise typed exceptions (see [Error handling](#error-handling)).

#### Environments

```python
# Create
env = client.create_environment(
    image="ubuntu:22.04",
    cpu=1.0,           # cores (default: server config)
    memory_mb=2048,    # MB (default: server config)
    disk_mb=4096,      # MB (default: server config)
    network_policy="none",   # "none" | "egress-limited" | "full"
    env_vars={"FOO": "bar"},
)

# List — scoped to your API key
envs = client.list_environments()

# Get
env = client.get_environment("env_abc123")

# Destroy
client.destroy_environment("env_abc123")
```

#### Exec

```python
# Blocking — waits for the command to exit, collects all output
result = client.exec(
    env.id,
    ["bash", "-c", "ls -la"],
    working_dir="/tmp",       # optional
    timeout_seconds=30,       # optional
)
print(result.stdout)
print(result.stderr)
print(result.exit_code)  # "0", "1", etc.
print(result.ok)         # True if exit_code == "0"

# Streaming — yields ExecEvent objects in real time
for event in client.exec_stream(env.id, ["bash", "-c", "for i in 1 2 3; do echo $i; sleep 1; done"]):
    if event.stream == "stdout":
        print(event.data, end="", flush=True)
    elif event.stream == "exit":
        print(f"\nexited with {event.data}")
```

#### Files

```python
# List files at a path
files = client.list_files(env.id, "/tmp")
for f in files:
    print(f.name, f.size, f.is_dir)

# Read a file (returns bytes)
data = client.read_file(env.id, "/etc/hosts")
print(data.decode())

# Write a file (str or bytes; parent dirs created automatically)
client.write_file(env.id, "/tmp/script.py", "print('hello')")
```

## Error handling

All errors inherit from `haas.HaasError` and carry a `status_code` attribute.

| Exception | HTTP status |
|---|---|
| `AuthenticationError` | 401 |
| `ForbiddenError` | 403 (e.g. image not on allowlist) |
| `NotFoundError` | 404 |
| `ServerError` | 5xx |
| `HaasError` | any other 4xx |

```python
try:
    env = client.get_environment("env_gone")
except haas.NotFoundError:
    print("environment does not exist")
except haas.AuthenticationError:
    print("bad API key")
except haas.HaasError as e:
    print(f"API error {e.status_code}: {e}")
```

## Types

| Type | Fields |
|---|---|
| `Environment` | `id`, `status`, `spec`, `created_at`, `last_used_at`, `expires_at`, `container_id` |
| `EnvironmentSpec` | `image`, `cpu`, `memory_mb`, `disk_mb`, `network_policy`, `env_vars` |
| `ExecResult` | `stdout`, `stderr`, `exit_code`, `ok` |
| `ExecEvent` | `stream` (`"stdout"` / `"stderr"` / `"exit"`), `data` |
| `FileInfo` | `name`, `path`, `size`, `is_dir`, `mod_time` |
