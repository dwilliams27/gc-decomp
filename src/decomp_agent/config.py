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
    max_tokens_per_attempt: int = 100_000
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


class OrchestrationConfig(BaseModel):
    db_path: Path = Path("decomp.db")
    max_function_size: int | None = None
    batch_size: int = 50
    default_workers: int = 1
    default_budget: float | None = None


class LoggingConfig(BaseModel):
    level: str = "INFO"
    log_file: Path | None = None
    json_format: bool = False


class Config(BaseModel):
    melee: MeleeConfig
    agent: AgentConfig = AgentConfig()
    docker: DockerConfig = DockerConfig()
    ghidra: GhidraConfig = GhidraConfig()
    orchestration: OrchestrationConfig = OrchestrationConfig()
    logging: LoggingConfig = LoggingConfig()
    pricing: PricingConfig = PricingConfig()


DEFAULT_CONFIG_PATH = Path(__file__).parents[2] / "config" / "default.toml"


def load_config(path: Path | None = None) -> Config:
    config_path = path or DEFAULT_CONFIG_PATH
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    return Config(**data)
