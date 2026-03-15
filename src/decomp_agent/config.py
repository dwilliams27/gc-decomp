from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]
from pathlib import Path

from pydantic import BaseModel, model_validator

from decomp_agent.cost import PricingConfig


class MeleeConfig(BaseModel):
    repo_path: Path
    version: str = "GALE01"
    build_dir: str = "build"

    # Source file prefixes that live outside src/.
    # Maps prefix -> directory relative to repo_path.
    _SOURCE_ROOTS: dict[str, str] = {
        "dolphin/": "extern/dolphin/src",
    }

    @property
    def src_dir(self) -> Path:
        return self.repo_path / "src"

    def resolve_source_path(self, source_file: str) -> Path:
        """Resolve an object name to its actual filesystem path.

        Most source files live under src/ (e.g. "melee/lb/foo.c" -> src/melee/lb/foo.c).
        Dolphin SDK files live under extern/dolphin/src/ instead.
        """
        for prefix, root in self._SOURCE_ROOTS.items():
            if source_file.startswith(prefix):
                return self.repo_path / root / source_file
        return self.repo_path / "src" / source_file

    @property
    def build_path(self) -> Path:
        return self.repo_path / self.build_dir / self.version

    @property
    def report_path(self) -> Path:
        return self.build_path / "report.json"

    @property
    def configure_py(self) -> Path:
        return self.repo_path / "configure.py"

    @property
    def symbols_path(self) -> Path:
        return self.repo_path / "config" / self.version / "symbols.txt"

    @property
    def splits_path(self) -> Path:
        return self.repo_path / "config" / self.version / "splits.txt"

    @property
    def objdiff_json(self) -> Path:
        return self.repo_path / "objdiff.json"

    @model_validator(mode="after")
    def validate_repo_exists(self) -> MeleeConfig:
        if not self.repo_path.is_dir():
            raise ValueError(f"Melee repo not found: {self.repo_path}")
        if not self.configure_py.is_file():
            raise ValueError(f"configure.py not found: {self.configure_py}")
        return self


class AgentConfig(BaseModel):
    model: str = "gpt-5.2-codex"
    max_iterations: int = 30
    max_tokens_per_attempt: int = 2_000_000
    max_ctx_chars: int = 20_000


class DockerConfig(BaseModel):
    enabled: bool = False
    container_name: str = "melee-build"
    image: str = "ghcr.io/doldecomp/build-melee:main"


class GhidraConfig(BaseModel):
    enabled: bool = False
    install_dir: Path | None = None  # Ghidra installation directory
    project_path: Path | None = None  # Directory containing the .gpr file
    project_name: str = "MeleeProject"
    program_path: str = "/main.dol"  # Path within the Ghidra project
    dol_path: Path | None = None  # Path to original DOL for initial import


class ClaudeCodeConfig(BaseModel):
    enabled: bool = False
    container_name: str = "docker-worker-1"
    timeout_seconds: int = 3600  # 60 min per cold-start function attempt
    max_turns: int = 50
    warm_start_turns: int = 80
    near_match_turns: int = 150
    warm_start_timeout_seconds: int = 3600
    near_match_timeout_seconds: int = 5400
    warm_start_threshold_pct: float = 80.0
    near_match_threshold_pct: float = 95.0
    file_mode_max_turns: int = 150
    file_mode_timeout_seconds: int = 7200
    orchestrator_max_turns: int = 30
    orchestrator_timeout_seconds: int = 1800
    isolated_worker_enabled: bool = False
    worker_root: Path = Path("/tmp/decomp-claude-workers")
    image: str = "decomp-agent-worker:latest"


class CodexCodeConfig(BaseModel):
    enabled: bool = False
    container_name: str = "docker-worker-1"
    timeout_seconds: int = 1800  # 30 min per function attempt
    isolated_worker_enabled: bool = False
    worker_root: Path = Path("/tmp/decomp-codex-workers")
    auth_file: Path | None = None
    image: str = "decomp-agent-worker:latest"
    http_proxy: str | None = None
    https_proxy: str | None = None


class OrchestrationConfig(BaseModel):
    db_path: Path = Path("decomp.db")
    max_function_size: int | None = None
    batch_size: int = 50
    default_workers: int = 1
    default_budget: float | None = None
    max_attempts_per_function: int = 10


class CampaignConfig(BaseModel):
    orchestrator_provider: str = "claude"
    worker_provider_policy: str = "claude"
    max_active_workers: int = 4
    timeout_hours: int = 8
    max_no_progress_cycles: int = 6
    baseline_compile_retries: int = 1
    rate_limit_backoff_seconds: int = 300
    rate_limit_reset_hours: int = 5
    orchestrator_poll_seconds: int = 30
    manager_wake_cooldown_seconds: int = 45
    worker_stall_seconds: int = 900
    root_dir: Path = Path("/tmp/decomp-campaigns")
    allow_shared_fix_workers: bool = False
    allow_temporary_unmatched_regressions: bool = False


class LoggingConfig(BaseModel):
    level: str = "INFO"
    log_file: Path | None = None
    json_format: bool = False


class Config(BaseModel):
    melee: MeleeConfig
    agent: AgentConfig = AgentConfig()
    docker: DockerConfig = DockerConfig()
    ghidra: GhidraConfig = GhidraConfig()
    claude_code: ClaudeCodeConfig = ClaudeCodeConfig()
    codex_code: CodexCodeConfig = CodexCodeConfig()
    orchestration: OrchestrationConfig = OrchestrationConfig()
    campaign: CampaignConfig = CampaignConfig()
    logging: LoggingConfig = LoggingConfig()
    pricing: PricingConfig = PricingConfig()


DEFAULT_CONFIG_PATH = Path(__file__).parents[2] / "config" / "default.toml"


def load_config(path: Path | None = None) -> Config:
    config_path = path or DEFAULT_CONFIG_PATH
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    return Config(**data)
