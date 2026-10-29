import ctypes
import logging
import os
import sys
import time

from apputils import (
    app_path,
    enable_faulthandler,
    get_app_dir,
    install_exception_hooks,
    read_json,
    setup_logging,
    writable_dir,
)

# Keep the mutex/lock handle alive for the lifetime of the process.
_SINGLE_INSTANCE_HANDLE = None
_SINGLE_INSTANCE_FILE = None


def _acquire_single_instance() -> bool:
    """
    Prevent two copies of Writing Tools from running at once.

    Two instances used to fight over the global hotkey, the clipboard and
    config.json — which showed up as seemingly random crashes/pastes. Returns
    False if another instance already owns the lock.
    """
    global _SINGLE_INSTANCE_HANDLE, _SINGLE_INSTANCE_FILE

    if sys.platform.startswith("win"):
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.CreateMutexW.argtypes = [
                ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p
            ]
            handle = kernel32.CreateMutexW(
                None, False, "Local\\WritingTools_SingleInstance"
            )
            if not handle:
                # If the mutex API itself fails, don't block the user.
                return True
            _SINGLE_INSTANCE_HANDLE = handle
            # ERROR_ALREADY_EXISTS == 183
            if ctypes.get_last_error() == 183:
                kernel32.CloseHandle(handle)
                _SINGLE_INSTANCE_HANDLE = None
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            logging.warning("Single-instance check failed: %s", exc)
            return True

    # Best-effort POSIX file lock.
    try:
        import fcntl

        path = os.path.join(writable_dir(), "writingtools.lock")
        _SINGLE_INSTANCE_FILE = open(path, "w", encoding="utf-8")
        fcntl.flock(_SINGLE_INSTANCE_FILE, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _SINGLE_INSTANCE_FILE.write(str(os.getpid()))
        _SINGLE_INSTANCE_FILE.flush()
        return True
    except (ImportError, OSError) as exc:
        logging.debug("POSIX single-instance lock unavailable: %s", exc)
        return True


def _resolved_ui_scale() -> float:
    """
    Read the saved UI scale without creating a QApplication.

    Qt only reads QT_SCALE_FACTOR when QApplication is constructed, so this
    has to happen in main() before WritingToolApp exists. Missing config.json
    (first launch) still gets the default so new users see the smaller layout.
    """
    from defaults import DEFAULT_UI_SCALE, clamp_ui_scale

    try:
        config = read_json(app_path("config.json"))
    except Exception as exc:  # noqa: BLE001 - never block startup
        logging.debug("Could not read UI scale from config: %s", exc)
        return DEFAULT_UI_SCALE
    if not isinstance(config, dict):
        return DEFAULT_UI_SCALE
    return clamp_ui_scale(config.get("ui_scale", DEFAULT_UI_SCALE))


def _apply_ui_scale():
    """Expose the configured scale to Qt before QApplication is created."""
    try:
        scale = _resolved_ui_scale()
        if abs(scale - 1.0) < 0.001:
            # 100% is Qt's natural scale — leave the environment untouched.
            os.environ.pop("QT_SCALE_FACTOR", None)
            return
        os.environ["QT_SCALE_FACTOR"] = f"{scale:.4f}".rstrip("0").rstrip(".")
        logging.info("UI scale set to %.0f%%", scale * 100)
    except Exception as exc:  # noqa: BLE001 - scaling is cosmetic
        logging.warning("Could not apply UI scale: %s", exc)


def _notify_already_running():
    message = (
        "Writing Tools is already running.\n\n"
        "Look for its icon in the system tray, or close the existing copy "
        "before starting a new one."
    )
    if sys.platform.startswith("win"):
        try:
            ctypes.windll.user32.MessageBoxW(
                None, message, "Writing Tools", 0x40  # MB_ICONINFORMATION
            )
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def _acquire_single_instance_with_retry() -> bool:
    """
    Take the single-instance lock, waiting briefly after a self-relaunch.

    During a relaunch the previous process may still be shutting down and
    holding the mutex, so retry instead of showing the "already running"
    message. A normal launch fails fast.
    """
    attempts = 100 if os.environ.pop("WT_RELAUNCH", "") == "1" else 1
    for attempt in range(attempts):
        if _acquire_single_instance():
            return True
        if attempt < attempts - 1:
            time.sleep(0.1)
    return False


def main():
    """The main entry point of the application."""
    log_path = setup_logging()
    enable_faulthandler(log_path)
    install_exception_hooks()
    logging.info("Starting Writing Tools from %s", get_app_dir())

    # Must happen before QApplication is created (inside WritingToolApp).
    _apply_ui_scale()

    # When the app relaunches itself (e.g. after a UI-scale change), the old
    # process may still hold the mutex for a moment. Wait instead of showing
    # the "already running" message.
    if not _acquire_single_instance_with_retry():
        logging.warning("Another Writing Tools instance is already running")
        _notify_already_running()
        return 0

    from WritingToolApp import WritingToolApp

    app = None
    try:
        # WritingToolApp applies the Fusion style, palette and stylesheet
        # itself before creating any window.
        app = WritingToolApp(sys.argv)
        app.setApplicationName("Writing Tools")
        app.setApplicationDisplayName("Writing Tools")
        app.setQuitOnLastWindowClosed(False)
        return app.exec()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        logging.critical("Fatal startup/runtime error", exc_info=True)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None,
                    "Writing Tools – Unexpected Error",
                    "Writing Tools hit an unexpected error:\n\n"
                    f"{str(exc)[:800]}\n\n"
                    f"Details were written to:\n{log_path}",
                )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
