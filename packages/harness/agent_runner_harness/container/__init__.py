"""Container runner — wraps docker run for agent execution.

Provides run_in_container() and build_image() helpers that shell out
to the Docker CLI. The harness calls these when running in authoritative
(non-dev) mode.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ContainerResult:
    """Result from a container run."""

    returncode: int
    stdout: str
    stderr: str
    image: str
    command: list[str]
    duration_seconds: float = 0.0

    @property
    def success(self) -> bool:
        """True if the container exited with code 0."""
        return self.returncode == 0


def run_in_container(
    image: str,
    working_copy: Path,
    env: dict[str, str],
    command: list[str],
    limits: dict[str, Any] | None = None,
    *,
    timeout: int = 600,
) -> ContainerResult:
    """Run a command inside a Docker container.

    The working copy is bind-mounted read-write to /workspace/repo.
    Environment variables from ``env`` are injected via --env flags.

    Args:
        image: Docker image tag or digest.
        working_copy: Host path to mount as /workspace/repo.
        env: Environment variables to pass into the container.
        command: Command to run inside the container.
        limits: Optional resource limits dict (e.g. {'memory': '4g', 'cpus': '2'}).
        timeout: Subprocess timeout in seconds.

    Returns:
        ContainerResult with returncode, stdout, stderr.
    """
    import time

    limits = limits or {}
    working_copy = Path(working_copy)

    docker_args = [
        "docker", "run",
        "--rm",
        "--user", "runner",
        "--volume", f"{working_copy}:/workspace/repo:rw",
        "--workdir", "/workspace/repo",
    ]

    # Resource limits
    if "memory" in limits:
        docker_args += ["--memory", str(limits["memory"])]
    if "cpus" in limits:
        docker_args += ["--cpus", str(limits["cpus"])]

    # Environment variables
    for k, v in env.items():
        docker_args += ["--env", f"{k}={v}"]

    docker_args.append(image)
    docker_args.extend(command)

    start = time.monotonic()
    try:
        result = subprocess.run(
            docker_args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return ContainerResult(
            returncode=3,
            stdout="",
            stderr=f"Container timed out after {timeout}s",
            image=image,
            command=command,
            duration_seconds=float(timeout),
        )
    duration = time.monotonic() - start

    return ContainerResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        image=image,
        command=command,
        duration_seconds=duration,
    )


def build_image(dockerfile_dir: Path, tag: str) -> str:
    """Build a Docker image from a Dockerfile directory.

    Args:
        dockerfile_dir: Directory containing the Dockerfile (runner.Dockerfile).
        tag: Tag to apply to the built image.

    Returns:
        The image digest string (from docker inspect).

    Raises:
        subprocess.CalledProcessError: If the build fails.
    """
    dockerfile_dir = Path(dockerfile_dir)
    result = subprocess.run(
        [
            "docker", "build",
            "-f", str(dockerfile_dir / "runner.Dockerfile"),
            "-t", tag,
            "--label", "agent-runner=true",
            str(dockerfile_dir.parent),  # build context is repo root
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    # Get digest
    inspect = subprocess.run(
        ["docker", "inspect", "--format", "{{index .RepoDigests 0}}", tag],
        capture_output=True,
        text=True,
    )
    digest = inspect.stdout.strip()
    return digest or tag
