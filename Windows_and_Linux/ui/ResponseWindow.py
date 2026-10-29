import html as html_lib
import logging

import markdown2
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QScrollArea

from ui.UIUtils import UIUtils

_ = lambda x: x

class MarkdownTextBrowser(QtWidgets.QTextBrowser):
    """Enhanced text browser for displaying Markdown content with improved sizing"""
    
    def __init__(self, parent=None, is_user_message=False):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenExternalLinks(True)
        self.zoom_factor = 1.2
        self.base_font_size = 13
        self.is_user_message = is_user_message
        
        # Critical: Remove scrollbars to prevent extra space
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # Set size policies to prevent unwanted expansion
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum
        )
        
        self._apply_zoom()
        
    def _apply_zoom(self):
        new_size = int(self.base_font_size * self.zoom_factor)
        palette = UIUtils.palette()
        
        # Browser chrome: background, border, padding and font size. Table
        # styling lives in the rendered HTML (see set_markdown_html), because
        # Qt's widget stylesheet does not style QTextDocument elements.
        self.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {('transparent' if self.is_user_message else palette['card'])};
                color: {palette['text']};
                border: {('none' if self.is_user_message else '1px solid ' + palette['border'])};
                border-radius: 6px;
                padding: 10px;
                margin: 0px;
                font-size: {new_size}px;
            }}
        """)

    def set_markdown_html(self, html):
        """Render Markdown HTML with document-level CSS for tables/code."""
        p = UIUtils.palette()
        css = (
            "<style>"
            "table { border-collapse: collapse; }"
            f"th, td {{ border: 1px solid {p['border']}; padding: 5px 8px; "
            "text-align: left; }}"
            f"th {{ background-color: {p['hover']}; font-weight: 600; }}"
            "pre { background-color: transparent; }"
            "</style>"
        )
        self.setHtml(css + html)
        
    def _update_size(self):
        """Wrap the document to the current viewport and fix the height."""
        scroll_area = self.get_scroll_area()
        if scroll_area is not None and scroll_area.batching:
            # The scroll area sizes every message in one pass; recalculating
            # here as well is what made resizing O(n²).
            return

        available_width = max(1, self.viewport().width() - 16)  # Account for padding
        self.document().setTextWidth(available_width)

        # Get precise content height
        doc_size = self.document().size()
        content_height = doc_size.height()

        # Add minimal padding for content
        new_height = int(content_height + 16)  # Reduced total padding

        if self.minimumHeight() != new_height:
            self.setMinimumHeight(new_height)
            self.setMaximumHeight(new_height)  # Force fixed height

            # Update scroll area if needed
            if scroll_area is not None:
                scroll_area.update_content_height()
                
    def wheelEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            # Get the main response window
            parent = self.parent()
            while parent and not isinstance(parent, ResponseWindow):
                parent = parent.parent()
                
            if parent:
                if delta > 0:
                    parent.zoom_all_messages('in')
                else:
                    parent.zoom_all_messages('out')
                event.accept()
        else:
            # Pass wheel events to parent for scrolling
            if self.parent():
                self.parent().wheelEvent(event)
            
    def zoom_in(self):
        old_factor = self.zoom_factor
        self.zoom_factor = min(3.0, self.zoom_factor * 1.1)
        if old_factor != self.zoom_factor:
            self._apply_zoom()
            self._update_size()
        
    def zoom_out(self):
        old_factor = self.zoom_factor
        self.zoom_factor = max(0.5, self.zoom_factor / 1.1)
        if old_factor != self.zoom_factor:
            self._apply_zoom()
            self._update_size()
        
    def reset_zoom(self):
        old_factor = self.zoom_factor
        self.zoom_factor = 1.2  # Reset to default zoom
        if old_factor != self.zoom_factor:
            self._apply_zoom()
            self._update_size()
    
    def get_scroll_area(self):
        """Find the parent ChatContentScrollArea"""
        parent = self.parent()
        while parent:
            if isinstance(parent, ChatContentScrollArea):
                return parent
            parent = parent.parent()
        return None
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_size()


class ChatContentScrollArea(QScrollArea):
    """Improved scrollable container for chat messages with dynamic sizing and proper spacing"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.content_widget = None
        self.layout = None
        self._batching = False
        self._last_resize_width = -1
        self._resize_timer = QtCore.QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(60)
        self._resize_timer.timeout.connect(self.refresh_message_widths)
        self.setup_ui()
        
    def setup_ui(self):
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        # Main container widget with explicit size policy
        self.content_widget = QtWidgets.QWidget()
        self.content_widget.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.MinimumExpanding
        )
        self.setWidget(self.content_widget)
        
        # Main layout with compact spacing
        self.layout = QtWidgets.QVBoxLayout(self.content_widget)
        self.layout.setSpacing(6)
        self.layout.setContentsMargins(12, 12, 12, 12)
        self.layout.addStretch()
        
        # Enhanced scroll area styling
        self.setStyleSheet("""
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background-color: transparent;
            }
            QScrollBar:vertical {
                background-color: transparent;
                width: 10px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: rgba(128, 128, 128, 0.45);
                min-height: 20px;
                border-radius: 3px;
                margin: 2px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

    def add_message(self, text, is_user=False):
        # Remove the bottom stretch, but never remove a real message if the
        # layout was rebuilt or is momentarily out of sync.
        if self.layout.count() > 0:
            last_item = self.layout.itemAt(self.layout.count() - 1)
            if last_item is not None and last_item.spacerItem() is not None:
                self.layout.takeAt(self.layout.count() - 1)

        text = "" if text is None else str(text)

        # Create message container with improved width
        msg_container = QtWidgets.QWidget()
        msg_container.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum
        )
        
        # Message layout with minimal margins
        msg_layout = QtWidgets.QVBoxLayout(msg_container)
        msg_layout.setContentsMargins(0, 0, 0, 0)
        msg_layout.setSpacing(0)
        
        # Create text display with updated width
        text_display = MarkdownTextBrowser(is_user_message=is_user)

        # Enable tables/strike extras in markdown2. Broken Markdown should
        # render as plain text, never crash the chat window.
        try:
            html = markdown2.markdown(text, extras=['tables', 'strike'])
        except Exception as exc:  # noqa: BLE001
            logging.error("Markdown rendering failed: %s", exc)
            html = html_lib.escape(text)
        text_display.set_markdown_html(html)

        # Calculate proper text display size using full width
        text_display.document().setTextWidth(max(1, self.width() - 20))
        doc_size = text_display.document().size()
        text_display.setMinimumHeight(int(doc_size.height() + 16))

        msg_layout.addWidget(text_display)

        self.layout.addWidget(msg_container)
        self.layout.addStretch()

        parent = self.parent()
        if parent is not None:
            try:
                parent.current_text_display = text_display
            except RuntimeError:
                pass


        QtCore.QTimer.singleShot(50, self.post_message_updates)
        
        return text_display

    def post_message_updates(self):
        """Handle updates after adding a message with proper timing"""
        self.scroll_to_bottom()
        if isinstance(self.parent(), ResponseWindow):
            self.parent()._adjust_window_height()

    def update_content_height(self):
        """Recalculate total content height with improved spacing calculation"""
        total_height = 0
        
        # Calculate height of all messages
        for i in range(self.layout.count() - 1):  # Skip stretch item
            item = self.layout.itemAt(i)
            if item and item.widget():
                widget_height = item.widget().sizeHint().height()
                total_height += widget_height
        
        # Add spacing between messages and margins
        total_height += (self.layout.spacing() * max(0, self.layout.count() - 2))
        total_height += self.layout.contentsMargins().top() + self.layout.contentsMargins().bottom()
        
        # Set minimum height with some padding
        self.content_widget.setMinimumHeight(total_height + 10)
        
        # Update window height if needed
        if isinstance(self.parent(), ResponseWindow):
            self.parent()._adjust_window_height()

    def scroll_to_bottom(self):
        """Smooth scroll to bottom of content"""
        vsb = self.verticalScrollBar()
        vsb.setValue(vsb.maximum())

    # -- bulk layout -----------------------------------------------------

    @property
    def batching(self) -> bool:
        """True while messages are being resized in one pass."""
        return self._batching

    def set_batching(self, active: bool):
        """Suppress per-message height recalculations during a bulk update."""
        self._batching = bool(active)

    def refresh_message_widths(self):
        """Re-wrap and resize every message once (after resize/zoom)."""
        available_width = max(1, self.width() - 40)  # Account for margins
        self._batching = True
        try:
            for i in range(max(0, self.layout.count() - 1)):  # Skip stretch item
                item = self.layout.itemAt(i)
                if item and item.widget():
                    container = item.widget()
                    if container.layout() is None or container.layout().itemAt(0) is None:
                        continue
                    text_display = container.layout().itemAt(0).widget()
                    if isinstance(text_display, MarkdownTextBrowser):
                        # Recalculate text width and height
                        text_display.document().setTextWidth(available_width)
                        doc_size = text_display.document().size()
                        text_display.setMinimumHeight(int(doc_size.height() + 20))
        finally:
            self._batching = False
        self.update_content_height()

    def resizeEvent(self, event):
        """Coalesce resize events; only the settled width triggers a re-wrap."""
        super().resizeEvent(event)
        width = self.width()
        if width == self._last_resize_width:
            return
        self._last_resize_width = width
        self._resize_timer.start()


class ResponseWindow(QtWidgets.QWidget):
    """Enhanced response window with improved sizing and zoom handling"""

    # Per-window signal: follow-up replies are delivered to the window that
    # asked the question, even if the user has opened another response window.
    followup_response_signal = QtCore.Signal(str)

    def __init__(self, app, title=_("Response"), parent=None):
        super().__init__(parent)
        self.app = app
        self.original_title = title
        self.setWindowTitle(title)
        self.option = title.replace(" Result", "")
        self.selected_text = None
        self.input_field = None
        self.loading_label = None
        self.loading_container = None
        self.chat_area = None
        self.chat_history = []
        self._closed = False

        # Setup thinking animation with full range of dots
        self.thinking_timer = QtCore.QTimer(self)
        self.thinking_timer.timeout.connect(self.update_thinking_dots)
        self.thinking_dots_state = 0
        self.thinking_dots = ["", ".", "..", "..."]  # Now properly includes all states
        self.thinking_timer.setInterval(300)

        self.init_ui()
        logging.debug('Connecting response signals')
        self.followup_response_signal.connect(self.handle_followup_response)
        logging.debug('Response signals connected')

        # Set initial size for "Thinking..." state
        initial_width = 480
        initial_height = 240
        self.resize(initial_width, initial_height)
                
    def init_ui(self):
        # Window setup with enhanced flags
        self.setWindowFlags(QtCore.Qt.WindowType.Window | 
                          QtCore.Qt.WindowType.WindowCloseButtonHint | 
                          QtCore.Qt.WindowType.WindowMinimizeButtonHint |
                          QtCore.Qt.WindowType.WindowMaximizeButtonHint)
        self.setMinimumSize(560, 360)
        
        # Main layout setup
        UIUtils.setup_window_and_layout(self)
        content_layout = QtWidgets.QVBoxLayout(self.background)
        content_layout.setContentsMargins(16, 14, 16, 14)
        content_layout.setSpacing(8)

        # Top bar with zoom controls
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setSpacing(4)
        
        title_label = QtWidgets.QLabel(self.option)
        title_label.setStyleSheet(
            f"font-size: 15px; font-weight: 600; "
            f"color: {UIUtils.palette()['text']};"
        )
        top_bar.addWidget(title_label)
        
        top_bar.addStretch()

        # Zoom label with matched size
        zoom_label = QtWidgets.QLabel("Zoom:")
        UIUtils.set_label_role(zoom_label, "muted")
        top_bar.addWidget(zoom_label)
        
        # Enhanced zoom controls with swapped order
        zoom_controls = [
            ('plus', 'Zoom In', lambda: self.zoom_all_messages('in')),
            ('minus', 'Zoom Out', lambda: self.zoom_all_messages('out')),
            ('reset', 'Reset Zoom', lambda: self.zoom_all_messages('reset'))
        ]
            
        for icon, tooltip, action in zoom_controls:
            btn = UIUtils.icon_button(icon, tooltip=tooltip, size=26)
            btn.clicked.connect(action)
            top_bar.addWidget(btn)
            
        content_layout.addLayout(top_bar)

        # Copy controls with matching text size
        copy_bar = QtWidgets.QHBoxLayout()
        copy_hint = QtWidgets.QLabel(_("Select to copy with formatting"))
        UIUtils.set_label_role(copy_hint, "muted")
        copy_bar.addWidget(copy_hint)
        copy_bar.addStretch()
        
        copy_md_btn = QtWidgets.QPushButton(_("Copy as Markdown"))
        UIUtils.set_variant(copy_md_btn, "secondary")
        copy_md_btn.clicked.connect(self.copy_first_response)  # Updated to only copy first response
        copy_bar.addWidget(copy_md_btn)
        content_layout.addLayout(copy_bar)

        # Loading indicator
        loading_container = QtWidgets.QWidget()
        loading_layout = QtWidgets.QHBoxLayout(loading_container)
        loading_layout.setContentsMargins(0, 0, 0, 0)
        
        self.loading_label = QtWidgets.QLabel(_("Thinking"))
        self.loading_label.setStyleSheet(
            f"QLabel {{ color: {UIUtils.palette()['muted']}; "
            f"font-size: 13px; padding: 10px; }}"
        )
        self.loading_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        
        loading_inner_container = QtWidgets.QWidget()
        loading_inner_container.setFixedWidth(160)
        loading_inner_layout = QtWidgets.QHBoxLayout(loading_inner_container)
        loading_inner_layout.setContentsMargins(0, 0, 0, 0)
        loading_inner_layout.addWidget(self.loading_label)
        
        loading_layout.addStretch()
        loading_layout.addWidget(loading_inner_container)
        loading_layout.addStretch()
        
        content_layout.addWidget(loading_container)
        self.loading_container = loading_container
        
        # Start thinking animation
        self.start_thinking_animation(initial=True)
        
        # Enhanced chat area with full width
        self.chat_area = ChatContentScrollArea()
        content_layout.addWidget(self.chat_area)
        
        # Input area with enhanced styling
        bottom_bar = QtWidgets.QHBoxLayout()
        bottom_bar.setSpacing(6)
        
        self.input_field = QtWidgets.QLineEdit()
        self.input_field.setPlaceholderText(_("Ask a follow-up question")+'...')
        self.input_field.returnPressed.connect(self.send_message)
        bottom_bar.addWidget(self.input_field)
        
        send_button = UIUtils.icon_button(
            "send", size=30, variant="primary", on_accent=True
        )
        send_button.clicked.connect(self.send_message)
        bottom_bar.addWidget(send_button)
        
        content_layout.addLayout(bottom_bar)

    # Method to get first response text
    def get_first_response_text(self):
        """Get the first model response text from chat history"""
        try:
            # Check chat history exists
            if not self.chat_history:
                return None
                
            # Find first assistant message
            for msg in self.chat_history:
                if msg["role"] == "assistant":
                    return msg["content"]
                    
            return None
        except Exception as e:
            logging.error(f"Error getting first response: {e}")
            return None

    def copy_first_response(self):
        """Copy only the first model response as Markdown"""
        response_text = self.get_first_response_text()
        if response_text:
            QtWidgets.QApplication.clipboard().setText(response_text)

    def update_thinking_dots(self):
        """Update the thinking animation dots with proper cycling"""
        self.thinking_dots_state = (self.thinking_dots_state + 1) % len(self.thinking_dots)
        dots = self.thinking_dots[self.thinking_dots_state]
        
        if self.loading_label.isVisible():
            self.loading_label.setText(_("Thinking")+f"{dots}")
        else:
            self.input_field.setPlaceholderText(_("Thinking")+f"{dots}")
    
    def start_thinking_animation(self, initial=False):
        """Start the thinking animation for either initial load or follow-up questions"""
        self.thinking_dots_state = 0
        
        if initial:
            self.loading_label.setText(_("Thinking"))
            self.loading_label.setVisible(True)
            self.loading_container.setVisible(True)
        else:
            self.input_field.setPlaceholderText(_("Thinking"))
            self.loading_container.setVisible(False)
            
        self.thinking_timer.start()

    def stop_thinking_animation(self):
        """Stop the thinking animation"""
        self.thinking_timer.stop()
        self.loading_container.hide()
        self.loading_label.hide()
        self.input_field.setPlaceholderText(_("Ask a follow-up question"))
        self.input_field.setEnabled(True)
        
        # Force layout update
        if self.layout():
            self.layout().invalidate()
            self.layout().activate()

    def zoom_all_messages(self, action='in'):
        """Apply a zoom action to every message in a single layout pass."""
        area = self.chat_area
        area.set_batching(True)
        try:
            for i in range(area.layout.count() - 1):  # Skip stretch item
                item = area.layout.itemAt(i)
                if item and item.widget():
                    text_display = item.widget().layout().itemAt(0).widget()
                    if isinstance(text_display, MarkdownTextBrowser):
                        if action == 'in':
                            text_display.zoom_in()
                        elif action == 'out':
                            text_display.zoom_out()
                        else:  # reset
                            text_display.reset_zoom()
        finally:
            area.set_batching(False)

        # Update widths/heights once after all fonts changed.
        area.refresh_message_widths()
        
    def _adjust_window_height(self):
        """Calculate and set the ideal window height"""
        # Skip adjustment if window already has a size
        if hasattr(self, '_size_initialized'):
            return
                
        try:
            # Get content widget height
            content_height = self.chat_area.content_widget.sizeHint().height()
                
            # Calculate other UI elements height
            ui_elements_height = (
                self.layout().contentsMargins().top() +
                self.layout().contentsMargins().bottom() +
                self.input_field.height() +
                self.layout().spacing() * 5 +
                170  # compact header/footer allowance
            )
                
            # Get screen constraints
            screen = QtWidgets.QApplication.screenAt(self.pos())
            if not screen:
                screen = QtWidgets.QApplication.primaryScreen()
                
            # Calculate maximum available height (85% of screen)
            max_height = int(screen.geometry().height() * 0.85)
                
            # Calculate desired height to show more content initially
            desired_content_height = int(content_height * 0.85)  # Show 85% of content
            desired_total_height = min(
                desired_content_height + ui_elements_height,
                max_height
            )
                
            # Set reasonable minimum height
            final_height = max(480, desired_total_height)
                
            # Standard compact window width
            final_width = 560
                
            # Update both width and height
            self.resize(final_width, final_height)
                
            # Center on screen
            frame_geometry = self.frameGeometry()
            screen_center = screen.geometry().center()
            frame_geometry.moveCenter(screen_center)
            self.move(frame_geometry.topLeft())
                
            # Mark size as initialized
            self._size_initialized = True
                
        except Exception as e:
            logging.error(f"Error adjusting window height: {e}")
            self.resize(560, 520)
            self._size_initialized = True

    @Slot(str)
    def set_text(self, text):
        """Set the initial response text."""
        if self._closed:
            return
        text = "" if text is None else str(text)
        if not text.strip():
            self.set_error("The AI provider returned an empty response.")
            return

        # Always ensure chat history is initialized properly
        self.chat_history = [
            {"role": "user", "content": f"{self.option}: {self.selected_text}"},
            {"role": "assistant", "content": text},  # Add initial response immediately
        ]

        self.stop_thinking_animation()
        text_display = self.chat_area.add_message(text)

        # Update zoom state (config is a dict, not an object).
        try:
            zoom = (self.app.config or {}).get("response_window_zoom")
            if isinstance(zoom, (int, float)) and 0.5 <= float(zoom) <= 3.0:
                text_display.zoom_factor = float(zoom)
                text_display._apply_zoom()
        except Exception as exc:  # noqa: BLE001
            logging.debug("Could not restore response zoom: %s", exc)

        QtCore.QTimer.singleShot(100, self._adjust_window_height)

    @Slot(str)
    def set_error(self, message):
        """Replace the 'Thinking…' state with a visible, non-stuck error."""
        if self._closed:
            return
        message = message or "An unknown error occurred."
        self.stop_thinking_animation()
        self.chat_area.add_message(f"⚠️ **Error**\n\n{message}")
        if not self.chat_history:
            self.chat_history = [{"role": "assistant", "content": message}]
        else:
            self.chat_history.append({"role": "assistant", "content": message})
        QtCore.QTimer.singleShot(100, self._adjust_window_height)

    def append_text(self, text):
        """Append streaming/extra text to the last assistant message."""
        if self._closed:
            return
        text = "" if text is None else str(text)
        if not text:
            return
        if self.chat_history and self.chat_history[-1].get("role") == "assistant":
            self.chat_history[-1]["content"] += text
            return
        self.chat_area.add_message(text)
        self.chat_history.append({"role": "assistant", "content": text})

    @Slot(str)
    def handle_followup_response(self, response_text):
        """Handle the follow-up response from the AI."""
        if self._closed:
            return
        if response_text:
            self.loading_label.setVisible(False)
            text_display = self.chat_area.add_message(response_text)

            # Maintain consistent zoom level
            if hasattr(self, "current_text_display"):
                text_display.zoom_factor = self.current_text_display.zoom_factor
                text_display._apply_zoom()

            if self.chat_history and self.chat_history[-1].get("role") != "assistant":
                self.chat_history.append({
                    "role": "assistant",
                    "content": response_text,
                })

        self.stop_thinking_animation()
        self.input_field.setEnabled(True)

        # Update window height
        QtCore.QTimer.singleShot(100, self._adjust_window_height)
        
    def send_message(self):
        """Send a new message/question"""
        if self._closed:
            return
        message = self.input_field.text().strip()
        if not message:
            return

        self.input_field.setEnabled(False)
        self.input_field.clear()
        
        # Add user message and maintain zoom level
        text_display = self.chat_area.add_message(message, is_user=True)
        if hasattr(self, 'current_text_display'):
            text_display.zoom_factor = self.current_text_display.zoom_factor
            text_display._apply_zoom()
        
        self.chat_history.append({"role": "user", "content": message})
        self.start_thinking_animation()
        self.app.process_followup_question(self, message)
        
    def closeEvent(self, event):
        """Handle window close event."""
        self._closed = True
        try:
            self.thinking_timer.stop()
            self.followup_response_signal.disconnect(self.handle_followup_response)
        except (RuntimeError, TypeError):
            pass

        # Save the zoom factor, but only when it actually changed: this runs
        # on every window close and used to rewrite config.json each time.
        try:
            if hasattr(self, "current_text_display") and isinstance(self.app.config, dict):
                zoom = float(self.current_text_display.zoom_factor)
                if self.app.config.get("response_window_zoom") != zoom:
                    self.app.config["response_window_zoom"] = zoom
                    self.app.save_config(self.app.config)
        except Exception as exc:  # noqa: BLE001
            logging.error("Could not save response zoom: %s", exc)

        self.chat_history = []
        if getattr(self.app, "current_response_window", None) is self:
            self.app.current_response_window = None

        super().closeEvent(event)
