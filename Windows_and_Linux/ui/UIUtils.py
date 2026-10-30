import os

from PySide6 import QtGui, QtCore, QtWidgets
from PySide6.QtGui import QImage, QPixmap

from apputils import resource_path

import darkdetect
try:
    colorMode = 'dark' if darkdetect.isDark() else 'light'
except Exception:  # darkdetect can fail on unusual platforms
    colorMode = 'light'


class NoWheelComboBox(QtWidgets.QComboBox):
    """
    A combo box that ignores the mouse wheel.

    Scrolling a settings page used to change the selected provider or model
    whenever the pointer happened to be over a dropdown. The wheel is now
    handed to the enclosing scroll area instead, so the page scrolls while the
    selection only changes on an explicit click.
    """

    def wheelEvent(self, event):  # noqa: N802 (Qt naming)
        area = self._scroll_area()
        if area is not None:
            QtWidgets.QApplication.sendEvent(area.viewport(), event)
            return
        event.ignore()

    def _scroll_area(self):
        """Return the closest enclosing scroll area, if there is one."""
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QtWidgets.QAbstractScrollArea):
                return parent
            parent = parent.parentWidget()
        return None


class NoteItemDelegate(QtWidgets.QStyledItemDelegate):
    """
    Draws a dropdown row as ``name ........ grey note``.

    The note lives in ``NOTE_ROLE`` so the item text stays the clean name
    (what the closed combo box shows), while the open list gets a tidy,
    right-aligned secondary column such as "free tier available".
    """

    NOTE_ROLE = int(QtCore.Qt.ItemDataRole.UserRole) + 1

    def paint(self, painter, option, index):
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        name = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget else QtWidgets.QApplication.style()
        style.drawControl(
            QtWidgets.QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget
        )

        palette = UIUtils.palette()
        rect = opt.rect.adjusted(8, 0, -8, 0)
        painter.save()
        painter.setPen(QtGui.QColor(palette["text"]))
        painter.drawText(
            rect,
            int(QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft),
            name,
        )
        note = index.data(self.NOTE_ROLE) or ""
        if note:
            small = QtGui.QFont(painter.font())
            small.setPointSizeF(max(small.pointSizeF() - 1.0, 7.0))
            painter.setFont(small)
            painter.setPen(QtGui.QColor(palette["muted"]))
            painter.drawText(
                rect,
                int(QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignRight),
                note,
            )
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 28))
        return size


class UIUtils:
    # Windows 11 Fluent-inspired palette. Neutral surfaces, one accent colour,
    # compact controls. The dark variant uses a light-blue accent with dark
    # text, matching Windows 11's dark-mode buttons.
    DARK = {
        "bg": "#202020",
        "card": "#2B2B2B",
        "input": "#2D2D2D",
        "text": "#F5F5F5",
        "muted": "#A8A8A8",
        "border": "#3D3D3D",
        "hover": "#323232",
        "selected": "#264A63",
        "accent": "#4CC2FF",
        "accent_hover": "#6FD0FF",
        "accent_pressed": "#3AA9E0",
        "accent_text": "#062E3F",
        "success": "#6CCB5F",
        "danger": "#FF99A4",
        "link": "#6CB8F6",
    }
    LIGHT = {
        "bg": "#F3F3F3",
        "card": "#FFFFFF",
        "input": "#FFFFFF",
        "text": "#1A1A1A",
        "muted": "#616161",
        "border": "#D6D6D6",
        "hover": "#F6F6F6",
        "selected": "#E5F1FB",
        "accent": "#0067C0",
        "accent_hover": "#1975C5",
        "accent_pressed": "#005A9E",
        "accent_text": "#FFFFFF",
        "success": "#0F7B0F",
        "danger": "#C42B1C",
        "link": "#0067C0",
    }

    @classmethod
    def palette(cls):
        return cls.DARK if colorMode == "dark" else cls.LIGHT

    # -- small helpers used across windows ------------------------------

    @classmethod
    def set_variant(cls, widget, variant):
        """Apply a QSS variant ("primary", "secondary", "icon") to a widget."""
        try:
            widget.setProperty("variant", variant)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        except RuntimeError:
            pass

    @classmethod
    def set_label_role(cls, label, role):
        """Apply a QSS role to a label: title/section/muted/success/error."""
        try:
            label.setProperty("role", role)
            label.style().unpolish(label)
            label.style().polish(label)
        except RuntimeError:
            pass

    @classmethod
    def apply_palette(cls, app):
        """Set Qt's native palette so links, dialogs and text match the app."""
        p = cls.palette()
        role = QtGui.QPalette.ColorRole
        palette = app.palette()
        colours = {
            role.Window: p["bg"],
            role.WindowText: p["text"],
            role.Base: p["input"],
            role.AlternateBase: p["card"],
            role.Text: p["text"],
            role.Button: p["card"],
            role.ButtonText: p["text"],
            role.BrightText: p["accent_text"],
            role.Highlight: p["accent"],
            role.HighlightedText: p["accent_text"],
            role.ToolTipBase: p["card"],
            role.ToolTipText: p["text"],
            role.PlaceholderText: p["muted"],
            role.Link: p["link"],
            role.Mid: p["border"],
            role.Dark: p["border"],
            role.Light: p["card"],
        }
        for colour_role, value in colours.items():
            palette.setColor(colour_role, QtGui.QColor(value))
        app.setPalette(palette)

    @classmethod
    def icon_path(cls, name: str, on_accent: bool = False) -> str:
        """
        Absolute path to a themed icon.

        Icons ship as `<name>_light.png` / `<name>_dark.png`. `on_accent`
        flips the contrast for glyphs drawn on an accent-coloured surface
        (light glyph in dark mode, dark glyph in light mode).
        """
        dark = colorMode == "dark"
        if on_accent:
            dark = not dark
        suffix = "dark" if dark else "light"
        name = name.replace("\\", "/").lstrip("/")
        if name.startswith("icons/"):
            # options.json stores icons as "icons/<name>" (without a suffix).
            name = name[len("icons/"):]
        filename = name if name.endswith((".png", ".ico")) else f"{name}_{suffix}.png"
        return resource_path(os.path.join("icons", filename))

    @classmethod
    def icon(cls, name: str, on_accent: bool = False) -> QtGui.QIcon:
        """QIcon for a themed icon; empty when the file is missing."""
        path = cls.icon_path(name, on_accent=on_accent)
        return QtGui.QIcon(path) if os.path.exists(path) else QtGui.QIcon()

    @classmethod
    def icon_button(cls, name: str, *, tooltip: str = "", size=24,
                    variant: str = "icon", on_accent: bool = False,
                    parent=None) -> QtWidgets.QPushButton:
        """Compact icon-only button shared by the popup and response window."""
        button = QtWidgets.QPushButton(parent)
        button.setIcon(cls.icon(name, on_accent=on_accent))
        button.setFixedSize(size, size)
        cls.set_variant(button, variant)
        if tooltip:
            button.setToolTip(tooltip)
        return button

    @classmethod
    def _chevron_path(cls) -> str:
        """Absolute QSS-friendly path to the current theme's combo arrow."""
        return cls.icon_path("chevron").replace("\\", "/")

    @classmethod
    def apply_app_theme(cls, app):
        """Apply the Fusion style, native palette and shared stylesheet."""
        try:
            app.setStyle("Fusion")
        except Exception:  # noqa: BLE001 - style is cosmetic
            pass
        cls.apply_palette(app)
        app.setStyleSheet(cls.app_stylesheet())

    @classmethod
    def app_stylesheet(cls) -> str:
        """A compact, desktop-native stylesheet for every window."""
        p = cls.palette()
        return f"""
            QWidget {{
                font-family: "Segoe UI Variable Text", "Segoe UI",
                             "SF Pro Text", "Inter", "Noto Sans", sans-serif;
                font-size: 13px;
                color: {p['text']};
            }}
            QLabel {{ background: transparent; }}
            QLabel[role="title"] {{
                font-size: 18px;
                font-weight: 600;
                color: {p['text']};
            }}
            QLabel[role="section"] {{
                font-size: 13px;
                font-weight: 600;
                color: {p['text']};
            }}
            QLabel[role="muted"] {{
                font-size: 12px;
                color: {p['muted']};
            }}
            QLabel[role="success"] {{
                font-size: 12px;
                color: {p['success']};
            }}
            QLabel[role="error"] {{
                font-size: 12px;
                color: {p['danger']};
            }}
            QLabel[role="link"] {{
                color: {p['link']};
            }}

            /* Buttons are neutral by default; only primary actions use the
               accent, like a standard desktop app. */
            QPushButton {{
                background-color: {p['card']};
                color: {p['text']};
                border: 1px solid {p['border']};
                border-radius: 4px;
                padding: 5px 12px;
                min-height: 20px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {p['hover']};
                border-color: {p['border']};
            }}
            QPushButton:pressed {{
                background-color: {p['selected']};
            }}
            QPushButton:disabled {{
                background-color: {p['bg']};
                border-color: {p['border']};
                color: {p['muted']};
            }}
            QPushButton[variant="primary"] {{
                background-color: {p['accent']};
                border-color: {p['accent']};
                color: {p['accent_text']};
                font-weight: 600;
            }}
            QPushButton[variant="primary"]:hover {{
                background-color: {p['accent_hover']};
                border-color: {p['accent_hover']};
            }}
            QPushButton[variant="primary"]:pressed {{
                background-color: {p['accent_pressed']};
                border-color: {p['accent_pressed']};
            }}
            QPushButton[variant="primary"]:disabled {{
                background-color: {p['border']};
                border-color: {p['border']};
                color: {p['muted']};
            }}
            QPushButton[variant="icon"] {{
                background: transparent;
                border: none;
                padding: 3px;
                min-height: 0;
            }}
            QPushButton[variant="icon"]:hover {{
                background-color: {p['hover']};
            }}
            QPushButton[variant="icon"]:pressed {{
                background-color: {p['selected']};
            }}

            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit,
            QPlainTextEdit {{
                background-color: {p['input']};
                color: {p['text']};
                border: 1px solid {p['border']};
                border-radius: 4px;
                padding: 5px 8px;
                min-height: 20px;
                font-size: 13px;
                selection-background-color: {p['accent']};
                selection-color: {p['accent_text']};
            }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
            QDoubleSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
                border-color: {p['accent']};
            }}
            QLineEdit:disabled, QComboBox:disabled {{
                color: {p['muted']};
                background-color: {p['bg']};
            }}
            QComboBox {{
                padding-right: 24px;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 22px;
                subcontrol-origin: padding;
                subcontrol-position: center right;
            }}
            QComboBox::down-arrow {{
                image: url("{cls._chevron_path()}");
                width: 9px;
                height: 9px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {p['card']};
                color: {p['text']};
                border: 1px solid {p['border']};
                border-radius: 4px;
                padding: 3px;
                selection-background-color: {p['selected']};
                selection-color: {p['text']};
                outline: 0;
            }}
            QCheckBox, QRadioButton {{
                spacing: 6px;
                font-size: 13px;
            }}
            QCheckBox::indicator, QRadioButton::indicator {{
                width: 16px;
                height: 16px;
            }}

            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: transparent;
                width: 10px;
                margin: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {p['border']};
                min-height: 24px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {p['muted']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 10px;
                margin: 2px;
            }}
            QScrollBar::handle:horizontal {{
                background: {p['border']};
                min-width: 24px;
                border-radius: 3px;
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0;
            }}

            QSlider::groove:horizontal {{
                height: 4px;
                border-radius: 2px;
                background: {p['border']};
            }}
            QSlider::sub-page:horizontal {{
                border-radius: 2px;
                background: {p['accent']};
            }}
            QSlider::handle:horizontal {{
                width: 14px;
                margin: -6px 0;
                border-radius: 7px;
                background: {p['accent']};
            }}
            QSlider::handle:horizontal:hover {{ background: {p['accent_hover']}; }}

            QToolTip {{
                background-color: {p['card']};
                color: {p['text']};
                border: 1px solid {p['border']};
                padding: 4px 7px;
            }}
            QMenu {{
                background-color: {p['card']};
                color: {p['text']};
                border: 1px solid {p['border']};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{ padding: 6px 22px 6px 18px; border-radius: 3px; }}
            QMenu::item:selected {{
                background-color: {p['selected']};
                color: {p['text']};
            }}
            QMenu::separator {{
                height: 1px;
                background: {p['border']};
                margin: 4px 8px;
            }}
            QMessageBox {{ background-color: {p['card']}; }}
            QMessageBox QLabel {{ color: {p['text']}; }}
            QFrame[frameShape="4"], QFrame[frameShape="5"] {{
                color: {p['border']};
            }}
        """

    @classmethod
    def clear_layout(cls, layout):
        """
        Clear the layout of all widgets. Spacer items and already-deleted
        widgets are handled gracefully.
        """
        if layout is None:
            return
        while True:
            child = layout.takeAt(0)
            if child is None:
                break
            try:
                child_layout = child.layout()
                if child_layout is not None:
                    cls.clear_layout(child_layout)
                    child_layout.deleteLater()
                    continue
                widget = child.widget()
                if widget is not None:
                    widget.deleteLater()
            except RuntimeError:
                # Widget/layout was deleted by Qt before we got here.
                continue

    @classmethod
    def resize_and_round_image(cls, image, image_size = 100, rounding_amount = 50):
        image = image.scaledToWidth(image_size)
        clipPath = QtGui.QPainterPath()
        clipPath.addRoundedRect(0, 0, image_size, image_size, rounding_amount, rounding_amount)
        target = QImage(image_size, image_size, QImage.Format_ARGB32)
        target.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(target)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setClipPath(clipPath)
        painter.drawImage(0, 0, image)
        painter.end()
        targetPixmap = QPixmap.fromImage(target)
        return targetPixmap

    @classmethod
    def setup_window_and_layout(cls, base: QtWidgets.QWidget):
        # Set the window icon
        icon_path = resource_path(os.path.join('icons', 'app_icon.png'))
        if os.path.exists(icon_path):
            base.setWindowIcon(QtGui.QIcon(icon_path))
        main_layout = QtWidgets.QVBoxLayout(base)
        main_layout.setContentsMargins(0, 0, 0, 0)
        base.background = ThemeBackground(base)
        main_layout.addWidget(base.background)


class ThemeBackground(QtWidgets.QWidget):
    """
    Draws the window background.

    A soft, neutral tint (like Windows 11 Mica) instead of a photographic
    wallpaper, so every window reads as a standard desktop app. Popups paint
    on the elevated card colour with a hairline border.
    """
    def __init__(self, parent=None, is_popup=False, border_radius=0):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.is_popup = is_popup
        self.border_radius = border_radius

    def paintEvent(self, event):
        """Paint a softly tinted surface with an optional border."""
        palette = UIUtils.palette()
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        base = QtGui.QColor(
            palette["card"] if self.is_popup else palette["bg"]
        )
        path = QtGui.QPainterPath()
        path.addRoundedRect(
            0, 0, self.width(), self.height(),
            self.border_radius, self.border_radius,
        )
        painter.setClipPath(path)

        if colorMode == 'dark':
            top = base.lighter(108)
            bottom = base.darker(106)
        else:
            top = base.lighter(103)
            bottom = base.darker(102)
        gradient = QtGui.QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.fillPath(path, gradient)

        # A hairline edge is what makes a frameless popup look like a window.
        if self.border_radius:
            pen = QtGui.QPen(QtGui.QColor(palette["border"]))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1),
                self.border_radius, self.border_radius,
            )

