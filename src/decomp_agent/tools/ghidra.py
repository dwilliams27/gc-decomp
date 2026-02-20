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
import re
import shutil
import struct
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from decomp_agent.config import Config

log = logging.getLogger(__name__)

# Regex to extract hex address from function names like "lbSnap_8001DF20" or "fn_80038700"
_FUNC_ADDR_RE = re.compile(r"_([0-9A-Fa-f]{8})$")


class _DOLAddressMap:
    """Maps GameCube virtual addresses to flat file offsets.

    When Ghidra imports a DOL without the GameCube Loader extension,
    it loads the file as a flat binary starting at address 0. The DOL
    header contains section tables that map file offsets to virtual
    addresses (0x8000xxxx). This class parses that header and provides
    the reverse mapping so we can look up functions by their virtual
    address in the flat-loaded project.
    """

    def __init__(self, dol_path: Path) -> None:
        with open(dol_path, "rb") as f:
            header = f.read(0xE4)

        if len(header) < 0xE4:
            raise RuntimeError(f"DOL file too small: {dol_path}")

        # Parse section tables: 7 text + 11 data = 18 sections
        text_offsets = struct.unpack(">7I", header[0x00:0x1C])
        data_offsets = struct.unpack(">11I", header[0x1C:0x48])
        text_vaddrs = struct.unpack(">7I", header[0x48:0x64])
        data_vaddrs = struct.unpack(">11I", header[0x64:0x90])
        text_sizes = struct.unpack(">7I", header[0x90:0xAC])
        data_sizes = struct.unpack(">11I", header[0xAC:0xD8])

        # Build list of (vaddr_start, vaddr_end, file_offset) for non-empty sections
        self._sections: list[tuple[int, int, int]] = []
        for off, va, sz in zip(text_offsets, text_vaddrs, text_sizes):
            if sz > 0:
                self._sections.append((va, va + sz, off))
        for off, va, sz in zip(data_offsets, data_vaddrs, data_sizes):
            if sz > 0:
                self._sections.append((va, va + sz, off))

    def vaddr_to_flat(self, vaddr: int) -> int | None:
        """Convert a virtual address to the flat file offset used by Ghidra.

        Returns None if the address doesn't fall within any DOL section.
        """
        for start, end, file_offset in self._sections:
            if start <= vaddr < end:
                return file_offset + (vaddr - start)
        return None

# Module-level singleton — JVM startup is expensive (~10-30s), so we
# initialize once and reuse across all function decompilations.
_session: _GhidraSession | None = None
_session_lock = threading.Lock()


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
        import os

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

        # Set GHIDRA_INSTALL_DIR for pyghidra if not already set
        if "GHIDRA_INSTALL_DIR" not in os.environ:
            install_dir = ghidra_cfg.install_dir
            if install_dir is None:
                # Try common Homebrew location
                homebrew_path = Path("/opt/homebrew/opt/ghidra/libexec")
                if homebrew_path.is_dir():
                    install_dir = homebrew_path
                else:
                    raise RuntimeError(
                        "GHIDRA_INSTALL_DIR not set and ghidra.install_dir "
                        "not configured. Set one of them to the Ghidra "
                        "installation directory."
                    )
            os.environ["GHIDRA_INSTALL_DIR"] = str(install_dir)

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

        # Build DOL address map for vaddr → flat offset translation.
        # Without the GameCube Loader extension, Ghidra imports the DOL
        # as a flat binary at address 0, so virtual addresses (0x8000xxxx)
        # need to be translated to flat file offsets.
        dol_path = ghidra_cfg.dol_path
        if dol_path is None:
            dol_path = (
                config.melee.repo_path
                / "orig"
                / config.melee.version
                / "sys"
                / "main.dol"
            )
        if dol_path.exists():
            self._addr_map = _DOLAddressMap(dol_path)
            log.info("DOL address map loaded from %s", dol_path)
        else:
            self._addr_map = None
            log.warning(
                "DOL file not found at %s — virtual address lookup disabled",
                dol_path,
            )

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
        """Look up a Ghidra Function by entry-point virtual address.

        Handles the address translation needed when the DOL was imported
        without the GameCube Loader (flat binary at address 0). Tries:
        1. Direct lookup at the given address (works if GameCube Loader was used)
        2. Translated flat offset via DOL address map
        """
        addr_space = self._program.getAddressFactory().getDefaultAddressSpace()

        # Try direct lookup first (works if DOL was loaded with correct base)
        addr = addr_space.getAddress(address)
        func = self._program.getFunctionManager().getFunctionAt(addr)
        if func is not None:
            return func

        # Translate virtual address to flat file offset
        if self._addr_map is not None:
            flat = self._addr_map.vaddr_to_flat(address)
            if flat is not None:
                addr = addr_space.getAddress(flat)
                return self._program.getFunctionManager().getFunctionAt(addr)

        return None

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


def _extract_address(function_name: str) -> int | None:
    """Extract the hex address from a function name like 'lbSnap_8001DF20'.

    Returns the address as an integer, or None if no address found.
    """
    m = _FUNC_ADDR_RE.search(function_name)
    if m is None:
        return None
    return int(m.group(1), 16)


def _get_session(config: Config) -> _GhidraSession:
    """Get or create the module-level Ghidra session singleton."""
    global _session
    with _session_lock:
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

    # Fallback: extract address from function name (e.g. "lbSnap_8001DF20")
    # and look up by address. Ghidra projects without symbol imports only
    # have auto-generated names like "FUN_8001df20".
    if func is None:
        addr = _extract_address(function_name)
        if addr is not None:
            func = session.get_function_by_address(addr)

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
        homebrew_path = Path("/opt/homebrew/opt/ghidra/libexec/support/analyzeHeadless")
        if homebrew_path.exists():
            analyze = str(homebrew_path)
        else:
            log.error("analyzeHeadless not found on PATH")
            return False

    # Determine DOL path
    dol_path = ghidra_cfg.dol_path
    if dol_path is None:
        dol_path = config.melee.repo_path / "orig" / config.melee.version / "sys" / "main.dol"
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
        "-processor", "PowerPC:BE:32:default",
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
