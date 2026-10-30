import logging
import os
import threading

from aiprovider import (
    AIProvider,
    _spec_summary,
    deobfuscate_api_key,
    normalize_tier_filter,
)
from apputils import resource_path
from defaults import (
    DEFAULT_UI_SCALE,
    MAX_UI_SCALE,
    MIN_UI_SCALE,
    UI_SCALE_STEP,
    clamp_ui_scale,
)
from PySide6 import QtCore, QtWidgets
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QHBoxLayout, QScrollArea

from ui.AutostartManager import AutostartManager
from ui.UIUtils import NoteItemDelegate, NoWheelComboBox, UIUtils

_ = lambda x: x


class _ModelFetchSignals(QtCore.QObject):
    """Signals used to deliver a background model-list fetch to the UI thread."""

    finished = QtCore.Signal(int, object)
    failed = QtCore.Signal(int, str)


class SettingsWindow(QtWidgets.QWidget):
    """
    The settings window for the application.
    Now with scrolling support for better usability on smaller screens.
    """

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.current_provider_layout = None
        self._model_setting = None
        self._model_status_label = None
        self._model_refresh_button = None
        self._model_details_label = None
        self._model_request_id = 0
        # Unsaved edits, keyed by provider name. Switching providers must not
        # throw away what was typed, and nothing is written to config.json
        # until the user presses Save.
        self._drafts = {}
        self._rendered_provider = None
        self.save_button = None
        self.scroll_area = None
        self.scroll_content = None
        self.bottom_container = None
        self._model_signals = _ModelFetchSignals(self)
        self._model_signals.finished.connect(self._on_models_fetched)
        self._model_signals.failed.connect(self._on_model_fetch_failed)
        self.provider_dropdown = None
        self.provider_container = None
        self.autostart_checkbox = None
        self.shortcut_input = None
        self.ui_scale_slider = None
        self.ui_scale_value_label = None
        self._initial_ui_scale = int(round(DEFAULT_UI_SCALE * 100))
        self.init_ui()
        self.retranslate_ui()


    def retranslate_ui(self):
        self.setWindowTitle(_("Settings"))

    def init_provider_ui(self, provider: AIProvider, layout):
        """
        Initialize the user interface for the provider, including logo, name, description and all settings.
        """
        if provider is None:
            return
        # Keep whatever is on screen for the provider we are leaving.
        self._capture_provider_draft()

        # Any fetch still in flight belongs to the previous provider.
        self._model_request_id += 1
        self._model_setting = None
        self._model_status_label = None
        self._model_refresh_button = None
        self._model_details_label = None

        # The old widgets are deleted below; forget the Python references so
        # set_value/get_value never touches a deleted Qt object later.
        previous = getattr(self, "_rendered_provider", None)
        if previous is not None:
            for setting in previous.settings:
                try:
                    setting.release_ui()
                except (AttributeError, RuntimeError):
                    pass
        self._rendered_provider = provider

        if self.current_provider_layout:
            try:
                self.current_provider_layout.setParent(None)
                UIUtils.clear_layout(self.current_provider_layout)
                self.current_provider_layout.deleteLater()
            except RuntimeError:
                # The previous layout's widgets were already deleted.
                pass

        self.current_provider_layout = QtWidgets.QVBoxLayout()
        self.current_provider_layout.setContentsMargins(0, 0, 0, 0)
        self.current_provider_layout.setSpacing(8)

        # Create a horizontal layout for the logo and provider name
        provider_header_layout = QtWidgets.QHBoxLayout()
        provider_header_layout.setSpacing(10)
        provider_header_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        if provider.logo:
            logo_path = resource_path(
                os.path.join('icons', f"provider_{provider.logo}.png")
            )
            if os.path.exists(logo_path):
                targetPixmap = UIUtils.resize_and_round_image(QImage(logo_path), 24, 12)
                logo_label = QtWidgets.QLabel()
                logo_label.setPixmap(targetPixmap)
                logo_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
                provider_header_layout.addWidget(logo_label)

        provider_name_label = QtWidgets.QLabel(provider.display_name)
        UIUtils.set_label_role(provider_name_label, "section")
        provider_name_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
        provider_header_layout.addWidget(provider_name_label)

        self.current_provider_layout.addLayout(provider_header_layout)

        if provider.description:
            description_label = QtWidgets.QLabel(provider.description)
            UIUtils.set_label_role(description_label, "muted")
            description_label.setWordWrap(True)
            self.current_provider_layout.addWidget(description_label)

        if hasattr(provider, 'ollama_button_text'):
            # Create container for buttons
            button_layout = QtWidgets.QHBoxLayout()
            
            # Add Ollama setup button
            ollama_button = QtWidgets.QPushButton(provider.ollama_button_text)
            UIUtils.set_variant(ollama_button, "secondary")
            if provider.ollama_button_action:
                ollama_button.clicked.connect(provider.ollama_button_action)
            button_layout.addWidget(ollama_button)
            
            # Add original button
            main_button = QtWidgets.QPushButton(provider.button_text)
            UIUtils.set_variant(main_button, "primary")
            if provider.button_action:
                main_button.clicked.connect(provider.button_action)
            button_layout.addWidget(main_button)
            
            self.current_provider_layout.addLayout(button_layout)
        else:
            # Original single button logic
            if provider.button_text:
                button = QtWidgets.QPushButton(provider.button_text)
                UIUtils.set_variant(button, "primary")
                if provider.button_action:
                    button.clicked.connect(provider.button_action)
                self.current_provider_layout.addWidget(button, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)

        # Initialize config if needed
        if not isinstance(self.app.config, dict):
            self.app.config = {}
        if not isinstance(self.app.config.get("providers"), dict):
            self.app.config["providers"] = {}
        if provider.provider_name not in self.app.config["providers"]:
            self.app.config["providers"][provider.provider_name] = {}

        # Values come from this session's draft (if the provider was edited
        # already) or from its own saved config block. Nothing is invented.
        saved = self.app.config["providers"][provider.provider_name]
        draft = self._drafts.get(provider.provider_name, {})
        provider.model_tier_filter = normalize_tier_filter(
            draft.get("model_tier_filter", saved.get("model_tier_filter"))
        )

        # Add provider settings
        for setting in provider.settings:
            value = draft.get(setting.name, saved.get(setting.name, setting.default_value))
            if setting.name == "api_key":
                value = deobfuscate_api_key(value)
            setting.set_value(value)
            is_model_setting = (
                setting.name in ("api_model", "model_name")
                and hasattr(setting, "set_options")
            )
            if is_model_setting:
                # Model-tier filter sits directly above the model dropdown.
                filter_row = QtWidgets.QHBoxLayout()
                filter_row.setSpacing(10)
                filter_label = QtWidgets.QLabel(_("Model filter"))
                UIUtils.set_label_role(filter_label, "section")
                filter_row.addWidget(filter_label)

                self.model_tier_dropdown = NoWheelComboBox()
                self.model_tier_dropdown.addItem(_("All models"), "all")
                self.model_tier_dropdown.addItem(_("Free only"), "free")
                self.model_tier_dropdown.addItem(_("Paid only"), "paid")
                idx = self.model_tier_dropdown.findData(provider.model_tier_filter)
                self.model_tier_dropdown.setCurrentIndex(idx if idx >= 0 else 0)
                self.model_tier_dropdown.currentIndexChanged.connect(
                    self._on_model_tier_changed
                )
                filter_row.addWidget(self.model_tier_dropdown, 1)
                self.current_provider_layout.addLayout(filter_row)

            setting.render_to_layout(self.current_provider_layout)
            if (setting.name in ("api_model", "model_name")
                    and hasattr(setting, "set_options")):
                # Only dropdown-backed model settings can accept a live list.
                self._model_setting = setting
                self._model_details_label = QtWidgets.QLabel()
                self._model_details_label.setWordWrap(True)
                self._model_details_label.setTextInteractionFlags(
                    QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
                )
                self.current_provider_layout.addWidget(self._model_details_label)
                self._connect_model_signals(provider, setting)

        if self._model_setting is not None:
            # Apply model-dependent options (e.g. Gemini thinking levels) and
            # show the selected model's specs before the user changes anything.
            try:
                provider.on_model_change()
            except Exception as exc:  # noqa: BLE001
                logging.debug("Could not update model-dependent settings: %s", exc)
            self._update_model_details(provider, self._model_setting)

        # Live model discovery. Free providers rotate/retire model IDs often,
        # so the user can pull the current list instead of trusting a stale
        # hard-coded dropdown.
        if self._model_setting is not None:
            refresh_button = QtWidgets.QPushButton(
                _("🔄 Refresh available models")
            )
            UIUtils.set_variant(refresh_button, "secondary")
            refresh_button.clicked.connect(
                lambda _checked=False, p=provider: self.refresh_models(p)
            )
            self._model_refresh_button = refresh_button
            self.current_provider_layout.addWidget(refresh_button)

            self._model_status_label = QtWidgets.QLabel("")
            self._model_status_label.setWordWrap(True)
            UIUtils.set_label_role(self._model_status_label, "muted")
            # Only shown once there is something to report, so the form does
            # not waste a line on a permanent hint.
            self._model_status_label.setVisible(False)
            self.current_provider_layout.addWidget(self._model_status_label)

        layout.addLayout(self.current_provider_layout)
        # Provider forms differ in length; keep the window snug but whole.
        self._schedule_fit_height()

    # -- live model discovery ---------------------------------------------

    def _connect_model_signals(self, provider, setting):
        """Refresh specs and dependent options when the model changes."""
        dropdown = getattr(setting, "dropdown", None)
        if dropdown is not None:
            dropdown.currentIndexChanged.connect(
                lambda *_args, p=provider: self._on_model_selection_changed(p)
            )
        custom_input = getattr(setting, "custom_input", None)
        if custom_input is not None:
            custom_input.editingFinished.connect(
                lambda p=provider: self._on_model_selection_changed(p)
            )

    def _on_model_selection_changed(self, provider):
        if (provider is not self._current_provider()
                or self._model_setting is None):
            return
        try:
            provider.on_model_change()
        except Exception as exc:  # noqa: BLE001
            logging.debug("Could not update model-dependent settings: %s", exc)
        self._update_model_details(provider, self._model_setting)

    def _update_model_details(self, provider, setting=None):
        label = self._model_details_label
        if label is None:
            return
        setting = setting or self._model_setting
        model_id = setting.get_value() if setting is not None else ""
        spec = (getattr(provider, "model_specs", {}) or {}).get(model_id, {})
        summary = _spec_summary(spec)
        if summary:
            text = _("Model details: ") + summary
        else:
            text = _(
                "Model details are not available yet. Click "
                "\"Refresh available models\" to load specs."
            )
        try:
            UIUtils.set_label_role(label, "muted")
            label.setText(text)
        except RuntimeError:
            pass

    def _current_provider(self):
        try:
            index = self.provider_dropdown.currentData()
            if index is None:
                return None
            return self.app.providers[int(index)]
        except (AttributeError, IndexError, TypeError, ValueError):
            return None

    @staticmethod
    def _sync_provider_settings(provider):
        """Copy the values currently visible in the form onto the provider."""
        for setting in provider.settings:
            try:
                setattr(provider, setting.name, setting.get_value())
            except Exception as exc:  # noqa: BLE001
                logging.debug("Could not sync setting %s: %s", setting.name, exc)

    def refresh_models(self, provider):
        if self._model_setting is None:
            return
        button = self._model_refresh_button
        if button is not None:
            try:
                if not button.isEnabled():
                    return
                button.setEnabled(False)
                button.setText(_("Fetching models…"))
            except RuntimeError:
                return

        self._sync_provider_settings(provider)
        self._model_request_id += 1
        request_id = self._model_request_id
        setting = self._model_setting
        current_value = setting.get_value()

        if self._model_status_label is not None:
            self._set_model_status(_("Fetching the live model list…"),
                                   error=False)

        signals = self._model_signals

        def work():
            try:
                result = provider.list_models()
                if isinstance(result, tuple) and len(result) == 2:
                    models, note = result
                else:
                    models, note = result, ""
                payload = (models or [], note or "", provider, setting,
                           current_value, request_id)
                signals.finished.emit(request_id, payload)
            except Exception as exc:  # noqa: BLE001
                logging.error("Could not fetch models for %s: %s",
                              provider.provider_name, exc)
                try:
                    signals.failed.emit(
                        request_id, str(exc) or exc.__class__.__name__
                    )
                except RuntimeError:
                    # Settings window was closed while the request was running.
                    pass

        threading.Thread(
            target=work,
            daemon=True,
            name=f"model-list-{provider.provider_name}",
        ).start()

    def _finish_model_fetch(self):
        button = self._model_refresh_button
        if button is not None:
            try:
                button.setEnabled(True)
                button.setText(_("🔄 Refresh available models"))
            except RuntimeError:
                pass

    def _on_models_fetched(self, request_id, payload):
        if request_id != self._model_request_id:
            return
        self._finish_model_fetch()
        try:
            models, note, provider, setting, old_value, _request_id = payload
        except (TypeError, ValueError):
            return
        if (provider is not self._current_provider()
                or setting is not self._model_setting
                or self._model_status_label is None):
            return
        if not models:
            self._set_model_status(
                note or _("The provider returned no usable models."),
                error=True,
            )
            return

        setting.set_options(models)
        available = {value for _label, value in models}
        # Keep only specs that belong to the freshly fetched catalog. This is
        # what removes retired models from the persisted list on Save.
        specs = getattr(provider, "model_specs", {}) or {}
        provider.model_specs = {
            model_id: spec for model_id, spec in specs.items()
            if model_id in available
        }
        provider.cached_models = list(models)

        message = note or f"Found {len(models)} models."
        if old_value and old_value not in available and models:
            # The saved model is gone (or is not part of this provider's
            # catalog any more). Preselect a current one so clicking Save
            # actually fixes the request instead of keeping a dead ID.
            default_model = getattr(provider, "DEFAULT_MODEL", "") or ""
            replacement = (
                default_model
                if default_model in available
                else models[0][1]
            )
            setting.set_value(replacement)
            message += _(
                f" Your saved model ({old_value}) is not in the live list; "
                f"selected {replacement} instead — click Save to keep it."
            )
        try:
            provider.on_model_change()
        except Exception as exc:  # noqa: BLE001
            logging.debug("Could not update model-dependent settings: %s", exc)
        self._update_model_details(provider, setting)
        self._set_model_status(message, error=False)

    def _on_model_fetch_failed(self, request_id, message):
        if request_id != self._model_request_id:
            return
        self._finish_model_fetch()
        self._set_model_status(message, error=True)

    def _set_model_status(self, message, error=False):
        label = self._model_status_label
        if label is None:
            return
        try:
            UIUtils.set_label_role(label, "error" if error else "success")
            label.setText(message)
            label.setVisible(bool(message))
        except RuntimeError:
            pass

    def init_ui(self):
        """
        Build a compact, logical settings form.

        The content is sized to fit the window, so nothing normally needs
        scrolling; the scroll area is only a safety net on very small screens
        and never shows a scrollbar.
        """
        self.setWindowTitle(_('Settings'))
        if not isinstance(self.app.config, dict):
            self.app.config = {}
        # Compact desktop width, like a standard preferences window.
        self.setFixedWidth(560)

        UIUtils.setup_window_and_layout(self)
        main_layout = QtWidgets.QVBoxLayout(self.background)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll_area.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # Let the window background show through.
        scroll_area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget { background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        scroll_area.viewport().setAutoFillBackground(False)
        self.scroll_area = scroll_area

        scroll_content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(scroll_content)
        content_layout.setContentsMargins(20, 14, 20, 6)
        content_layout.setSpacing(8)
        self.scroll_content = scroll_content

        # Shared label column so the general rows line up.
        label_width = 78

        title_label = QtWidgets.QLabel(_("Settings"))
        UIUtils.set_label_role(title_label, "title")
        content_layout.addWidget(
            title_label, alignment=QtCore.Qt.AlignmentFlag.AlignCenter
        )

        # General options on one row: start-on-boot (Windows build only)
        # and the shortcut field side by side.
        general_row = QHBoxLayout()
        general_row.setSpacing(10)
        if AutostartManager.get_startup_path():
            self.autostart_checkbox = QtWidgets.QCheckBox(_("Start on Boot"))
            self.autostart_checkbox.setChecked(
                AutostartManager.check_autostart()
            )
            self.autostart_checkbox.stateChanged.connect(
                self.toggle_autostart
            )
            general_row.addWidget(self.autostart_checkbox)
        general_row.addStretch(1)
        shortcut_label = QtWidgets.QLabel(_("Shortcut"))
        UIUtils.set_label_role(shortcut_label, "section")
        general_row.addWidget(shortcut_label)
        self.shortcut_input = QtWidgets.QLineEdit(
            self.app.config.get('shortcut', 'ctrl+space')
        )
        self.shortcut_input.setFixedWidth(170)
        general_row.addWidget(self.shortcut_input)
        content_layout.addLayout(general_row)

        # UI scale: label, slider and live percentage in a single row.
        # Qt reads QT_SCALE_FACTOR once at startup, so the change is
        # stored now and applied on the next launch (Save offers to
        # relaunch immediately).
        scale_row = QHBoxLayout()
        scale_row.setSpacing(10)
        scale_label = QtWidgets.QLabel(_("UI Scale"))
        UIUtils.set_label_role(scale_label, "section")
        scale_label.setFixedWidth(label_width)
        scale_row.addWidget(scale_label)

        self.ui_scale_slider = QtWidgets.QSlider(
            QtCore.Qt.Orientation.Horizontal
        )
        self.ui_scale_slider.setRange(
            int(round(MIN_UI_SCALE * 100)),
            int(round(MAX_UI_SCALE * 100)),
        )
        self.ui_scale_slider.setSingleStep(UI_SCALE_STEP)
        self.ui_scale_slider.setPageStep(UI_SCALE_STEP * 2)
        self.ui_scale_slider.setTickInterval(UI_SCALE_STEP * 2)
        self.ui_scale_slider.setTickPosition(
            QtWidgets.QSlider.TickPosition.TicksBelow
        )
        self.ui_scale_slider.setToolTip(
            _("Scale everything from 70% to 140%. Takes effect when "
              "Writing Tools restarts.")
        )
        current_scale = int(
            round(
                clamp_ui_scale(
                    self.app.config.get("ui_scale", DEFAULT_UI_SCALE)
                )
                * 100
            )
        )
        self._initial_ui_scale = current_scale
        self.ui_scale_slider.setValue(current_scale)
        self.ui_scale_slider.valueChanged.connect(
            self._on_ui_scale_changed
        )
        scale_row.addWidget(self.ui_scale_slider, 1)

        self.ui_scale_value_label = QtWidgets.QLabel("")
        self.ui_scale_value_label.setFixedWidth(38)
        self.ui_scale_value_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.ui_scale_value_label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; "
            f"color: {UIUtils.palette()['accent']};"
        )
        scale_row.addWidget(self.ui_scale_value_label)
        # Fill the percentage now that the label exists.
        self._on_ui_scale_changed(current_scale)
        content_layout.addLayout(scale_row)

        # Thin divider between the general options and the provider section.
        divider = QtWidgets.QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(
            f"background-color: {UIUtils.palette()['border']}; border: none;"
        )
        content_layout.addWidget(divider)

        # Provider selection shares its row with the label.
        provider_row = QHBoxLayout()
        provider_row.setSpacing(10)
        provider_label = QtWidgets.QLabel(_("AI Provider"))
        UIUtils.set_label_role(provider_label, "section")
        provider_label.setFixedWidth(label_width)
        provider_row.addWidget(provider_label)

        self.provider_dropdown = NoWheelComboBox()
        self.provider_dropdown.setInsertPolicy(
            QtWidgets.QComboBox.InsertPolicy.NoInsert
        )

        # Clean name on the left, grey hint ("free tier available") right-aligned
        # in the open list. The stable provider_name stays the config key.
        self.provider_dropdown.setItemDelegate(NoteItemDelegate(self.provider_dropdown))
        previous_local = None
        for index, provider in enumerate(self.app.providers):
            is_local = getattr(provider, "tier", "") == "local"
            if previous_local is False and is_local:
                # Separator line before the local providers at the end.
                self.provider_dropdown.insertSeparator(self.provider_dropdown.count())
                separator = self.provider_dropdown.model().item(
                    self.provider_dropdown.count() - 1
                )
                if separator is not None:
                    # Not selectable, so it can never become the active provider.
                    separator.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            self.provider_dropdown.addItem(provider.display_name, index)
            self.provider_dropdown.setItemData(
                self.provider_dropdown.count() - 1,
                provider.display_note,
                NoteItemDelegate.NOTE_ROLE,
            )
            previous_local = is_local

        def _row_for(provider_name):
            for row in range(self.provider_dropdown.count()):
                idx = self.provider_dropdown.itemData(row)
                if (idx is not None
                        and self.app.providers[int(idx)].provider_name == provider_name):
                    return row
            return -1

        provider_index = _row_for(
            self.app.config.get('provider', 'Gemini (Recommended)')
        )
        if provider_index < 0:
            provider_index = _row_for("Gemini (Recommended)")
        self.provider_dropdown.setCurrentIndex(provider_index if provider_index >= 0 else 0)
        provider_row.addWidget(self.provider_dropdown, 1)
        content_layout.addLayout(provider_row)

        # Container for the selected provider's own settings.
        self.provider_container = QtWidgets.QVBoxLayout()
        self.provider_container.setContentsMargins(0, 2, 0, 0)
        self.provider_container.setSpacing(6)
        content_layout.addLayout(self.provider_container)

        # Initialize provider UI
        provider_instance = self._current_provider()
        self.init_provider_ui(provider_instance, self.provider_container)

        # Connect provider dropdown
        self.provider_dropdown.currentIndexChanged.connect(
            lambda: self.init_provider_ui(self._current_provider(), self.provider_container)
        )

        # Set up scroll area with content
        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

        # Create bottom container for save button and hint
        bottom_container = QtWidgets.QWidget()
        # Scope the transparent background to the container itself. A bare
        # declaration would also make the primary button transparent.
        bottom_container.setObjectName("bottomContainer")
        bottom_container.setStyleSheet(
            "#bottomContainer { background: transparent; }"
        )
        bottom_layout = QtWidgets.QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(20, 4, 20, 12)
        bottom_layout.setSpacing(6)
        self.bottom_container = bottom_container

        # Add save button to bottom container
        save_button = QtWidgets.QPushButton(_("Save"))
        UIUtils.set_variant(save_button, "primary")
        save_button.clicked.connect(self.save_settings)
        self.save_button = save_button
        bottom_layout.addWidget(save_button)

        live_hint = QtWidgets.QLabel(
            _("Changes apply immediately — no restart needed.")
        )
        UIUtils.set_label_role(live_hint, "muted")
        live_hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        live_hint.setWordWrap(True)
        bottom_layout.addWidget(live_hint)

        main_layout.addWidget(bottom_container)

        # Size the window to the content so no scrollbar is needed.
        self._schedule_fit_height()

    # -- compact sizing ----------------------------------------------------

    _MIN_HEIGHT = 260

    def _fit_height(self):
        content = getattr(self, "scroll_content", None)
        bottom = getattr(self, "bottom_container", None)
        if content is None or bottom is None:
            return
        try:
            width = self.width() or 560
            layout = content.layout()
            content_height = layout.heightForWidth(width) if layout is not None else -1
            if content_height <= 0:
                content_height = content.sizeHint().height()
            total = content_height + bottom.sizeHint().height() + 4
        except RuntimeError:
            return
        screen = self.screen()
        if screen is None:
            screen = QtWidgets.QApplication.primaryScreen()
        available = screen.availableGeometry().height() if screen is not None else 800
        total = max(260, min(total, int(available * 0.94)))
        try:
            self.setFixedHeight(total)
        except RuntimeError:
            pass

    def _schedule_fit_height(self):
        """Fit the window to its content once the new layout is in place."""
        QtCore.QTimer.singleShot(0, self._fit_height)

    def _on_model_tier_changed(self, _index=None):
        """
        Apply the Free/Paid/All choice to the current provider only.

        Only that provider's model list is rebuilt, so the selected model
        (and anything else typed into the form) stays untouched.
        """
        provider = self._current_provider()
        dropdown = getattr(self, "model_tier_dropdown", None)
        if provider is None or dropdown is None:
            return
        value = normalize_tier_filter(dropdown.currentData())
        provider.model_tier_filter = value
        self._drafts.setdefault(provider.provider_name, {})[
            "model_tier_filter"
        ] = value
        setting = self._model_setting
        if setting is not None:
            setting.set_options(setting.full_options)
            self._update_model_details(provider, setting)
        self._schedule_fit_height()

    def _capture_provider_draft(self):
        """Remember the visible values of the current provider, unsaved."""
        provider = getattr(self, "_rendered_provider", None)
        if provider is None:
            return
        values = self._drafts.setdefault(provider.provider_name, {})
        for setting in provider.settings:
            rendered = (getattr(setting, "input", None) is not None
                        or getattr(setting, "dropdown", None) is not None)
            if not rendered:
                continue
            try:
                values[setting.name] = setting.get_value()
            except (AttributeError, RuntimeError):
                pass
        values["model_tier_filter"] = normalize_tier_filter(
            getattr(provider, "model_tier_filter", "all")
        )

    @staticmethod
    def toggle_autostart(state):
        """Toggle the autostart setting."""
        AutostartManager.set_autostart(state == 2)

    def _on_ui_scale_changed(self, value):
        """Snap the slider to 5% steps and refresh the percentage label."""
        snapped = int(round(value / UI_SCALE_STEP) * UI_SCALE_STEP)
        snapped = max(
            int(round(MIN_UI_SCALE * 100)),
            min(int(round(MAX_UI_SCALE * 100)), snapped),
        )
        slider = self.ui_scale_slider
        if slider is not None and snapped != value:
            slider.blockSignals(True)
            slider.setValue(snapped)
            slider.blockSignals(False)
        label = self.ui_scale_value_label
        if label is not None:
            try:
                label.setText(f"{snapped}%")
            except RuntimeError:
                pass

    def _prompt_scale_relaunch(self):
        """Offer an immediate restart so the new scale takes effect."""
        relaunch = getattr(self.app, "relaunch", None)
        if not callable(relaunch):
            return
        choice = QtWidgets.QMessageBox.question(
            self,
            _("UI Scale Saved"),
            _("The new UI scale will be applied when Writing Tools starts.\n\n"
              "Restart Writing Tools now?\n\n"
              "Your settings, buttons and instructions are kept."),
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.Yes,
        )
        if choice == QtWidgets.QMessageBox.StandardButton.Yes:
            relaunch()

    def save_settings(self):
        """Save the current settings."""
        if not isinstance(self.app.config, dict):
            self.app.config = {}
        # Preserve an existing locale; only default to English for new users.
        self.app.config.setdefault('locale', 'en')

        self.app.config['shortcut'] = self.shortcut_input.text().strip() or 'ctrl+space'
        if self.ui_scale_slider is not None:
            self.app.config['ui_scale'] = clamp_ui_scale(
                self.ui_scale_slider.value() / 100.0
            )
        # The filter lives in each provider's own config block now; drop the
        # old global key.
        self.app.config.pop('model_tier_filter', None)
        self.app.config['streaming'] = False
        current = self._current_provider()
        if current is not None:
            self.app.config['provider'] = current.provider_name

        # Mark config as current for both migrations (new users start here).
        self.app.config['is_config_file_updated_for_v8'] = True
        self.app.config['is_config_file_updated_for_v9'] = True
        self.app.config['is_config_file_updated_for_v10'] = True
        self.app.config['is_config_file_updated_for_v11'] = True

        selected_provider = self._current_provider()
        if selected_provider is None:
            logging.error("Provider dropdown selection is invalid")
            return

        # Write every provider that was edited in this session, each into its
        # own block, then persist config.json once.
        self._capture_provider_draft()
        for provider in self.app.providers:
            values = self._drafts.get(provider.provider_name)
            if not values:
                continue
            provider.model_tier_filter = normalize_tier_filter(
                values.get("model_tier_filter")
            )
            provider.save_config(values=values, persist=False)
        self._drafts.clear()
        self.app.save_config(self.app.config)

        provider_name = self.app.config.get('provider', selected_provider.provider_name)
        self.app.current_provider = next(
            (provider for provider in self.app.providers
             if provider.provider_name == provider_name),
            self.app.providers[0]
        )

        providers_config = self.app.config.get("providers", {})
        if not isinstance(providers_config, dict):
            providers_config = {}
        self.app.current_provider.load_config(
            providers_config.get(self.app.current_provider.provider_name, {})
        )
        self.app.config['provider'] = self.app.current_provider.provider_name
        self.app.save_config(self.app.config)

        self.app.register_hotkey()

        self._show_saved_feedback()

        new_scale = clamp_ui_scale(
            self.app.config.get("ui_scale", DEFAULT_UI_SCALE)
        )
        # Compare with the scale this process is actually running, not just
        # the saved value: declining the restart earlier must not suppress
        # the prompt forever.
        active_scale = getattr(
            self.app, "active_ui_scale", self._initial_ui_scale / 100.0
        )
        if (
            self.ui_scale_slider is not None
            and abs(new_scale - active_scale) > 1e-6
        ):
            self._prompt_scale_relaunch()

    def _show_saved_feedback(self):
        """Confirm the save in place, without closing the window."""
        button = self.save_button
        if button is None:
            return
        try:
            button.setEnabled(False)
            button.setText(_("✓ Saved"))
        except RuntimeError:
            return
        QtCore.QTimer.singleShot(1800, self._reset_save_button)

    def _reset_save_button(self):
        button = self.save_button
        if button is None:
            return
        try:
            button.setText(_("Save"))
            button.setEnabled(True)
        except RuntimeError:
            # Window was closed while the confirmation was showing.
            pass
