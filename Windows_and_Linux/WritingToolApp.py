import gettext
import logging
import os
import signal
import subprocess
import sys
import threading
import time

import pyperclip
import ui.AboutWindow
import ui.CustomPopupWindow
import ui.ResponseWindow
import ui.SettingsWindow
from aiprovider import create_providers, obfuscate_api_key
from apputils import (
    app_path,
    atomic_write_json,
    backup_corrupt_file,
    data_path,
    get_app_dir,
    read_json,
    resource_path,
)
from defaults import get_default_options
from pynput import keyboard as pykeyboard
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QLocale, Signal, Slot
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox
from ui.UIUtils import UIUtils

_ = gettext.gettext


class _SelectedTextHolder:
    """
    Carries the result of an async clipboard capture from `_show_popup` to
    `process_option_thread`. The capture thread sets `text` and signals
    `ready` once done.
    """

    __slots__ = ("text", "ready")

    def __init__(self):
        self.text = ""
        self.ready = threading.Event()


class WritingToolApp(QtWidgets.QApplication):
    """The main application class for Writing Tools."""

    output_ready_signal = Signal(str)
    show_message_signal = Signal(str, str)  # title, message
    hotkey_triggered_signal = Signal()

    # Minimum time between two hotkey activations. Prevents key-repeat from
    # opening/closing the popup dozens of times.
    HOTKEY_DEBOUNCE_SECONDS = 0.25

    def __init__(self, argv):
        super().__init__(argv)
        # Apply the shared theme before any window exists, so even the first
        # window gets the palette and stylesheet.
        UIUtils.apply_app_theme(self)
        self.current_response_window = None
        self._shutting_down = False
        self._config_lock = threading.RLock()
        self._last_hotkey_time = 0.0

        logging.debug("Initializing WritingToolApp")
        self.output_ready_signal.connect(self.replace_text)
        self.show_message_signal.connect(self.show_message_box)
        self.hotkey_triggered_signal.connect(self.on_hotkey_pressed)

        self.config = None
        self.config_path = app_path("config.json")
        self.load_config()

        # Run any pending config migrations in a single pass (single restart).
        self._migrate_config()

        self.options = None
        self.options_path = data_path("options.json")
        # Live-reload bookkeeping (see _setup_options_watcher). These must
        # exist before load_options/save_options are touched.
        self._options_watcher = None
        self._options_reload_timer = None
        self._options_poll_timer = None
        self._options_signature = None
        self._options_reload_attempts = 0
        self.load_options()
        self._options_signature = self._options_file_signature(
            self.options_path
        )

        self.popup_window = None
        self.tray_icon = None
        self.tray_menu = None
        self.settings_window = None
        self.about_window = None
        self.registered_hotkey = None
        self.output_queue = ""
        self.last_replace = 0
        self.hotkey_listener = None
        self.paused = False
        self.toggle_action = None
        self.current_provider = None
        # The scale the running Qt process actually uses. main.py sets
        # QT_SCALE_FACTOR before QApplication exists; Settings compares
        # against this (not just the saved config) so "Later" does not hide
        # a pending restart.
        self.active_ui_scale = self._read_active_ui_scale()

        # Holder for the user's selected text. Populated asynchronously by a
        # background thread so the popup can show instantly.
        self.current_text_holder = None
        # Serializes the entire Ctrl+C + clipboard capture cycle. If a second
        # hotkey arrives while one is in flight we skip it rather than reading
        # a stale clipboard (which used to paste random text into documents).
        self._capture_lock = threading.Lock()

        self._ = gettext.gettext

        # Initialize the Ctrl+C/SIGINT listener/timer.
        self.ctrl_c_timer = None
        self.setup_ctrl_c_listener()

        # Set up all AI providers. A broken optional provider must never stop
        # the app from starting.
        self.providers = create_providers(self)

        # Watch options.json so instruction edits (from the popup or an
        # external editor) are picked up without restarting the app.
        self._setup_options_watcher()

        first_run = not self.config
        if first_run:
            # No setup wizard: start with defaults and open the normal
            # Settings window so the user can pick a provider and paste a key.
            logging.debug("No config found, starting with defaults")
            self.config = {}
        else:
            logging.debug("Config found, setting up hotkey and tray icon")
        self._init_configured_app()
        if first_run:
            self.show_settings()

        self.recent_triggers = []  # Track recent hotkey triggers
        self.TRIGGER_WINDOW = 1.5  # Time window in seconds
        self.MAX_TRIGGERS = 3  # Max allowed triggers in window

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _init_configured_app(self):
        provider_name = (self.config or {}).get("provider", "Gemini (Recommended)")
        self.current_provider = next(
            (p for p in self.providers if p.provider_name == provider_name),
            None,
        )
        if self.current_provider is None:
            logging.warning("Provider %s not found. Using the default provider.",
                            provider_name)
            self.current_provider = next(
                (p for p in self.providers
                 if p.provider_name == "Gemini (Recommended)"),
                self.providers[0],
            )

        # Always load using the resolved provider name, not the (possibly
        # stale) name from the config file.
        self.current_provider.load_config(
            (self.config.get("providers", {}) or {}).get(
                self.current_provider.provider_name, {}
            )
        )

        self.create_tray_icon()
        self.register_hotkey()

        lang = self.config.get("locale")
        self.change_language(lang)

    def setup_translations(self, lang=None):
        if not lang:
            lang = QLocale.system().name().split("_")[0]

        try:
            translation = gettext.translation(
                "messages",
                localedir=resource_path("locales"),
                languages=[lang],
            )
        except (FileNotFoundError, OSError):
            translation = gettext.NullTranslations()

        translation.install()
        self._ = translation.gettext
        ui.AboutWindow._ = self._
        ui.SettingsWindow._ = self._
        ui.ResponseWindow._ = self._
        ui.CustomPopupWindow._ = self._

    def retranslate_ui(self):
        self.update_tray_menu()

    def change_language(self, lang):
        self.setup_translations(lang)
        self.retranslate_ui()

        for widget in QApplication.topLevelWidgets():
            if widget != self and hasattr(widget, "retranslate_ui"):
                try:
                    widget.retranslate_ui()
                except Exception as exc:  # noqa: BLE001
                    logging.error("Could not retranslate %s: %s", widget, exc)

    # ------------------------------------------------------------------
    # Hotkey spam protection
    # ------------------------------------------------------------------

    def check_trigger_spam(self):
        """
        Return True if the hotkey has been pressed too often in a short time.

        Old versions *quit the whole app* when this happened, which users
        understandably experienced as a random crash. Now we simply ignore
        the extra presses.
        """
        current_time = time.monotonic()
        self.recent_triggers.append(current_time)
        self.recent_triggers = [
            t for t in self.recent_triggers
            if current_time - t <= self.TRIGGER_WINDOW
        ]
        return len(self.recent_triggers) >= self.MAX_TRIGGERS

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @staticmethod
    def _read_active_ui_scale():
        """The scale Qt was started with (1.0 when no factor is set)."""
        try:
            return float(os.environ.get("QT_SCALE_FACTOR", "1"))
        except (TypeError, ValueError):
            return 1.0

    def load_config(self):
        """Load config.json, tolerating missing or corrupt files."""
        self.config_path = app_path("config.json")
        logging.debug("Loading config from %s", self.config_path)
        try:
            raw = read_json(self.config_path)
        except Exception as exc:  # noqa: BLE001 - read_json already guards
            logging.error("Unexpected error reading config: %s", exc)
            raw = None

        if raw is None:
            if os.path.exists(self.config_path):
                backup_corrupt_file(self.config_path)
            self.config = None
            logging.debug("Config file not found or unreadable")
            return

        if not isinstance(raw, dict):
            logging.error("config.json is not a JSON object; ignoring it")
            backup_corrupt_file(self.config_path)
            self.config = None
            return

        self.config = raw
        logging.debug("Config loaded successfully")

    def _migrate_config(self):
        """
        One-shot config migration. Catches any user up to the current schema
        (v11) regardless of where they started — v7, v8, or already current —
        in a single pass, and continues startup without a restart.

        Each version is gated on its own `is_config_file_updated_for_v{N}`
        flag, so re-running this is a no-op once everything's caught up.
        """
        CURRENT_CONFIG_VERSION = 11
        NEW_DEFAULT_MODEL = "gemini-flash-latest"
        V8_TO_V9_MAP = {
            "gemma-3-27b-it": NEW_DEFAULT_MODEL,
            "gemma-3-4b-it": NEW_DEFAULT_MODEL,
            "gemini-flash-lite-latest": NEW_DEFAULT_MODEL,
        }
        RETIRED_GEMINI_MODELS = {
            "gemini-2.5-pro": NEW_DEFAULT_MODEL,
            "gemini-2.5-flash": NEW_DEFAULT_MODEL,
            "gemini-2.5-flash-lite": NEW_DEFAULT_MODEL,
            "gemini-2.5-flash-preview": NEW_DEFAULT_MODEL,
            "gemini-2.0-flash": NEW_DEFAULT_MODEL,
            "gemini-2.0-flash-lite": NEW_DEFAULT_MODEL,
        }
        # v10: providers retired these model IDs. Without this, an existing
        # config keeps requesting a dead model even after the defaults in
        # aiprovider.py are updated.
        V9_TO_V10_MODELS = {
            "OpenRouter (Free Models)": {
                "meta-llama/llama-3.3-70b-instruct:free":
                    "nvidia/nemotron-3.5-lightning:free",
                "deepseek/deepseek-r1:free":
                    "nvidia/nemotron-3.5-lightning:free",
                "deepseek/deepseek-chat-v3-0324:free":
                    "nvidia/nemotron-3.5-lightning:free",
                "google/gemini-2.0-flash-exp:free":
                    "google/gemma-4-31b-it:free",
                "qwen/qwen-2.5-72b-instruct:free":
                    "nvidia/nemotron-3.5-lightning:free",
                "mistralai/mistral-7b-instruct:free":
                    "google/gemma-4-31b-it:free",
                "meta-llama/llama-3.1-8b-instruct:free":
                    "nvidia/nemotron-3.5-lightning:free",
            },
            "Groq (Free Tier)": {
                "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
                "llama-3.1-8b-instant": "openai/gpt-oss-120b",
                "qwen/qwen3-32b": "qwen/qwen3.8-27b",
                "deepseek-r1-distill-llama-70b": "openai/gpt-oss-120b",
                "gemma2-9b-it": "openai/gpt-oss-20b",
            },
            "Cerebras (Free Tier)": {
                "llama-3.3-70b": "gpt-oss-120b",
                "llama3.1-8b": "gpt-oss-120b",
                "qwen-3-32b": "gpt-oss-120b",
                "deepseek-r1-distill-llama-70b": "gpt-oss-120b",
            },
            "Cohere (Free Trial)": {
                "command-r-plus-08-2024": "command-a-03-2025",
                "command-r-08-2024": "command-a-03-2025",
                "command-light": "command-a-03-2025",
            },
            "NVIDIA NIM (Free Credits)": {
                "meta/llama-3.3-70b-instruct":
                    "nvidia/nemotron-3.5-lightning-30b-a3b",
                "meta/llama-3.1-8b-instruct":
                    "nvidia/nemotron-3.5-lightning-30b-a3b",
                "deepseek-ai/deepseek-r1":
                    "nvidia/nemotron-3.5-lightning-30b-a3b",
                "mistralai/mistral-7b-instruct-v0.3":
                    "nvidia/nemotron-3.5-lightning-30b-a3b",
                "nvidia/llama-3.1-nemotron-70b-instruct":
                    "nvidia/nemotron-3.5-lightning-30b-a3b",
            },
            "SambaNova (Free Tier)": {
                "Meta-Llama-3.1-8B-Instruct": "Meta-Llama-3.3-70B-Instruct",
                "DeepSeek-R1-Distill-Llama-70B":
                    "Meta-Llama-3.3-70B-Instruct",
                "Qwen2.5-72B-Instruct": "Meta-Llama-3.3-70B-Instruct",
            },
            "Zhipu GLM (Free)": {
                "glm-4-flash": "glm-5.3-flash",
                "glm-4.5-flash": "glm-5.3-flash",
                "glm-4-flash-250414": "glm-5.3-flash",
                "glm-4v-flash": "glm-5.3-flash",
            },
        }

        if not self.config or not isinstance(self.config, dict):
            logging.debug("No config to migrate (new user)")
            return

        needs_v8 = not self.config.get("is_config_file_updated_for_v8", False)
        needs_v9 = not self.config.get("is_config_file_updated_for_v9", False)
        needs_v10 = not self.config.get("is_config_file_updated_for_v10", False)
        needs_v11 = not self.config.get("is_config_file_updated_for_v11", False)

        if not needs_v8 and not needs_v9 and not needs_v10 and not needs_v11:
            logging.debug("Config already up-to-date, no migration needed")
            return

        logging.info(
            "Running config migration "
            "(needs_v8=%s, needs_v9=%s, needs_v10=%s, needs_v11=%s)...",
            needs_v8, needs_v9, needs_v10, needs_v11,
        )
        config_changed = False
        providers = self.config.get("providers")
        if not isinstance(providers, dict):
            providers = {}
            self.config["providers"] = providers
        gemini_config = providers.get("Gemini (Recommended)")

        if isinstance(gemini_config, dict):
            old_model = gemini_config.get("model_name", "")

            if needs_v8:
                if old_model != NEW_DEFAULT_MODEL:
                    gemini_config["model_name"] = NEW_DEFAULT_MODEL
                    logging.info(
                        '[v8] Bumped Gemini model "%s" -> "%s"',
                        old_model, NEW_DEFAULT_MODEL,
                    )
                    config_changed = True

                api_key = gemini_config.get("api_key", "")
                if api_key:
                    new_key = obfuscate_api_key(api_key)
                    if new_key != api_key:
                        gemini_config["api_key"] = new_key
                        logging.info("[v8] Obfuscated plaintext Gemini API key")
                        config_changed = True

            elif needs_v9:
                new_model = V8_TO_V9_MAP.get(old_model)
                if new_model is not None and new_model != old_model:
                    gemini_config["model_name"] = new_model
                    logging.info(
                        '[v9] Bumped Gemini model "%s" -> "%s"',
                        old_model, new_model,
                    )
                    config_changed = True

        if needs_v10:
            # GitHub Models was retired on 30 July 2026 and replaced by
            # Together AI. Drop the dead entry so the provider list stays clean.
            if providers.pop("GitHub Models (Free)", None) is not None:
                logging.info("[v10] Removed retired GitHub Models provider")
                config_changed = True

            for provider_name, model_map in V9_TO_V10_MODELS.items():
                provider_config = providers.get(provider_name)
                if not isinstance(provider_config, dict):
                    continue
                key = "model_name" if "Gemini" in provider_name else "api_model"
                old_model = provider_config.get(key)
                new_model = model_map.get(old_model)
                if new_model and new_model != old_model:
                    provider_config[key] = new_model
                    logging.info(
                        '[v10] Bumped %s model "%s" -> "%s"',
                        provider_name, old_model, new_model,
                    )
                    config_changed = True

        if needs_v11:
            # Gemini 2.5 Flash/Flash-Lite were retired on 20 October 2026, and
            # Gemini 2.0 is long gone. Existing configs may also carry a cached
            # live list from before the retirement — prune those entries too so
            # Settings never shows a model the API no longer accepts.
            gemini_config = providers.get("Gemini (Recommended)")
            if isinstance(gemini_config, dict):
                old_model = str(gemini_config.get("model_name") or "")
                if old_model.startswith(("gemini-2.5", "gemini-2.0")):
                    gemini_config["model_name"] = NEW_DEFAULT_MODEL
                    logging.info(
                        '[v11] Bumped Gemini model "%s" -> "%s"',
                        old_model, NEW_DEFAULT_MODEL,
                    )
                    config_changed = True

                cached = gemini_config.get("model_options")
                if isinstance(cached, list):
                    def _is_retired(entry):
                        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                            return True
                        model_id = str(entry[1]).strip()
                        return (not model_id
                                or model_id.startswith(("gemini-2.5",
                                                        "gemini-2.0")))
                    filtered = [entry for entry in cached if not _is_retired(entry)]
                    if not filtered:
                        gemini_config.pop("model_options", None)
                        gemini_config.pop("model_specs", None)
                        logging.info("[v11] Cleared stale cached Gemini models")
                        config_changed = True
                    elif len(filtered) != len(cached):
                        gemini_config["model_options"] = filtered
                        specs = gemini_config.get("model_specs")
                        if isinstance(specs, dict):
                            allowed = {str(entry[1]) for entry in filtered}
                            gemini_config["model_specs"] = {
                                model_id: spec for model_id, spec in specs.items()
                                if model_id in allowed
                            }
                        logging.info("[v11] Pruned retired cached Gemini models")
                        config_changed = True

        for number in range(8, CURRENT_CONFIG_VERSION + 1):
            self.config[f"is_config_file_updated_for_v{number}"] = True

        self.save_config(self.config)
        logging.info("Config migration complete")

        if config_changed:
            # Use QMessageBox directly — signals aren't connected yet at this
            # point in __init__. The migrated config is already saved and
            # loaded, so startup continues normally: no restart required.
            QMessageBox.information(
                None,
                "Writing Tools Updated",
                "Writing Tools has updated your settings to the current "
                "format.\n\nNo restart is needed.",
            )

    def load_options(self):
        """Load options.json, falling back to built-in defaults if needed."""
        self.options_path = data_path("options.json")
        signature = self._options_file_signature(self.options_path)
        if (
            signature is not None
            and signature == self._options_signature
            and isinstance(self.options, dict)
            and self.options
        ):
            # File is untouched since the last load (usually our own save):
            # keep the in-memory copy instead of parsing it again.
            return
        logging.debug("Loading options from %s", self.options_path)
        raw = read_json(self.options_path)
        if not isinstance(raw, dict) or not raw:
            if raw is not None:
                backup_corrupt_file(self.options_path)
            logging.warning("Options missing or invalid; using built-in defaults")
            self.options = get_default_options()
            return

        # Drop malformed entries instead of letting them crash the popup.
        sanitized = {}
        for name, cfg in raw.items():
            if not isinstance(name, str) or not isinstance(cfg, dict):
                logging.warning("Ignoring malformed option %r", name)
                continue
            if not isinstance(cfg.get("prefix"), str) or not isinstance(
                cfg.get("instruction"), str
            ):
                logging.warning("Ignoring option %r with missing prefix/instruction", name)
                continue
            sanitized[name] = cfg

        if not sanitized:
            logging.warning("No valid options found; using built-in defaults")
            self.options = get_default_options()
            return
        self.options = sanitized

    def save_options(self, options: dict) -> bool:
        """Atomically save options.json next to the executable."""
        if not isinstance(options, dict):
            logging.error("Refusing to save non-dict options")
            return False
        path = app_path("options.json")
        success = atomic_write_json(path, options, indent=2)
        if success:
            self.options_path = path
            self.options = options
            self._options_signature = self._options_file_signature(path)
            self._refresh_options_watch()
        return success

    # ------------------------------------------------------------------
    # Live options reload ("hotload")
    # ------------------------------------------------------------------

    @staticmethod
    def _options_file_signature(path):
        """A cheap fingerprint used to ignore redundant watcher events."""
        try:
            stat = os.stat(path)
            return (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return None

    def _setup_options_watcher(self):
        """Reload options.json automatically when it changes on disk."""
        try:
            watcher = QtCore.QFileSystemWatcher(self)
            watcher.fileChanged.connect(self._on_options_file_changed)
            self._options_watcher = watcher

            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(250)
            timer.timeout.connect(self._reload_options_from_disk)
            self._options_reload_timer = timer

            # Fallback poll: some editors replace the file in a way that
            # silently drops the OS watch. A stat() every 2s is free and
            # keeps hot-reload reliable.
            poll = QtCore.QTimer(self)
            poll.setInterval(2000)
            poll.timeout.connect(self._poll_options_file)
            poll.start()
            self._options_poll_timer = poll

            self._refresh_options_watch()
        except Exception as exc:  # noqa: BLE001
            logging.warning("Could not watch options.json: %s", exc)

    def _refresh_options_watch(self):
        """(Re)attach the watch — atomic saves replace the file inode."""
        watcher = getattr(self, "_options_watcher", None)
        if watcher is None:
            return
        try:
            watched = set(watcher.files())
            for path in (self.options_path, app_path("options.json")):
                if path and os.path.exists(path) and path not in watched:
                    watcher.addPath(path)
        except Exception as exc:  # noqa: BLE001
            logging.debug("Could not refresh options watch: %s", exc)

    def _on_options_file_changed(self, _path=None):
        self._schedule_options_reload()

    def _schedule_options_reload(self):
        timer = getattr(self, "_options_reload_timer", None)
        if timer is not None:
            timer.start()
        else:
            self._reload_options_from_disk()

    def _options_reload_path(self):
        """The options file the app should be reading right now."""
        path = data_path("options.json")
        if os.path.exists(path):
            return path
        return getattr(self, "options_path", "") or path

    def _poll_options_file(self):
        """Cheap fallback for editors that bypass the OS file watch."""
        path = self._options_reload_path()
        signature = self._options_file_signature(path)
        if signature is not None and signature != self._options_signature:
            self._reload_options_from_disk()

    def _reload_options_from_disk(self):
        """Apply options.json edits made outside the app (no restart)."""
        path = self._options_reload_path()
        signature = self._options_file_signature(path)
        if signature is not None and signature == self._options_signature:
            # Nothing new (usually our own save); skip the extra work.
            self._refresh_options_watch()
            return

        raw = read_json(path)
        if not isinstance(raw, dict) or not raw:
            # Most likely a half-written file. Retry a few times instead of
            # replacing the user's buttons with defaults mid-save.
            attempts = getattr(self, "_options_reload_attempts", 0) + 1
            self._options_reload_attempts = attempts
            if attempts <= 10:
                self._schedule_options_reload()
            else:
                logging.warning(
                    "options.json stayed unreadable; keeping the current buttons"
                )
            return

        self._options_reload_attempts = 0
        try:
            self.apply_options_changes()
            self._options_signature = signature
            logging.info("options.json changed on disk; reloaded live")
        except Exception as exc:  # noqa: BLE001
            logging.error("Could not reload options.json: %s", exc)

    def apply_options_changes(self, refresh_popup: bool = True):
        """
        Reload options.json and re-register per-button hotkeys.

        Called after in-app edits (add/edit/delete/reset button) and by the
        file watcher, so changes take effect without a relaunch.
        """
        self.load_options()
        path = self._options_reload_path()
        self._options_signature = self._options_file_signature(path)
        if self.config:
            self.register_hotkey()
        self._refresh_options_watch()

        if not refresh_popup:
            return
        popup = getattr(self, "popup_window", None)
        if popup is not None:
            try:
                popup.refresh_from_options()
            except RuntimeError:
                # The C++ widget was already destroyed.
                self.popup_window = None
            except Exception as exc:  # noqa: BLE001
                logging.error("Could not refresh the popup: %s", exc)

    def save_config(self, config):
        """Atomically save config.json. Safe to call from worker threads."""
        if not isinstance(config, dict):
            logging.error("Refusing to save non-dict config")
            return
        with self._config_lock:
            if atomic_write_json(self.config_path, config, indent=4):
                self.config = config
                logging.debug("Config saved successfully")

    # ------------------------------------------------------------------
    # Windows
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Hotkey registration
    # ------------------------------------------------------------------

    @staticmethod
    def _to_pynput_hotkey(hotkey_str):
        """
        Convert a user-facing hotkey string ('ctrl+j', 'ctrl+alt+space') to
        pynput's `<ctrl>+j` / `<ctrl>+<alt>+<space>` format. Single-char keys
        stay as-is; multi-char keys (modifiers, named keys) get wrapped in <>.
        """
        return "+".join(
            f"{token}" if len(token) <= 1 else f"<{token}>"
            for token in (hotkey_str or "").strip().lower().split("+")
            if token
        )

    def start_hotkey_listener(self):
        """
        Build a single `GlobalHotKeys` listener that handles both the main
        Writing Tools shortcut AND any per-button direct hotkeys defined in
        options.json.
        """
        try:
            if self.hotkey_listener is not None:
                try:
                    self.hotkey_listener.stop()
                except Exception as exc:  # noqa: BLE001
                    logging.debug("Stopping old hotkey listener: %s", exc)
                finally:
                    self.hotkey_listener = None

            hotkey_map = {}
            config = self.config or {}

            # --- Global Writing Tools hotkey ---------------------------------
            orig_shortcut = (config.get("shortcut") or "ctrl+space").strip().lower()
            self.registered_hotkey = orig_shortcut
            try:
                global_parsed = self._to_pynput_hotkey(orig_shortcut)
                if global_parsed:
                    pykeyboard.HotKey.parse(global_parsed)

                    def on_global_activate():
                        if self.paused:
                            return
                        logging.debug("triggered global hotkey")
                        self.hotkey_triggered_signal.emit()

                    hotkey_map[global_parsed] = on_global_activate
                    logging.debug("Registered global hotkey: %s", global_parsed)
            except Exception as exc:  # noqa: BLE001
                logging.error('Failed to parse global hotkey "%s": %s',
                              orig_shortcut, exc)

            # --- Per-button direct hotkeys -----------------------------------
            # Custom is excluded — it needs a typed instruction from the user.
            for button_name, button_cfg in (self.options or {}).items():
                if button_name == "Custom" or not isinstance(button_cfg, dict):
                    continue
                raw = (button_cfg.get("hotkey") or "").strip().lower()
                if not raw:
                    continue
                try:
                    parsed = self._to_pynput_hotkey(raw)
                    pykeyboard.HotKey.parse(parsed)
                except Exception as exc:  # noqa: BLE001
                    logging.error('Invalid hotkey "%s" for button "%s": %s',
                                  raw, button_name, exc)
                    continue
                if parsed in hotkey_map:
                    logging.warning(
                        'Hotkey "%s" for button "%s" conflicts with an '
                        "already-registered binding; skipping",
                        raw, button_name,
                    )
                    continue
                hotkey_map[parsed] = self._make_button_hotkey_callback(button_name)
                logging.debug("Registered button hotkey: %s -> %s",
                              parsed, button_name)

            if not hotkey_map:
                logging.warning("No hotkeys to register")
                return

            self.hotkey_listener = pykeyboard.GlobalHotKeys(hotkey_map)
            self.hotkey_listener.start()
        except Exception as exc:  # noqa: BLE001
            logging.error("Failed to register hotkey listener: %s", exc,
                          exc_info=True)

    def _make_button_hotkey_callback(self, button_name):
        """
        Build a callback that fires `button_name` directly, bypassing the
        popup. The actual work is bounced through the Qt event loop because
        pynput invokes callbacks on its listener thread.
        """
        def callback():
            if self.paused:
                return
            logging.debug('Direct hotkey fired for button "%s"', button_name)
            if self.current_provider is not None:
                self.current_provider.cancel()
                self.output_queue = ""
            # noinspection PyTypeChecker
            QtCore.QMetaObject.invokeMethod(
                self,
                "_fire_button_directly",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, button_name),
            )
        return callback

    @Slot(str)
    def _fire_button_directly(self, button_name):
        """Run a button's option without showing the popup."""
        logging.debug('Firing button "%s" directly', button_name)

        if self.popup_window is not None and self.popup_window.isVisible():
            self.popup_window.close()

        if not self.options or button_name not in self.options:
            logging.warning('Button "%s" no longer exists; ignoring hotkey',
                            button_name)
            return

        holder = _SelectedTextHolder()
        self.current_text_holder = holder
        self._fire_ctrl_c_and_capture_async(holder)
        self.process_option(button_name, holder=holder)

    def register_hotkey(self):
        logging.debug("Registering hotkey")
        self.start_hotkey_listener()
        logging.debug("Hotkey registered")

    def on_hotkey_pressed(self):
        """Handle the hotkey press event."""
        logging.debug("Hotkey pressed")

        if self.paused:
            return

        now = time.monotonic()
        if now - self._last_hotkey_time < self.HOTKEY_DEBOUNCE_SECONDS:
            logging.debug("Ignoring hotkey repeat")
            return
        self._last_hotkey_time = now

        if self.check_trigger_spam():
            logging.warning("Hotkey spam detected - ignoring this press")
            return

        if self.current_provider is not None:
            logging.debug("Cancelling current provider's request")
            self.current_provider.cancel()
            self.output_queue = ""

        # noinspection PyTypeChecker
        QtCore.QMetaObject.invokeMethod(
            self, "_show_popup", QtCore.Qt.ConnectionType.QueuedConnection
        )

    @Slot()
    def _show_popup(self):
        """
        Show the popup immediately, and capture the selected text in parallel.

        Firing Ctrl+C before the popup is shown keeps focus on the user's
        source app while the keystroke is processed.
        """
        if self.paused:
            return

        logging.debug("Showing popup window")

        holder = _SelectedTextHolder()
        self.current_text_holder = holder
        self._fire_ctrl_c_and_capture_async(holder)

        try:
            if self.popup_window is not None:
                logging.debug("Existing popup window found")
                try:
                    if self.popup_window.isVisible():
                        self.popup_window.close()
                finally:
                    self.popup_window = None

            logging.debug("Creating new popup window")
            self.popup_window = ui.CustomPopupWindow.CustomPopupWindow(self)

            icon_path = resource_path(os.path.join("icons", "app_icon.png"))
            if os.path.exists(icon_path):
                self.setWindowIcon(QtGui.QIcon(icon_path))

            cursor_pos = QCursor.pos()
            screen = QGuiApplication.screenAt(cursor_pos)
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            if screen is None:
                logging.error("No screen available to position the popup")
                return
            screen_geometry = screen.availableGeometry()

            self.popup_window.show()
            self.popup_window.adjustSize()
            try:
                self.popup_window.activateWindow()
            except Exception as exc:  # noqa: BLE001
                logging.debug("Could not activate popup window: %s", exc)
            QtCore.QTimer.singleShot(100, self._focus_popup_input)

            popup_width = self.popup_window.width()
            popup_height = self.popup_window.height()
            x = cursor_pos.x()
            y = cursor_pos.y() + 20
            if x + popup_width > screen_geometry.right():
                x = screen_geometry.right() - popup_width
            if x < screen_geometry.left():
                x = screen_geometry.left()
            if y + popup_height > screen_geometry.bottom():
                y = cursor_pos.y() - popup_height - 10
            if y < screen_geometry.top():
                y = screen_geometry.top()
            self.popup_window.move(x, y)
            logging.debug("Popup window moved to position: (%s, %s)", x, y)
        except Exception as exc:  # noqa: BLE001
            logging.error("Error showing popup window: %s", exc, exc_info=True)
            if self.popup_window is not None:
                try:
                    self.popup_window.close()
                except Exception:
                    pass
                self.popup_window = None

    def _focus_popup_input(self):
        """Focus the popup's custom input, if the popup still exists."""
        try:
            if self.popup_window is not None and self.popup_window.isVisible():
                self.popup_window.custom_input.setFocus()
        except RuntimeError:
            # The underlying C++ widget was already destroyed.
            self.popup_window = None
        except Exception as exc:  # noqa: BLE001
            logging.debug("Could not focus popup input: %s", exc)

    def _fire_ctrl_c_and_capture_async(self, holder):
        """
        Inject Ctrl+C now (while focus is still on the source app), then poll
        the clipboard for the result in a background thread.

        The whole operation is serialized by `_capture_lock`. If another
        capture is already running we return an empty result immediately
        instead of stealing a stale clipboard value.
        """
        if not self._capture_lock.acquire(blocking=False):
            logging.debug("Clipboard capture already in progress; ignoring")
            holder.text = ""
            holder.ready.set()
            return

        try:
            clipboard_backup = pyperclip.paste() or ""
        except Exception:
            clipboard_backup = ""

        try:
            self.clear_clipboard()
            kbrd = pykeyboard.Controller()
            kbrd.press(pykeyboard.Key.ctrl)
            kbrd.press("c")
            kbrd.release("c")
            kbrd.release(pykeyboard.Key.ctrl)
        except Exception as exc:  # noqa: BLE001
            logging.error("Error simulating Ctrl+C: %s", exc)
            self._capture_lock.release()
            holder.text = ""
            holder.ready.set()
            return

        def _poll_clipboard():
            text = ""
            try:
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    try:
                        text = pyperclip.paste() or ""
                    except Exception as exc:  # noqa: BLE001
                        logging.error("Error reading clipboard during poll: %s", exc)
                        text = ""
                    if text:
                        break
                    time.sleep(0.05)
                holder.text = str(text)
                logging.debug("Captured selected text (len=%s)", len(holder.text))
            finally:
                try:
                    pyperclip.copy(clipboard_backup)
                except Exception as exc:  # noqa: BLE001
                    logging.error("Error restoring clipboard: %s", exc)
                holder.ready.set()
                self._capture_lock.release()

        threading.Thread(target=_poll_clipboard, daemon=True).start()

    @staticmethod
    def clear_clipboard():
        try:
            pyperclip.copy("")
        except Exception as exc:  # noqa: BLE001
            logging.error("Error clearing clipboard: %s", exc)

    # ------------------------------------------------------------------
    # Options / AI requests
    # ------------------------------------------------------------------

    def process_option(self, option, custom_change=None, holder=None):
        """
        Spawn a worker thread that waits for the clipboard capture and then
        runs the chosen option. Returns immediately so the GUI stays smooth.
        """
        logging.debug("Processing option: %s", option)

        if not self.options or option not in self.options:
            self.show_message_signal.emit(
                "Button Not Found",
                f'The "{option}" action is no longer available.\n'
                "It may have been deleted or options.json may be corrupt.",
            )
            return

        if self.current_provider is None:
            self.show_message_signal.emit(
                "No AI Provider",
                "No AI provider is configured. Open Settings and choose one.",
            )
            return

        if self.current_response_window is not None:
            try:
                self.current_response_window.close()
            except Exception as exc:  # noqa: BLE001
                logging.debug("Could not close old response window: %s", exc)
            self.current_response_window = None

        if holder is None:
            holder = self.current_text_holder

        threading.Thread(
            target=self.process_option_thread,
            args=(option, custom_change, holder),
            daemon=True,
        ).start()

    @Slot(str, str)
    def _setup_response_window(self, option, selected_text):
        """Open the response window and seed its chat history."""
        self.current_response_window = self.show_response_window(option, selected_text)
        self.current_response_window.chat_history = [
            {
                "role": "user",
                "content": f"Original text to {option.lower()}:\n\n{selected_text}",
            }
        ]

    def process_option_thread(self, option, custom_change=None, holder=None):
        """
        Worker: wait for the clipboard capture, open a response window if
        needed, then run the AI request.
        """
        logging.debug("Starting processing thread for option: %s", option)

        if holder is None or not holder.ready.wait(timeout=3.0):
            logging.warning("Timed out waiting for selected text capture")
        selected_text = (holder.text if holder else "") or ""

        if not selected_text.strip():
            self.show_message_signal.emit(
                "Error", "Please select text to use this option."
            )
            return

        option_cfg = (self.options or {}).get(option)
        if not isinstance(option_cfg, dict):
            self.show_message_signal.emit(
                "Button Not Found", f'The "{option}" action is no longer available.'
            )
            return

        open_in_window = bool(option_cfg.get("open_in_window"))
        if open_in_window:
            try:
                # BlockingQueuedConnection means the window exists before we
                # call the provider, so its chat history is ready for the
                # initial response.
                # noinspection PyTypeChecker
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "_setup_response_window",
                    QtCore.Qt.ConnectionType.BlockingQueuedConnection,
                    QtCore.Q_ARG(str, option),
                    QtCore.Q_ARG(str, selected_text),
                )
            except Exception as exc:  # noqa: BLE001
                logging.error("Could not create response window: %s", exc,
                              exc_info=True)
                self.show_message_signal.emit(
                    "Error", "Could not open the response window."
                )
                return

            if self.current_response_window is None:
                logging.error("Response window was not created")
                return

        try:
            prompt_prefix = option_cfg.get("prefix", "")
            system_instruction = option_cfg.get("instruction", "")
            if option == "Custom":
                prompt = (
                    f"{prompt_prefix}Described change: {custom_change}\n\n"
                    f"Text: {selected_text}"
                )
            else:
                prompt = f"{prompt_prefix}{selected_text}"

            self.output_queue = ""
            logging.debug("Getting response from provider for option: %s", option)

            if open_in_window:
                response = self.current_provider.get_response(
                    system_instruction, prompt, return_response=True
                )
                logging.debug("Got response of length: %s",
                              len(response) if response else 0)
                window = self.current_response_window
                if window is not None:
                    if response:
                        # noinspection PyTypeChecker
                        QtCore.QMetaObject.invokeMethod(
                            window,
                            "set_text",
                            QtCore.Qt.ConnectionType.QueuedConnection,
                            QtCore.Q_ARG(str, response),
                        )
                    else:
                        # The provider already showed a dialog with the real
                        # reason; give the window a useful final state instead
                        # of leaving it stuck on "Thinking…".
                        # noinspection PyTypeChecker
                        QtCore.QMetaObject.invokeMethod(
                            window,
                            "set_error",
                            QtCore.Qt.ConnectionType.QueuedConnection,
                            QtCore.Q_ARG(
                                str,
                                "The AI provider returned an error. Check "
                                "Settings and try again.",
                            ),
                        )
            else:
                logging.debug("Getting response for direct replacement")
                self.current_provider.get_response(system_instruction, prompt)
                logging.debug("Response processed")

        except Exception as exc:  # noqa: BLE001
            logging.error("An error occurred: %s", exc, exc_info=True)
            self.show_message_signal.emit(
                "Error", f"An error occurred:\n\n{str(exc)[:700]}"
            )

    @Slot(str, str)
    def show_message_box(self, title, message):
        try:
            QMessageBox.warning(None, title, message)
        except Exception as exc:  # noqa: BLE001
            logging.error("Could not show message box (%s: %s): %s",
                          title, message, exc)

    def show_response_window(self, option, text):
        response_window = ui.ResponseWindow.ResponseWindow(self, f"{option} Result")
        response_window.selected_text = text
        response_window.show()
        return response_window

    def replace_text(self, new_text):
        """
        Paste the model output into the focused app, or append it to the
        response window for Summary/Key Points/Table.
        """
        error_message = "ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST"

        if not new_text or not isinstance(new_text, str):
            logging.debug("No new text to process")
            return

        self.output_queue += new_text
        current_output = self.output_queue.strip()

        if current_output == error_message:
            self.show_message_signal.emit(
                "Error", "The text is incompatible with the requested change."
            )
            self.output_queue = ""
            return

        # Avoid pasting the sentinel one character at a time as it streams in.
        if len(current_output) <= len(error_message):
            clean_current = "".join(current_output.split())
            clean_error = "".join(error_message.split())
            if clean_current and clean_error.startswith(clean_current):
                return

        logging.debug("Processing output text")
        window = self.current_response_window
        try:
            if window is not None:
                window.append_text(new_text)
                if len(window.chat_history) == 1:  # Only the original text exists
                    window.chat_history.append({
                        "role": "assistant",
                        "content": self.output_queue.rstrip("\n"),
                    })
                return

            # Direct replacement path.
            cleaned_text = self.output_queue.rstrip("\n")
            if not cleaned_text:
                return
            clipboard_backup = pyperclip.paste() or ""
            try:
                pyperclip.copy(cleaned_text)
                # noinspection PyTypeChecker
                kbrd = pykeyboard.Controller()
                kbrd.press(pykeyboard.Key.ctrl)
                kbrd.press("v")
                kbrd.release("v")
                kbrd.release(pykeyboard.Key.ctrl)
                time.sleep(0.2)
            finally:
                try:
                    pyperclip.copy(clipboard_backup)
                except Exception as exc:  # noqa: BLE001
                    logging.error("Error restoring clipboard after paste: %s", exc)
            self.output_queue = ""
        except Exception as exc:  # noqa: BLE001
            logging.error("Error processing output: %s", exc, exc_info=True)
            self.show_message_signal.emit(
                "Error", f"Could not insert the AI response:\n\n{str(exc)[:500]}"
            )

    # ------------------------------------------------------------------
    # Tray / windows
    # ------------------------------------------------------------------

    def create_tray_icon(self):
        if self.tray_icon:
            logging.debug("Tray icon already exists")
            return

        logging.debug("Creating system tray icon")
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            logging.warning("System tray is not available on this system")

        icon_path = resource_path(os.path.join("icons", "app_icon.png"))
        if os.path.exists(icon_path):
            self.tray_icon = QtWidgets.QSystemTrayIcon(QtGui.QIcon(icon_path), self)
        else:
            logging.warning("Tray icon not found at %s", icon_path)
            self.tray_icon = QtWidgets.QSystemTrayIcon(self)

        self.tray_icon.setToolTip("Writing Tools")
        self.tray_menu = QtWidgets.QMenu()
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)

        self.update_tray_menu()
        self.tray_icon.show()
        logging.debug("Tray icon displayed")

    def _on_tray_activated(self, reason):
        """Double-clicking the tray icon opens Settings."""
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_settings()

    def update_tray_menu(self):
        if self.tray_menu is None:
            return
        self.tray_menu.clear()
        self.apply_dark_mode_styles(self.tray_menu)

        settings_action = self.tray_menu.addAction(self._("Settings"))
        settings_action.triggered.connect(lambda _checked=False: self.show_settings())

        self.toggle_action = self.tray_menu.addAction(
            self._("Resume") if self.paused else self._("Pause")
        )
        self.toggle_action.triggered.connect(self.toggle_paused)

        about_action = self.tray_menu.addAction(self._("About"))
        about_action.triggered.connect(self.show_about)

        exit_action = self.tray_menu.addAction(self._("Exit"))
        exit_action.triggered.connect(self.exit_app)

    def toggle_paused(self):
        logging.debug("Toggle paused state")
        self.paused = not self.paused
        if self.toggle_action is not None:
            try:
                self.toggle_action.setText(
                    self._("Resume") if self.paused else self._("Pause")
                )
            except RuntimeError:
                self.toggle_action = None
        logging.debug("App is paused" if self.paused else "App is resumed")

    @staticmethod
    def apply_dark_mode_styles(menu):
        """Match the tray menu to the app palette (Qt's QSS skips tray menus)."""
        colours = UIUtils.palette()
        palette = menu.palette()
        palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(colours["card"]))
        palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(colours["text"]))
        menu.setPalette(palette)

    # ------------------------------------------------------------------
    # Follow-up chat
    # ------------------------------------------------------------------

    def process_followup_question(self, response_window, question):
        """Process a follow-up question in the response window's chat."""
        logging.debug("Processing follow-up question: %s", question)

        if response_window is None or getattr(response_window, "_closed", False):
            return

        def process_thread():
            logging.debug("Starting follow-up processing thread")
            try:
                if not response_window.chat_history:
                    self.show_message_signal.emit("Error", "Chat history not found")
                    return

                response_window.chat_history.append({
                    "role": "user",
                    "content": question,
                })
                history = list(response_window.chat_history)
                system_instruction = (
                    "You are a helpful AI assistant. Provide clear and direct "
                    "responses, maintaining the same format and style as your "
                    "previous responses. If appropriate, use Markdown formatting "
                    "to make your response more readable."
                )

                # Every provider knows how to consume OpenAI-style messages:
                # Gemini maps roles/drops the system entry, Ollama and the
                # OpenAI-compatible providers accept them as-is.
                messages = [{"role": "system", "content": system_instruction}]
                for message in history:
                    role = message.get("role", "user")
                    content = message.get("content", "")
                    messages.append({
                        "role": "assistant" if role == "assistant" else "user",
                        "content": content,
                    })

                response_text = self.current_provider.get_response(
                    system_instruction, messages, return_response=True
                )

                if not response_text:
                    # Provider already surfaced the detailed error dialog.
                    self._emit_followup(
                        response_window,
                        "⚠️ The AI provider returned an error. "
                        "Check Settings and try again.",
                    )
                    return

                response_window.chat_history.append({
                    "role": "assistant",
                    "content": response_text,
                })
                self._emit_followup(response_window, response_text)

            except Exception as exc:  # noqa: BLE001
                logging.error("Error processing follow-up question: %s", exc,
                              exc_info=True)
                self.show_message_signal.emit(
                    "Error", f"An error occurred:\n\n{str(exc)[:700]}"
                )
                self._emit_followup(
                    response_window,
                    "Sorry, an error occurred while processing your question.",
                )

        threading.Thread(target=process_thread, daemon=True).start()

    @staticmethod
    def _emit_followup(response_window, text):
        """Deliver a follow-up response to the right (still-open) window."""
        try:
            if getattr(response_window, "_closed", False):
                return
            signal = getattr(response_window, "followup_response_signal", None)
            if signal is not None:
                signal.emit(text)
            else:  # pragma: no cover - compatibility fallback
                response_window.handle_followup_response(text)
        except RuntimeError:
            logging.debug("Response window was deleted before reply arrived")

    def show_settings(self):
        logging.debug("Showing settings window")
        if self.settings_window is not None:
            try:
                if self.settings_window.isVisible():
                    # Already open (e.g. double-click on the tray icon):
                    # just bring it to the front.
                    self.settings_window.raise_()
                    self.settings_window.activateWindow()
                    return
                self.settings_window.close()
            except RuntimeError:
                pass  # the Qt object is already gone
            self.settings_window = None
        self.settings_window = ui.SettingsWindow.SettingsWindow(self)
        self.settings_window.retranslate_ui()
        self.settings_window.show()

    def show_about(self):
        logging.debug("Showing about window")
        if not self.about_window:
            self.about_window = ui.AboutWindow.AboutWindow()
        self.about_window.show()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def setup_ctrl_c_listener(self):
        """Listen for Ctrl+C (SIGINT) to exit the app gracefully."""
        try:
            signal.signal(
                signal.SIGINT,
                lambda signum, frame: self.handle_sigint(signum, frame),
            )
        except (ValueError, OSError) as exc:
            logging.debug("Could not install SIGINT handler: %s", exc)

        # An empty timer keeps the Python signal handler responsive inside
        # Qt's event loop.
        self.ctrl_c_timer = QtCore.QTimer(self)
        self.ctrl_c_timer.timeout.connect(lambda: None)
        self.ctrl_c_timer.start(100)

    def handle_sigint(self, signum, frame):
        logging.info("Received SIGINT. Exiting...")
        self.exit_app()

    def relaunch(self):
        """
        Start a fresh copy of Writing Tools and quit this one.

        Qt's global scale factor can only be changed by recreating the
        QApplication, so the UI-scale setting uses this instead of asking the
        user to find the exe again. The new process waits briefly for this
        one to release the single-instance lock (WT_RELAUNCH).
        """
        if self._shutting_down:
            return False
        try:
            if getattr(sys, "frozen", False):
                command = [sys.executable]
            else:
                script = (
                    os.path.abspath(sys.argv[0])
                    if sys.argv and sys.argv[0]
                    else os.path.abspath("main.py")
                )
                command = [sys.executable, script]

            env = os.environ.copy()
            env["WT_RELAUNCH"] = "1"
            kwargs = {
                "cwd": get_app_dir(),
                "env": env,
                "close_fds": True,
            }
            if sys.platform.startswith("win"):
                kwargs["creationflags"] = (
                    subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            logging.info("Relaunching Writing Tools: %s", command)
            subprocess.Popen(command, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logging.error("Could not relaunch Writing Tools: %s", exc)
            try:
                QMessageBox.warning(
                    None,
                    "Writing Tools",
                    "The new UI scale is saved, but Writing Tools could not "
                    "restart automatically.\n\n"
                    "Please close and reopen it to apply the change.\n\n"
                    f"{exc}",
                )
            except Exception:
                pass
            return False

        self.exit_app()
        return True

    def exit_app(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        logging.debug("Stopping the listener")
        if self.hotkey_listener is not None:
            try:
                self.hotkey_listener.stop()
            except Exception as exc:  # noqa: BLE001
                logging.debug("Error stopping hotkey listener: %s", exc)
            finally:
                self.hotkey_listener = None
        if self.tray_icon is not None:
            try:
                self.tray_icon.hide()
            except Exception:
                pass
        logging.debug("Exiting application")
        self.quit()
