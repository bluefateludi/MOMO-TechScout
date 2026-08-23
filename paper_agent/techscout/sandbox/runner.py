"""Docker CLI runner with explicit argv and deterministic fake."""

import re
import subprocess
import time
from collections import defaultdict, deque
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from paper_agent.techscout.errors import FailureCode
from paper_agent.techscout.sandbox.types import (
    CompiledCommand,
    ExecutionStatus,
    InstallNetworkPolicy,
    NetworkAccess,
    PocStage,
    SandboxLimits,
    SandboxResult,
)


class SandboxRunner(Protocol):
    def run(
        self,
        command: CompiledCommand,
        run_workspace: Path,
        *,
        timeout_seconds: float | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> SandboxResult: ...


class DockerCliRunner:
    def __init__(
        self,
        workspace_root: Path,
        limits: SandboxLimits | None = None,
        *,
        docker_executable: str = "docker",
        install_network: InstallNetworkPolicy | None = None,
    ) -> None:
        self._workspace_root = workspace_root.resolve(strict=True)
        self._limits = limits or SandboxLimits()
        self._docker_executable = docker_executable
        self._install_network = install_network

    def docker_argv(
        self,
        command: CompiledCommand,
        run_workspace: Path,
        *,
        cidfile: Path | None = None,
    ) -> list[str]:
        workspace = run_workspace.resolve(strict=True)
        if workspace != self._workspace_root and self._workspace_root not in workspace.parents:
            raise ValueError("run workspace must stay inside the configured workspace root")

        network = "none"
        if (
            command.stage is PocStage.INSTALL
            and command.network_access is NetworkAccess.INSTALL_ONLY
        ):
            if self._install_network is None:
                raise PermissionError(
                    "install requires a dedicated destination-allowlisted Docker network"
                )
            network = self._install_network.docker_network

        argv = [
            self._docker_executable,
            "run",
            "--rm",
            "--init",
            "--cpus",
            str(self._limits.cpus),
            "--memory",
            self._limits.memory,
            "--pids-limit",
            str(self._limits.pids),
            "--storage-opt",
            f"size={self._limits.disk}",
            "--network",
            network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={self._limits.tmpfs}",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,source={workspace},target=/workspace",
            "--env",
            "HOME=/tmp",
        ]
        if cidfile is not None:
            argv.extend(("--cidfile", str(cidfile)))
        argv.extend((command.image, *command.argv))
        return argv

    def run(
        self,
        command: CompiledCommand,
        run_workspace: Path,
        *,
        timeout_seconds: float | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> SandboxResult:
        workspace = run_workspace.resolve(strict=True)
        cidfile = workspace / f".techscout-container-{uuid4().hex}.cid"
        argv = self.docker_argv(command, workspace, cidfile=cidfile)
        started = time.monotonic()
        effective_timeout = min(
            self._limits.timeout_seconds,
            timeout_seconds if timeout_seconds is not None else self._limits.timeout_seconds,
        )
        if cancel_requested is not None and cancel_requested():
            return _cancelled_result(command, started)
        if cancel_requested is not None:
            try:
                return self._run_controlled(
                    command,
                    argv,
                    cidfile,
                    started=started,
                    timeout_seconds=effective_timeout,
                    cancel_requested=cancel_requested,
                )
            finally:
                cidfile.unlink(missing_ok=True)
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self._force_remove_container(cidfile)
            return SandboxResult(
                command=command,
                status=ExecutionStatus.TIMED_OUT,
                exit_code=None,
                timed_out=True,
                duration_ms=_duration_ms(started),
                stdout=_bounded_text(exc.stdout, self._limits.output_bytes),
                stderr=_bounded_text(exc.stderr, self._limits.output_bytes),
                failure_code=FailureCode.POC_TIMEOUT,
            )
        except OSError as exc:
            return SandboxResult(
                command=command,
                status=ExecutionStatus.UNAVAILABLE,
                exit_code=None,
                timed_out=False,
                duration_ms=_duration_ms(started),
                stderr=_bounded_text(str(exc), self._limits.output_bytes),
                failure_code=FailureCode.TOOL_UNAVAILABLE,
            )
        finally:
            cidfile.unlink(missing_ok=True)

        if cancel_requested is not None and cancel_requested():
            return _cancelled_result(command, started)
        succeeded = completed.returncode == 0
        return SandboxResult(
            command=command,
            status=(ExecutionStatus.SUCCEEDED if succeeded else ExecutionStatus.FAILED),
            exit_code=completed.returncode,
            timed_out=False,
            duration_ms=_duration_ms(started),
            stdout=_bounded_text(completed.stdout, self._limits.output_bytes),
            stderr=_bounded_text(completed.stderr, self._limits.output_bytes),
            failure_code=None if succeeded else FailureCode.POC_NONZERO_EXIT,
        )

    def _run_controlled(
        self,
        command: CompiledCommand,
        argv: list[str],
        cidfile: Path,
        *,
        started: float,
        timeout_seconds: float,
        cancel_requested: Callable[[], bool],
    ) -> SandboxResult:
        """Poll a Docker CLI process so cancellation can stop the owned container."""
        try:
            process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            return SandboxResult(
                command=command,
                status=ExecutionStatus.UNAVAILABLE,
                exit_code=None,
                timed_out=False,
                duration_ms=_duration_ms(started),
                stderr=_bounded_text(str(exc), self._limits.output_bytes),
                failure_code=FailureCode.TOOL_UNAVAILABLE,
            )

        deadline = started + timeout_seconds
        while True:
            if cancel_requested():
                self._stop_controlled_process(process, cidfile)
                return _cancelled_result(command, started)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._stop_controlled_process(process, cidfile)
                return SandboxResult(
                    command=command,
                    status=ExecutionStatus.TIMED_OUT,
                    exit_code=None,
                    timed_out=True,
                    duration_ms=_duration_ms(started),
                    failure_code=FailureCode.POC_TIMEOUT,
                )
            try:
                stdout, stderr = process.communicate(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                continue
            if cancel_requested():
                self._stop_controlled_process(process, cidfile)
                return _cancelled_result(command, started)
            succeeded = process.returncode == 0
            return SandboxResult(
                command=command,
                status=(
                    ExecutionStatus.SUCCEEDED if succeeded else ExecutionStatus.FAILED
                ),
                exit_code=process.returncode,
                timed_out=False,
                duration_ms=_duration_ms(started),
                stdout=_bounded_text(stdout, self._limits.output_bytes),
                stderr=_bounded_text(stderr, self._limits.output_bytes),
                failure_code=None if succeeded else FailureCode.POC_NONZERO_EXIT,
            )

    def _stop_controlled_process(self, process: subprocess.Popen, cidfile: Path) -> None:
        self._force_remove_container(cidfile)
        try:
            process.terminate()
            process.communicate(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.communicate(timeout=1)
            except (OSError, subprocess.SubprocessError):
                pass
        self._force_remove_container(cidfile)

    def _force_remove_container(self, cidfile: Path) -> None:
        try:
            container_id = cidfile.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if re.fullmatch(r"[a-f0-9]{12,64}", container_id) is None:
            return
        try:
            subprocess.run(
                [self._docker_executable, "rm", "--force", container_id],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass


class FakeSandboxRunner:
    """FIFO deterministic runner used by ordinary tests and the Agent fake runtime."""

    def __init__(self) -> None:
        self._results: dict[tuple[str, PocStage], deque[SandboxResult]] = defaultdict(deque)
        self.calls: list[tuple[CompiledCommand, Path]] = []

    def queue(self, result: SandboxResult) -> None:
        key = (result.command.recipe_id, result.command.stage)
        self._results[key].append(result)

    def run(
        self,
        command: CompiledCommand,
        run_workspace: Path,
        *,
        timeout_seconds: float | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> SandboxResult:
        self.calls.append((command, run_workspace))
        if cancel_requested is not None and cancel_requested():
            return _cancelled_result(command, time.monotonic())
        key = (command.recipe_id, command.stage)
        if not self._results[key]:
            raise LookupError(f"no fake result queued for {key}")
        result = self._results[key].popleft()
        if result.command != command:
            raise ValueError("queued fake result does not match compiled command")
        if cancel_requested is not None and cancel_requested():
            return _cancelled_result(command, time.monotonic())
        return result


def _cancelled_result(command: CompiledCommand, started: float) -> SandboxResult:
    return SandboxResult(
        command=command,
        status=ExecutionStatus.CANCELLED,
        exit_code=None,
        timed_out=False,
        duration_ms=_duration_ms(started),
        failure_code=FailureCode.EXPERIMENT_CANCELLED,
    )


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _bounded_text(value: str | bytes | None, maximum: int) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    encoded = text.encode("utf-8")
    if len(encoded) <= maximum:
        return text
    suffix = "\n[output truncated]"
    budget = max(0, maximum - len(suffix.encode("utf-8")))
    return encoded[:budget].decode("utf-8", errors="ignore") + suffix
