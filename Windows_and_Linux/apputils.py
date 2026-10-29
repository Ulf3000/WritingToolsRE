"""
Shared utilities for Writing Tools.

Kept deliberately dependency-free (standard library only) so it can be used
from every module — including early-startup code before Qt exists.

Responsibilities:
  * Locate writable app/data/resource paths (source vs. PyInstaller onefile).
  * Atomic JSON read/write so a crash can never leave a half-written config.
  * Logging + crash hooks (file logging even when --windowed has no console).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
from logging.handlers import RotatingFileHandler

_APP_NAME = "WritingTools"
_LOG_FILE_HANDLE = None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def get_app_dir() -> str:
    """
    Directory that contains the executable (compiled) or main.py (source).

    This is where user-writable files (config.json, options.json, logs) live.
    """
    argv0 = sys.argv[0] if sys.argv and sys.argv[0] else ""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    if argv0 and os.path.exists(argv0):
        return os.path.dirname(os.path.abspath(argv0))
    if getattr(sys.modules.get("__main__"), "__file__", None):
        return os.path.dirname(os.path.abspath(sys.modules["__main__"].__file__))
    return os.getcwd()


def _bundle_dir() -> str | None:
    """PyInstaller's temporary extraction directory, if running onefile."""
    return getattr(sys, "_MEIPASS", None)


def app_path(relative: str) -> str:
    """Path next to the executable, regardless of whether it exists."""
    return os.path.join(get_app_dir(), relative)


def resource_path(relative: str) -> str:
    """
    Resolve a read-only resource (icons, backgrounds, locales).

    Preference order:
      1. A user-replaced file next to the exe (easy customisation).
      2. The bundled copy inside a onefile build.
      3. The path next to the exe (so callers can report a sensible name).
    """
    user_path = app_path(relative)
    if os.path.exists(user_path):
        return user_path
    bundle = _bundle_dir()
    if bundle:
        bundled = os.path.join(bundle, relative)
        if os.path.exists(bundled):
            return bundled
    return user_path


def data_path(relative: str) -> str:
    """
    Resolve a user-editable data file (options.json).

    Unlike resources, a file next to the exe always wins so edits stick.
    """
    user_path = app_path(relative)
    if os.path.exists(user_path):
        return user_path
    bundle = _bundle_dir()
    if bundle:
        bundled = os.path.join(bundle, relative)
        if os.path.exists(bundled):
            return bundled
    return user_path


def writable_dir() -> str:
    """Return a directory we can definitely write logs to."""
    candidates = [get_app_dir()]
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(os.path.join(local_appdata, _APP_NAME))
    home = os.path.expanduser("~")
    if home:
        candidates.append(os.path.join(home, "." + _APP_NAME.lower()))

    for directory in candidates:
        try:
            os.makedirs(directory, exist_ok=True)
            probe = os.path.join(directory, ".write-test")
            with open(probe, "w", encoding="utf-8") as handle:
                handle.write("ok")
            os.remove(probe)
            return directory
        except OSError:
            continue
    return tempfile.gettempdir()


# ---------------------------------------------------------------------------
# JSON I/O
# ---------------------------------------------------------------------------

def read_json(path: str):
    """Read JSON, returning None on missing/corrupt files (never raises)."""
    try:
        # utf-8-sig silently accepts a BOM. Windows Notepad and some editors
        # add one; treating that as corruption would dump the user back into
        # onboarding and discard their provider settings.
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        logging.error("Could not read JSON file %s: %s", path, exc)
        return None


def atomic_write_json(path: str, data, indent: int = 4) -> bool:
    """
    Write JSON atomically (temp file + os.replace).

    A power loss or crash mid-write can no longer truncate config.json.
    Returns True on success.
    """
    directory = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    try:
        os.makedirs(directory, exist_ok=True)
        # NamedTemporaryFile gives a unique file in the same directory, so
        # os.replace stays atomic (same volume).
        fd, temp_path = tempfile.mkstemp(
            prefix=os.path.basename(path) + ".",
            suffix=".tmp",
            dir=directory,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=indent, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            return True
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
    except OSError as exc:
        logging.error("Could not write JSON file %s: %s", path, exc)
        return False


def backup_corrupt_file(path: str) -> str | None:
    """Move a corrupt file aside so the user keeps a copy for debugging."""
    if not os.path.exists(path):
        return None
    import time as _time

    backup = f"{path}.corrupt-{int(_time.time())}"
    try:
        os.replace(path, backup)
        logging.warning("Moved corrupt file to %s", backup)
        return backup
    except OSError as exc:
        logging.error("Could not back up corrupt file %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Logging + crash reporting
# ---------------------------------------------------------------------------

def setup_logging() -> str:
    """
    Configure rotating file logging plus console logging when available.

    Returns the log file path. Safe to call multiple times.
    """
    global _LOG_FILE_HANDLE

    log_path = os.path.join(writable_dir(), "writingtools.log")
    root = logging.getLogger()
    if getattr(root, "_writingtools_configured", False):
        return log_path

    root.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )

    try:
        file_handler = RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        root.addHandler(file_handler)
    except OSError as exc:
        print(f"Writing Tools: could not open log file: {exc}", file=sys.stderr)

    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.setLevel(logging.INFO)
        root.addHandler(console)

    root._writingtools_configured = True  # type: ignore[attr-defined]
    logging.info("Logging started: %s", log_path)
    return log_path


def enable_faulthandler(log_path: str | None = None) -> None:
    """
    Enable faulthandler so native crashes (Qt/OpenGL) leave a traceback.

    This is what turns "it just disappears" into an actionable log entry.
    """
    try:
        import faulthandler

        if log_path:
            handle = open(log_path, "a", encoding="utf-8", buffering=1)
            faulthandler.enable(file=handle, all_threads=True)
        else:
            faulthandler.enable(all_threads=True)
    except Exception as exc:  # pragma: no cover - platform dependent
        logging.warning("Could not enable faulthandler: %s", exc)


def install_exception_hooks() -> None:
    """Log uncaught exceptions from the main thread and worker threads."""
    original_hook = sys.excepthook

    def _main_hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            original_hook(exc_type, exc_value, exc_tb)
            return
        logging.critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )
        # Keep the default behaviour for source runs / debugging.
        if sys.stderr is not None:
            original_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _main_hook

    def _thread_hook(args):
        logging.critical(
            "Uncaught exception in thread %s",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_hook


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def normalize_base_url(value: str) -> str:
    """Trim whitespace/trailing slash and strip a pasted /chat/completions."""
    value = (value or "").strip().rstrip("/")
    suffix = "/chat/completions"
    if value.lower().endswith(suffix):
        value = value[: -len(suffix)].rstrip("/")
    return value


def clean_model_text(text) -> str:
    """Normalise a model response into a plain string."""
    if text is None:
        return ""
    if isinstance(text, str):
        return text.rstrip("\n")
    if isinstance(text, list):
        parts = []
        for item in text:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "".join(parts).rstrip("\n")
    return str(text).rstrip("\n")
