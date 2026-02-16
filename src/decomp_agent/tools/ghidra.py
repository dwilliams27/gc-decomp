"""Ghidra headless decompilation via pyghidra.

Provides type-aware decompilation complementary to m2c:
- m2c produces matching-oriented C (good starting template)
- Ghidra produces semantically rich C (struct access, types, control flow)

The LLM agent uses both: Ghidra to understand *what* a function does,
m2c to understand *how* to write it for CodeWarrior matching.

Setup (one-time):
    1. pip install pyghidra
    2. Install Ghidra-GameCube-Loader + ghidra-gekko-broadway-lang extensions
    3. Run: decomp-agent ghidra-setup (or manually analyzeHeadless)
    4. Set ghidra.enabled = true in config
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from decomp_agent.config import Config

log = logging.getLogger(__name__)

# Module-level singleton — JVM startup is expensive (~10-30s), so we
# initialize once and reuse across all function decompilations.
_session: _GhidraSession | None = None


@dataclass
class GhidraResult:
    """Result of a Ghidra decompilation."""

    function_name: str
    c_code: str | None = None
    signature: str | None = None
    return_type: str | None = None
    parameters: list[dict[str, Any]] = field(default_factory=list)
    local_vars: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.c_code is not None

    def format_for_llm(self) -> str:
        """Format the result as context for the LLM."""
        if not self.success:
            return f"Ghidra decompilation unavailable: {self.error}"

        parts = [f"=== Ghidra decompilation of {self.function_name} ==="]
        if self.signature:
            parts.append(f"Signature: {self.signature}")
        parts.append("")
        parts.append(self.c_code or "")
        return "\n".join(parts)


class _GhidraSession:
    """Manages a persistent pyghidra session with the Melee DOL.

    The JVM and Ghidra project are opened once and kept alive for the
    duration of the process. Individual function decompilations are fast
    (~1-2s) since analysis is already done.
    """

    def __init__(self, config: Config) -> None:
        ghidra_cfg = config.ghidra

        if ghidra_cfg.project_path is None:
            raise RuntimeError(
                "ghidra.project_path not set in config. "
                "Run ghidra-setup or set it manually."
            )

        project_path = ghidra_cfg.project_path
        if not project_path.is_dir():
            raise RuntimeError(
                f"Ghidra project directory not found: {project_path}"
            )

        try:
            import pyghidra
        except ImportError:
            raise RuntimeError(
                "pyghidra not installed. Run: pip install pyghidra"
            )

        log.info("Starting Ghidra JVM (this takes ~10-30s on first call)...")
        pyghidra.start()

        self._project_cm = pyghidra.open_project(
            str(project_path), ghidra_cfg.project_name
        )
        self._project = self._project_cm.__enter__()

        self._program_cm = pyghidra.program_context(
            self._project, ghidra_cfg.program_path
        )
        self._program = self._program_cm.__enter__()

        # Import Ghidra Java classes (only available after pyghidra.start())
        from ghidra.app.decompiler import DecompInterface, DecompileOptions  # type: ignore[import]
        from ghidra.util.task import ConsoleTaskMonitor  # type: ignore[import]

        self._monitor = ConsoleTaskMonitor()
        self._ifc = DecompInterface()
        self._ifc.setOptions(DecompileOptions())
        self._ifc.openProgram(self._program)
        log.info("Ghidra session ready.")

    def get_function_by_name(self, name: str) -> Any:
        """Look up a Ghidra Function object by symbol name."""
        symbols = list(
            self._program.getSymbolTable().getGlobalSymbols(name)
        )
        if not symbols:
            return None
        addr = symbols[0].getAddress()
        return self._program.getFunctionManager().getFunctionAt(addr)

    def get_function_by_address(self, address: int) -> Any:
        """Look up a Ghidra Function object by entry-point address."""
        addr_space = self._program.getAddressFactory().getDefaultAddressSpace()
        addr = addr_space.getAddress(address)
        return self._program.getFunctionManager().getFunctionAt(addr)

    def decompile(self, func: Any) -> GhidraResult:
        """Decompile a Ghidra Function and return structured results."""
        name = func.getName()
        result = self._ifc.decompileFunction(func, 60, self._monitor)

        if not result.decompileCompleted():
            return GhidraResult(
                function_name=name,
                error=result.getErrorMessage() or "Decompilation failed",
            )

        c_code = result.getDecompiledFunction().getC()
        high_func = result.getHighFunction()

        # Extract parameter info from Ghidra's analysis
        params = []
        for p in func.getParameters():
            params.append({
                "name": p.getName(),
                "type": str(p.getDataType()),
                "size": p.getLength(),
            })

        # Extract local variables from the decompiler's high-level model
        local_vars = []
        if high_func:
            local_map = high_func.getLocalSymbolMap()
            for sym in local_map.getSymbols():
                if not sym.isParameter():
                    local_vars.append({
                        "name": sym.getName(),
                        "type": str(sym.getDataType()),
                    })

        return GhidraResult(
            function_name=name,
            c_code=c_code,
            signature=str(func.getSignature()),
            return_type=str(func.getReturnType()),
            parameters=params,
            local_vars=local_vars,
        )

    def close(self) -> None:
        """Shut down the Ghidra session."""
        self._ifc.dispose()
        self._program_cm.__exit__(None, None, None)
        self._project_cm.__exit__(None, None, None)


def _get_session(config: Config) -> _GhidraSession:
    """Get or create the module-level Ghidra session singleton."""
    global _session
    if _session is None:
        _session = _GhidraSession(config)
    return _session


def close_session() -> None:
    """Explicitly close the Ghidra session (e.g. at process exit)."""
    global _session
    if _session is not None:
        _session.close()
        _session = None


def get_ghidra_decompilation(
    function_name: str,
    config: Config,
) -> GhidraResult:
    """Get Ghidra's decompiled C code for a function by name.

    On first call, starts the JVM and opens the Ghidra project (~10-30s).
    Subsequent calls reuse the session and are fast (~1-2s per function).
    """
    if not config.ghidra.enabled:
        return GhidraResult(
            function_name=function_name,
            error="Ghidra not enabled. Set ghidra.enabled = true in config "
            "and ensure the project is set up.",
        )

    try:
        session = _get_session(config)
    except RuntimeError as e:
        return GhidraResult(function_name=function_name, error=str(e))

    func = session.get_function_by_name(function_name)
    if func is None:
        return GhidraResult(
            function_name=function_name,
            error=f"Function '{function_name}' not found in Ghidra project",
        )

    return session.decompile(func)


def get_ghidra_decompilation_by_address(
    address: int,
    config: Config,
) -> GhidraResult:
    """Get Ghidra's decompiled C code for a function at a specific address."""
    if not config.ghidra.enabled:
        return GhidraResult(
            function_name=hex(address),
            error="Ghidra not enabled.",
        )

    try:
        session = _get_session(config)
    except RuntimeError as e:
        return GhidraResult(function_name=hex(address), error=str(e))

    func = session.get_function_by_address(address)
    if func is None:
        return GhidraResult(
            function_name=hex(address),
            error=f"No function at address {hex(address)} in Ghidra project",
        )

    return session.decompile(func)


def setup_ghidra_project(config: Config) -> bool:
    """Create and analyze a Ghidra project for the Melee DOL.

    This is a one-time setup step. It runs analyzeHeadless to import
    the DOL and perform full analysis. Takes several minutes.

    Returns True if setup succeeded.
    """
    ghidra_cfg = config.ghidra

    # Find analyzeHeadless
    analyze = shutil.which("analyzeHeadless")
    if analyze is None:
        # Try common Homebrew location
        homebrew_path = Path("/opt/homebrew/opt/ghidra/lib/ghidra/support/analyzeHeadless")
        if homebrew_path.exists():
            analyze = str(homebrew_path)
        else:
            log.error("analyzeHeadless not found on PATH")
            return False

    # Determine DOL path
    dol_path = ghidra_cfg.dol_path
    if dol_path is None:
        dol_path = config.melee.repo_path / "orig" / config.melee.version / "main.dol"
    if not dol_path.exists():
        log.error("DOL file not found: %s", dol_path)
        return False

    # Create project directory
    project_path = ghidra_cfg.project_path
    if project_path is None:
        project_path = config.melee.repo_path / ".ghidra"
    project_path.mkdir(parents=True, exist_ok=True)

    cmd = [
        analyze,
        str(project_path),
        ghidra_cfg.project_name,
        "-import", str(dol_path),
        "-processor", "PowerPC:BE:32:Gekko_Broadway:default",
        "-overwrite",
    ]

    # Apply symbols if symbols.txt exists
    symbols = config.melee.symbols_path
    if symbols.exists():
        log.info("Will apply symbols from %s after analysis", symbols)

    log.info("Running analyzeHeadless (this may take several minutes)...")
    log.info("Command: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
        )
    except subprocess.TimeoutExpired:
        log.error("analyzeHeadless timed out")
        return False
    except FileNotFoundError:
        log.error("analyzeHeadless not found")
        return False

    if result.returncode != 0:
        log.error("analyzeHeadless failed:\n%s\n%s", result.stdout, result.stderr)
        return False

    log.info("Ghidra project created at %s", project_path)
    return True
