"""Strict local contracts for sandbox compilation and execution."""

from enum import Enum

from pydantic import Field, StringConstraints, model_validator
from typing_extensions import Annotated, Self

from paper_agent.techscout.errors import FailureCode, StableId
from paper_agent.techscout.models import NonEmptyStr, TechScoutModel


ImageRef = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
DEFAULT_SANDBOX_IMAGE: ImageRef = "momo-techscout-sandbox:wave1"
MemoryLimit = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*[kmg]$")]
DockerNetwork = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"),
]
ApprovedHost = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$"),
]


class PocStage(str, Enum):
    INSTALL = "install"
    TEST = "test"


class NetworkAccess(str, Enum):
    NONE = "none"
    INSTALL_ONLY = "install_only"


class ExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"


class CompilationDisposition(str, Enum):
    EXECUTABLE = "executable"
    RESEARCH_ONLY = "research_only"


class SandboxLimits(TechScoutModel):
    cpus: float = Field(default=1.0, gt=0, le=2)
    memory: MemoryLimit = "512m"
    pids: int = Field(default=64, ge=16, le=128)
    disk: MemoryLimit = "256m"
    tmpfs: MemoryLimit = "64m"
    timeout_seconds: float = Field(default=60.0, gt=0, le=120)
    output_bytes: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)


class InstallNetworkPolicy(TechScoutModel):
    """An externally enforced Docker egress network approved for package install."""

    docker_network: DockerNetwork
    allowed_destinations: tuple[ApprovedHost, ...] = Field(min_length=1)
    egress_allowlist_enforced: bool

    @model_validator(mode="after")
    def require_wave1_allowlist(self) -> Self:
        if self.docker_network.casefold() in {"bridge", "host", "default", "none"}:
            raise ValueError("install network must be a dedicated allowlisted network")
        approved = {"pypi.org", "files.pythonhosted.org"}
        if not set(self.allowed_destinations).issubset(approved):
            raise ValueError("install destination is not approved for Wave 1")
        if not self.egress_allowlist_enforced:
            raise ValueError("install network must enforce its destination allowlist")
        return self


class CompiledCommand(TechScoutModel):
    poc_plan_id: StableId
    candidate_id: StableId
    recipe_id: StableId
    stage: PocStage
    argv: tuple[NonEmptyStr, ...] = Field(min_length=1)
    image: ImageRef
    network_access: NetworkAccess

    @model_validator(mode="after")
    def network_is_install_only(self) -> Self:
        if self.stage is PocStage.TEST and self.network_access is not NetworkAccess.NONE:
            raise ValueError("test execution must not have network access")
        return self


class CompilationResult(TechScoutModel):
    disposition: CompilationDisposition
    command: CompiledCommand | None = None
    failure_code: FailureCode | None = None
    reason: NonEmptyStr

    @model_validator(mode="after")
    def disposition_is_consistent(self) -> Self:
        if self.disposition is CompilationDisposition.EXECUTABLE:
            if self.command is None or self.failure_code is not None:
                raise ValueError("executable compilation requires only a command")
        elif self.command is not None or self.failure_code is not FailureCode.POC_RECIPE_UNSUPPORTED:
            raise ValueError("research-only compilation requires unsupported recipe failure")
        return self


class SandboxResult(TechScoutModel):
    command: CompiledCommand
    status: ExecutionStatus
    exit_code: int | None = None
    timed_out: bool
    duration_ms: int = Field(ge=0)
    stdout: str = ""
    stderr: str = ""
    failure_code: FailureCode | None = None

    @model_validator(mode="after")
    def status_is_consistent(self) -> Self:
        if self.status is ExecutionStatus.SUCCEEDED:
            if self.exit_code != 0 or self.timed_out or self.failure_code is not None:
                raise ValueError("successful execution requires exit code zero")
        elif self.status is ExecutionStatus.TIMED_OUT:
            if not self.timed_out or self.failure_code is not FailureCode.POC_TIMEOUT:
                raise ValueError("timed out execution requires poc_timeout")
        elif self.status is ExecutionStatus.CANCELLED:
            if self.timed_out or self.failure_code is not FailureCode.EXPERIMENT_CANCELLED:
                raise ValueError("cancelled execution requires experiment_cancelled")
        elif self.failure_code is None:
            raise ValueError("unsuccessful execution requires a failure code")
        return self
