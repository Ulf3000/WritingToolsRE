import json
import logging
from functools import partial

from pynput import keyboard as pykeyboard
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from apputils import data_path, read_json
from defaults import get_default_options
from ui.UIUtils import ThemeBackground, UIUtils

_ = lambda x: x


class ButtonEditDialog(QDialog):
    """
    Dialog for editing or creating a button's properties
    (name/title, system instruction, open_in_window, etc.).
    """
    def __init__(self, parent=None, button_data=None, title="Edit Button"):
        super().__init__(parent)
        self.button_data = button_data if button_data else {
            "prefix": "Make this change to the following text:\n\n",
            "instruction": "",
            "icon": "icons/magnifying-glass",
            "open_in_window": False
        }
        # The hotkey input is created in init_ui; tracked here so the
        # parent window can read it back from get_button_data().
        self.hotkey_input = None
        self.setWindowTitle(title)
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        # Name
        name_label = QLabel("Button Name:")
        UIUtils.set_label_role(name_label, "section")
        self.name_input = QLineEdit()
        if "name" in self.button_data:
            self.name_input.setText(self.button_data["name"])
        layout.addWidget(name_label)
        layout.addWidget(self.name_input)

        # Instruction (multiline)
        instruction_label = QLabel("What should your AI do with your selected text? (System Instruction)")
        UIUtils.set_label_role(instruction_label, "section")
        instruction_label.setWordWrap(True)
        self.instruction_input = QPlainTextEdit()
        self.instruction_input.setPlainText(self.button_data.get("instruction", ""))
        self.instruction_input.setMinimumHeight(96)
        self.instruction_input.setPlaceholderText("""Examples:
    - Fix / improve / explain this code.
    - Make it funny.
    - Add emojis!
    - Roast this!
    - Translate to English.
    - Make the text title case.
    - If it's all caps, make it all small, and vice-versa.
    - Write a reply to this.
    - Analyse potential biases in this news article.""")
        layout.addWidget(instruction_label)
        layout.addWidget(self.instruction_input)
        
        # open_in_window
        display_label = QLabel("How should your AI response be shown?")
        UIUtils.set_label_role(display_label, "section")
        layout.addWidget(display_label)
        
        radio_layout = QHBoxLayout()
        self.replace_radio = QRadioButton("Replace the selected text")
        self.window_radio = QRadioButton("In a pop-up window")
        self.replace_radio.setChecked(not self.button_data.get("open_in_window", False))
        self.window_radio.setChecked(self.button_data.get("open_in_window", False))
        radio_layout.addWidget(self.replace_radio)
        radio_layout.addWidget(self.window_radio)
        layout.addLayout(radio_layout)

        # Direct hotkey (optional)
        hotkey_label = QLabel("Direct hotkey (optional):")
        UIUtils.set_label_role(hotkey_label, "section")
        layout.addWidget(hotkey_label)

        self.hotkey_input = QLineEdit()
        self.hotkey_input.setPlaceholderText("e.g. ctrl+j  (leave blank for none)")
        self.hotkey_input.setText(self.button_data.get("hotkey", ""))
        layout.addWidget(self.hotkey_input)

        hotkey_hint = QLabel(
            "Press this combination from anywhere to run this button "
            "directly, skipping the popup. Use '+' between keys, e.g. "
            "ctrl+j or ctrl+shift+p."
        )
        UIUtils.set_label_role(hotkey_hint, "muted")
        hotkey_hint.setWordWrap(True)
        layout.addWidget(hotkey_hint)

        # OK & Cancel
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        ok_button = QPushButton("OK")
        cancel_button = QPushButton("Cancel")
        UIUtils.set_variant(ok_button, "primary")
        UIUtils.set_variant(cancel_button, "secondary")
        for btn in (ok_button, cancel_button):
            btn.setMinimumWidth(84)
        btn_layout.addWidget(ok_button)
        btn_layout.addWidget(cancel_button)
        layout.addLayout(btn_layout)
        
        ok_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        
        self.setStyleSheet(
            f"QDialog {{ background-color: {UIUtils.palette()['bg']}; }}"
        )
        self.setMinimumWidth(440)

    def get_button_data(self):
        data = {
            "name": self.name_input.text(),
            "prefix": "Make this change to the following text:\n\n",
            # Retrieve multiline text
            "instruction": self.instruction_input.toPlainText(),
            "icon": "icons/custom",
            "open_in_window": self.window_radio.isChecked()
        }
        # Only include `hotkey` if the user actually typed one. Old
        # configs and buttons-without-hotkeys stay shaped exactly as
        # before — no empty-string clutter in options.json.
        hotkey = self.hotkey_input.text().strip().lower() if self.hotkey_input else ""
        if hotkey:
            data["hotkey"] = hotkey
        return data

class DraggableButton(QtWidgets.QPushButton):
    def __init__(self, parent_popup, key, text):
        super().__init__(text, parent_popup)
        self.popup = parent_popup
        self.key = key
        self.drag_start_position = None
        self.setAcceptDrops(True)
        self.icon_container = None

        # Enable mouse tracking and hover events, and styled background
        self.setMouseTracking(True)
        self.setAttribute(QtCore.Qt.WA_Hover, True)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)

        # Use a dynamic property "hover" (default False)
        self.setProperty("hover", False)

        # Set fixed size (compact, standard toolbar-button size)
        self.setFixedSize(112, 36)

        # Neutral surface + hairline border, like a desktop list/toolbar item.
        palette = UIUtils.palette()
        self.base_style = f"""
            QPushButton {{
                background-color: {palette['card']};
                border: 1px solid {palette['border']};
                border-radius: 6px;
                padding: 6px 8px;
                font-size: 12px;
                text-align: left;
                color: {palette['text']};
            }}
            QPushButton[hover="true"] {{
                background-color: {palette['hover']};
                border-color: {palette['muted']};
            }}
        """
        self.setStyleSheet(self.base_style)
        logging.debug("DraggableButton initialized")

    # Edit mode gives each button a small top strip for the edit/delete
    # badges, so they never cover the label.
    EDIT_PADDING_STYLE = """
        QPushButton {
            padding-top: 21px;
            padding-bottom: 2px;
        }
    """

    def set_edit_mode_style(self, editing):
        """Grow the button in edit mode and reserve room for the badges."""
        target = (112, 48) if editing else (112, 36)
        if (self.width(), self.height()) != target:
            self.setFixedSize(*target)
        self.setStyleSheet(
            self.base_style + (self.EDIT_PADDING_STYLE if editing else "")
        )

    def enterEvent(self, event):
        # Only update the hover property if NOT in edit mode.
        if not self.popup.edit_mode:
            self.setProperty("hover", True)
            self.style().unpolish(self)
            self.style().polish(self)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self.popup.edit_mode:
            self.setProperty("hover", False)
            self.style().unpolish(self)
            self.style().polish(self)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            if self.popup.edit_mode:
                self.drag_start_position = event.pos()
                event.accept()
                return
        super().mousePressEvent(event)
            
    def mouseMoveEvent(self, event):
        if not (event.buttons() & QtCore.Qt.LeftButton) or not self.drag_start_position:
            return

        distance = (event.pos() - self.drag_start_position).manhattanLength()
        if distance < QtWidgets.QApplication.startDragDistance():
            return

        if self.popup.edit_mode:
            try:
                idx = self.popup.button_widgets.index(self)
            except ValueError:
                self.drag_start_position = None
                return
            drag = QtGui.QDrag(self)
            mime_data = QtCore.QMimeData()
            mime_data.setData("application/x-button-index", str(idx).encode())
            drag.setMimeData(mime_data)

            pixmap = self.grab()
            drag.setPixmap(pixmap)
            drag.setHotSpot(event.pos())

            self.drag_start_position = None
            drop_action = drag.exec_(QtCore.Qt.MoveAction)
            logging.debug(f"Drag completed with action: {drop_action}")

    def dragEnterEvent(self, event):
        if self.popup.edit_mode and event.mimeData().hasFormat("application/x-button-index"):
            event.acceptProposedAction()
            self.setStyleSheet(self.base_style + f"""
                QPushButton {{
                    border: 2px dashed {UIUtils.palette()['accent']};
                }}
            """)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self.base_style)
        event.accept()

    def dropEvent(self, event):
        if not self.popup.edit_mode or not event.mimeData().hasFormat("application/x-button-index"):
            event.ignore()
            return

        try:
            source_idx = int(
                event.mimeData().data("application/x-button-index").data().decode()
            )
            target_idx = self.popup.button_widgets.index(self)
        except (ValueError, TypeError, UnicodeDecodeError):
            event.ignore()
            return

        if 0 <= source_idx < len(self.popup.button_widgets) and source_idx != target_idx:
            bw = self.popup.button_widgets
            bw[source_idx], bw[target_idx] = bw[target_idx], bw[source_idx]
            self.popup.rebuild_grid_layout()
            self.popup.update_json_from_grid()

        self.setStyleSheet(self.base_style)
        event.setDropAction(QtCore.Qt.MoveAction)
        event.acceptProposedAction()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.icon_container:
            self.icon_container.setGeometry(0, 0, self.width(), self.height())

class CustomPopupWindow(QtWidgets.QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.edit_mode = False

        self.drag_label = None
        self.edit_button = None
        self.reset_button = None
        self.close_button = None
        self.custom_input = None
        self.input_area = None
        
        self.button_widgets = []

        logging.debug('Initializing CustomPopupWindow')
        self.init_ui()

    def init_ui(self):
        logging.debug('Setting up CustomPopupWindow UI')
        self.setWindowFlags(QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setWindowTitle("Writing Tools")
        
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0,0,0,0)
        
        self.background = ThemeBackground(
            self,
            is_popup=True,
            border_radius=8
        )
        main_layout.addWidget(self.background)
        
        content_layout = QtWidgets.QVBoxLayout(self.background)
        # Margin control
        content_layout.setContentsMargins(8, 4, 8, 8)
        content_layout.setSpacing(8)
        
        # TOP BAR LAYOUT & STYLE
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(0)

        # The "Edit"/"Done" button (left), same exact size as close button
        self.edit_button = UIUtils.icon_button("pencil", size=24)
        self.edit_button.clicked.connect(self.toggle_edit_mode)
        top_bar.addWidget(self.edit_button, 0, Qt.AlignLeft)

        # The label "Drag to rearrange" (edit mode only)
        self.drag_label = QLabel("Drag to rearrange")
        UIUtils.set_label_role(self.drag_label, "muted")
        self.drag_label.setAlignment(Qt.AlignCenter)
        self.drag_label.hide()
        top_bar.addWidget(self.drag_label, 1, Qt.AlignVCenter | Qt.AlignHCenter)

        # The "Reset" button (edit-mode only) - also 24x24
        self.reset_button = UIUtils.icon_button("restore", size=24)
        self.reset_button.setToolTip("Reset buttons to defaults")
        self.reset_button.clicked.connect(self.on_reset_clicked)
        self.reset_button.hide()
        top_bar.addWidget(self.reset_button, 0, Qt.AlignRight)

        # Close button block:
        self.close_button = QPushButton("×")
        self.close_button.setFixedSize(24, 24)
        UIUtils.set_variant(self.close_button, "icon")
        self.close_button.setStyleSheet(
            f"QPushButton {{ color: {UIUtils.palette()['text']}; "
            f"font-size: 15px; font-weight: 600; }}"
        )
        self.close_button.clicked.connect(self.close)
        top_bar.addWidget(self.close_button, 0, Qt.AlignRight)
        content_layout.addLayout(top_bar)

        
        # Input area (hidden in edit mode)
        self.input_area = QWidget()
        input_layout = QHBoxLayout(self.input_area)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(6)
        
        self.custom_input = QLineEdit()
        self.custom_input.setPlaceholderText(_("Describe your change..."))
        self.custom_input.returnPressed.connect(self.on_custom_change)
        input_layout.addWidget(self.custom_input)
        
        send_btn = UIUtils.icon_button(
            "send", size=30, variant="primary", on_accent=True
        )
        send_btn.clicked.connect(self.on_custom_change)
        input_layout.addWidget(send_btn)
        
        content_layout.addWidget(self.input_area)

        self.build_buttons_list()
        self.rebuild_grid_layout(content_layout)

        # show update notice if applicable
        if self.app.config.get("update_available", False):
            update_label = QLabel()
            update_label.setOpenExternalLinks(True)
            update_label.setText('<a href="https://github.com/theJayTea/WritingTools/releases" style="color:rgb(255, 0, 0); text-decoration: underline; font-weight: bold;">There\'s an update! :D Download now.</a>')
            update_label.setStyleSheet("margin-top: 10px;")
            content_layout.addWidget(update_label, alignment=QtCore.Qt.AlignCenter)
        
        logging.debug('CustomPopupWindow UI setup complete')
        self.installEventFilter(self)
        QtCore.QTimer.singleShot(250, lambda: self.custom_input.setFocus())

    @staticmethod
    def load_options():
        """Load options.json, falling back to the built-in defaults."""
        options_path = data_path('options.json')
        data = read_json(options_path)
        if not isinstance(data, dict) or not data:
            logging.warning('Options file missing/invalid; using defaults')
            return get_default_options()

        # Ignore malformed entries instead of crashing while building buttons.
        sanitized = {}
        for name, cfg in data.items():
            if not isinstance(name, str) or not isinstance(cfg, dict):
                continue
            if not isinstance(cfg.get('prefix'), str) or not isinstance(
                cfg.get('instruction'), str
            ):
                continue
            sanitized[name] = cfg
        return sanitized or get_default_options()

    def _live_options(self):
        """
        Options as currently held by the app.

        The app keeps the authoritative, sanitised dict in memory and already
        owns the file watcher, so the popup does not need to re-read
        options.json for every button rebuild or hotkey check. Falls back to
        disk only in tests / standalone use.
        """
        options = getattr(self.app, "options", None)
        if isinstance(options, dict) and options:
            return options
        return self.load_options()

    @staticmethod
    def save_options(options):
        """Atomically save options.json next to the executable."""
        try:
            self_app = QtWidgets.QApplication.instance()
            if self_app is not None and hasattr(self_app, 'save_options'):
                if self_app.save_options(options):
                    return
            # Fallback for tests / unusual startup paths.
            from apputils import atomic_write_json
            atomic_write_json(data_path('options.json'), options, indent=2)
        except Exception as exc:  # noqa: BLE001
            logging.error('Could not save options.json: %s', exc)

    def build_buttons_list(self):
        """
        Reads options.json, creates DraggableButton for each (except "Custom"),
        storing them in self.button_widgets in the same order as the JSON file.
        """
        self.button_widgets.clear()
        data = self._live_options()

        for key, value in data.items():
            if key == "Custom" or not isinstance(value, dict):
                continue
            button = DraggableButton(self, key, key)
            icon_relative = value.get("icon")
            if isinstance(icon_relative, str) and icon_relative:
                button.setIcon(UIUtils.icon(icon_relative))

            # Tooltip surfaces the direct hotkey (if any) for discoverability.
            hotkey = (value.get("hotkey") or "").strip()
            if hotkey:
                button.setToolTip(f"Direct hotkey: {hotkey}")

            if not self.edit_mode:
                button.clicked.connect(partial(self.on_generic_instruction, key))
            self.button_widgets.append(button)

    def _apply_options_and_rebuild(self):
        """Reload options.json and rebuild this popup — no restart needed."""
        try:
            self.app.apply_options_changes(refresh_popup=False)
        except Exception as exc:  # noqa: BLE001
            logging.error("Could not apply options changes live: %s", exc)
        self.build_buttons_list()
        self.rebuild_grid_layout()
        self._fit_on_screen()

    def refresh_from_options(self):
        """Rebuild from options.json after an external edit (watcher path)."""
        if not self.isVisible():
            # The next hotkey press constructs a fresh popup anyway.
            return
        self.build_buttons_list()
        self.rebuild_grid_layout()
        self._fit_on_screen()

    def _fit_on_screen(self):
        """Keep the popup fully visible after adding/removing buttons."""
        try:
            self.adjustSize()
        except RuntimeError:
            return
        if not self.isVisible():
            return
        screen = QtGui.QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        frame = self.frameGeometry()
        x = max(area.left(), min(frame.x(), area.right() - frame.width()))
        y = max(area.top(), min(frame.y(), area.bottom() - frame.height()))
        if (x, y) != (frame.x(), frame.y()):
            self.move(x, y)

    def rebuild_grid_layout(self, parent_layout=None):
        """Rebuild grid layout with consistent sizing and proper Add New button placement."""
        if not parent_layout:
            parent_layout = self.background.layout()

        # Remove the existing grid (and any stale buttons it still owns) plus
        # the old "+ Add New" button.
        for i in reversed(range(parent_layout.count())):
            item = parent_layout.itemAt(i)
            if isinstance(item, QtWidgets.QGridLayout):
                grid = item
                for j in reversed(range(grid.count())):
                    w = grid.itemAt(j).widget()
                    if w:
                        grid.removeWidget(w)
                        # Buttons replaced by a fresh list must be destroyed;
                        # otherwise the old copies float over the new grid.
                        if w not in self.button_widgets:
                            w.deleteLater()
                parent_layout.removeItem(grid)
                grid.deleteLater()
            elif (item.widget() and isinstance(item.widget(), QPushButton) 
                and item.widget().text() == "+ Add New"):
                item.widget().deleteLater()

        # Create new grid with fixed column width
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(8)
        grid.setColumnMinimumWidth(0, 112)
        grid.setColumnMinimumWidth(1, 112)
        
        # Add buttons to grid
        row = 0
        col = 0
        for b in self.button_widgets:
            grid.addWidget(b, row, col)
            col += 1
            if col > 1:
                col = 0
                row += 1

        # Buttons created by add/edit/delete have no edit overlays yet.
        if self.edit_mode:
            for b in self.button_widgets:
                b.set_edit_mode_style(True)
                if not getattr(b, "icon_container", None):
                    self.add_edit_delete_icons(b)

        parent_layout.addLayout(grid)
        
        # Add New button (only in edit mode)
        if self.edit_mode:
            add_btn = QPushButton("+ Add New")
            UIUtils.set_variant(add_btn, "secondary")
            add_btn.setStyleSheet(
                f"QPushButton {{ border: 1px dashed "
                f"{UIUtils.palette()['border']}; margin-top: 8px; }}"
            )
            add_btn.clicked.connect(self.add_new_button_clicked)
            parent_layout.addWidget(add_btn)

    def add_edit_delete_icons(self, btn):
        """Add edit/delete icons as overlays with proper spacing."""
        if hasattr(btn, 'icon_container') and btn.icon_container:
            btn.icon_container.deleteLater()
        
        btn.icon_container = QtWidgets.QWidget(btn)
        btn.icon_container.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)
        
        btn.icon_container.setGeometry(0, 0, btn.width(), btn.height())
        
        palette = UIUtils.palette()
        circle_style = f"""
            QPushButton {{
                background-color: {palette['card']};
                border: 1px solid {palette['border']};
                border-radius: 9px;
                min-width: 16px;
                min-height: 16px;
                max-width: 16px;
                max-height: 16px;
                padding: 1px;
                margin: 0px;
            }}
            QPushButton:hover {{
                background-color: {palette['hover']};
                border-color: {palette['muted']};
            }}
        """
        
        # Create edit icon (top-left strip)
        edit_btn = QPushButton(btn.icon_container)
        edit_btn.setGeometry(5, 2, 16, 16)
        edit_btn.setIcon(UIUtils.icon("pencil"))
        edit_btn.setStyleSheet(circle_style)
        edit_btn.clicked.connect(partial(self.edit_button_clicked, btn))
        edit_btn.show()
        
        # Create delete icon (top-right strip)
        delete_btn = QPushButton(btn.icon_container)
        delete_btn.setGeometry(btn.width() - 21, 2, 16, 16)
        delete_btn.setIcon(UIUtils.icon("cross"))
        delete_btn.setStyleSheet(circle_style)
        delete_btn.clicked.connect(partial(self.delete_button_clicked, btn))
        delete_btn.show()
        
        btn.icon_container.raise_()
        btn.icon_container.show()

    def toggle_edit_mode(self):
        """Toggle edit mode with improved button labels and state handling."""
        self.edit_mode = not self.edit_mode
        logging.debug(f'Edit mode toggled: {self.edit_mode}')

        if self.edit_mode:
            # Switch to edit mode: check icon, same compact size.
            icon_name = "check"
            self.edit_button.setText("")
            self.edit_button.setFixedSize(24, 24)
            UIUtils.set_variant(self.edit_button, "icon")
            # Hide close, show reset button & drag label
            self.close_button.hide()
            self.reset_button.show()
            self.drag_label.show()

        else:
            # Switch back to normal (non-edit) mode:
            icon_name = "pencil"
            self.edit_button.setText("")
            self.edit_button.setFixedSize(24, 24)
            UIUtils.set_variant(self.edit_button, "icon")
            # Show close, hide reset & drag label
            self.close_button.show()
            self.reset_button.hide()
            self.drag_label.hide()

            # Persist the current arrangement and apply it live. The next
            # click already uses the updated instructions — no relaunch.
            self.update_json_from_grid()
            try:
                self.app.apply_options_changes(refresh_popup=False)
            except Exception as exc:  # noqa: BLE001
                logging.error("Could not apply options changes live: %s", exc)


        # Update the edit button icon now that icon_name is defined
        self.edit_button.setIcon(UIUtils.icon(icon_name))

        # Toggle the main input area
        self.input_area.setVisible(not self.edit_mode)

        # Update button overlays
        for btn in self.button_widgets:
            try:
                btn.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass

            if not self.edit_mode:
                btn.set_edit_mode_style(False)
                btn.clicked.connect(partial(self.on_generic_instruction, btn.key))
                if hasattr(btn, 'icon_container') and btn.icon_container:
                    btn.icon_container.deleteLater()
                    btn.icon_container = None
            else:
                btn.set_edit_mode_style(True)
                self.add_edit_delete_icons(btn)

        # Rebuild grid layout
        self.rebuild_grid_layout()


    def on_reset_clicked(self):
        """Reset options.json to the built-in defaults and apply it live."""
        confirm_box = QtWidgets.QMessageBox(self)
        confirm_box.setWindowTitle("Reset Buttons to Defaults")
        confirm_box.setText(
            "Reset the buttons to their original configuration?\n\n"
            "Your custom buttons and instructions will be removed. "
            "This takes effect immediately."
        )
        confirm_box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        confirm_box.setDefaultButton(QtWidgets.QMessageBox.No)

        if confirm_box.exec_() != QtWidgets.QMessageBox.Yes:
            return

        try:
            logging.debug('Resetting to default options.json')
            self.save_options(get_default_options())
            self._apply_options_and_rebuild()
        except Exception as e:
            logging.error(f"Error resetting options.json: {e}")
            QtWidgets.QMessageBox.warning(
                self, "Error",
                f"An error occurred while resetting: {e}",
            )

    def _validate_hotkey(self, hotkey, exclude_button=None):
        """
        Check a button hotkey string for validity and conflicts.

        Returns (ok, error_message). Empty hotkey is always ok — the dialog
        omits the field on save, which means "no direct hotkey for this
        button". This is also how every legacy/old options.json entry
        looks, so absence is always safe.

        `exclude_button` skips the named button when checking conflicts —
        used during edit so a button doesn't conflict with its own
        previously-saved hotkey.
        """
        if not hotkey:
            return True, None

        # Authoritative format check: try parsing it the same way the
        # listener will. Catches typos, unknown key names, missing
        # modifiers, etc. without us having to maintain a regex.
        try:
            pykeyboard.HotKey.parse(self.app._to_pynput_hotkey(hotkey))
        except Exception as e:
            return False, (
                f"'{hotkey}' isn't a valid hotkey.\n\n"
                f"Use '+' between keys, e.g. ctrl+j or ctrl+shift+p.\n"
                f"({e})"
            )

        # Conflict with the global Writing Tools shortcut. Same combination
        # can't dispatch to both the popup and a direct fire.
        global_shortcut = (self.app.config.get('shortcut') or 'ctrl+space').strip().lower()
        if hotkey == global_shortcut:
            return False, (
                f"'{hotkey}' is already used as the main Writing Tools "
                f"hotkey (set in Settings). Pick a different combination."
            )

        # Conflict with another button's hotkey.
        data = self._live_options()
        for k, v in data.items():
            if k == exclude_button:
                continue
            other = (v.get('hotkey') or '').strip().lower()
            if other and other == hotkey:
                return False, (
                    f"'{hotkey}' is already used by the '{k}' button. "
                    f"Pick a different combination."
                )

        return True, None

    @staticmethod
    def _build_button_entry(bd, existing=None):
        """
        Assemble the options.json entry for a button from dialog output.
        Preserves any non-dialog fields already on the existing entry, and
        only writes `hotkey` when the user provided one (legacy-clean).
        """
        entry = dict(existing) if existing else {}
        entry["prefix"] = bd["prefix"]
        entry["instruction"] = bd["instruction"]
        entry["icon"] = bd["icon"]
        entry["open_in_window"] = bd["open_in_window"]
        if bd.get("hotkey"):
            entry["hotkey"] = bd["hotkey"]
        else:
            # User cleared the hotkey field — drop the key so re-saving
            # doesn't leave a stale binding behind.
            entry.pop("hotkey", None)
        return entry

    def add_new_button_clicked(self):
        dialog = ButtonEditDialog(self, title="Add New Button")
        while dialog.exec_():
            bd = dialog.get_button_data()
            ok, err = self._validate_hotkey(bd.get("hotkey", ""))
            if not ok:
                QtWidgets.QMessageBox.warning(self, "Invalid hotkey", err)
                # Re-open the dialog with the user's entries preserved so
                # they can fix the hotkey instead of starting over.
                continue
            data = dict(self._live_options())
            data[bd["name"]] = self._build_button_entry(bd)
            self.save_options(data)

            # Apply immediately: the button appears and its hotkey (if any)
            # becomes live without relaunching the app.
            self._apply_options_and_rebuild()
            return


    def edit_button_clicked(self, btn):
        """User clicked the small pencil icon over a button."""
        key = btn.key
        data = self._live_options()
        bd = dict(data[key])
        bd["name"] = key

        dialog = ButtonEditDialog(self, bd)
        while dialog.exec_():
            new_data = dialog.get_button_data()
            # Pass `exclude_button=key` so we don't flag the button's own
            # current hotkey as a conflict with itself.
            ok, err = self._validate_hotkey(new_data.get("hotkey", ""), exclude_button=key)
            if not ok:
                QtWidgets.QMessageBox.warning(self, "Invalid hotkey", err)
                continue
            data = dict(self._live_options())
            existing = data.get(key)
            if new_data["name"] != key:
                del data[key]
            data[new_data["name"]] = self._build_button_entry(new_data, existing=existing)
            self.save_options(data)

            self._apply_options_and_rebuild()
            return

    def delete_button_clicked(self, btn):
        """Delete a button and apply the change immediately."""
        key = btn.key
        confirm = QtWidgets.QMessageBox(self)
        confirm.setWindowTitle("Delete Button")
        confirm.setText(
            f"Delete the '{key}' button?\n\n"
            "You can restore the default buttons with the reset icon."
        )
        confirm.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        confirm.setDefaultButton(QtWidgets.QMessageBox.No)

        if confirm.exec_() != QtWidgets.QMessageBox.Yes:
            return

        try:
            data = dict(self._live_options())
            data.pop(key, None)
            self.save_options(data)

            # Clean up this button and rebuild in place — no relaunch.
            for btn_ in self.button_widgets[:]:
                if btn_.key == key:
                    if getattr(btn_, "icon_container", None):
                        btn_.icon_container.deleteLater()
                    btn_.deleteLater()
                    self.button_widgets.remove(btn_)

            self._apply_options_and_rebuild()
        except Exception as e:
            logging.error(f"Error deleting button: {e}")
            QtWidgets.QMessageBox.warning(
                self, "Error",
                f"An error occurred while deleting the button: {e}",
            )

    def update_json_from_grid(self):
        """
        Called after a drop reorder. Reflect the new order in options.json,
        so that user's custom arrangement persists.

        Does nothing when the order already matches, so leaving edit mode
        without dragging no longer rewrites the file.
        """
        data = self._live_options()
        new_data = {}
        if isinstance(data.get("Custom"), dict):
            new_data["Custom"] = data["Custom"]
        for button in self.button_widgets:
            if button.key in data:
                new_data[button.key] = data[button.key]
        if not new_data:
            return
        # "Custom" is stored out of the grid, so only its presence matters —
        # compare the actual button order. Without a drag it is unchanged and
        # the file is left alone.
        current_order = [key for key in data if key != "Custom"]
        new_order = [key for key in new_data if key != "Custom"]
        if new_order == current_order:
            return
        self.save_options(new_data)

    def on_custom_change(self):
        txt = self.custom_input.text().strip()
        if txt:
            self.app.process_option('Custom', txt)
            self.close()

    def on_generic_instruction(self, instruction):
        if not self.edit_mode:
            self.app.process_option(instruction)
            self.close()

    def eventFilter(self, obj, event):
        # Hide on deactivate only if NOT in edit mode
        if event.type()==QtCore.QEvent.WindowDeactivate:
            if not self.edit_mode:
                self.hide()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key()==QtCore.Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
