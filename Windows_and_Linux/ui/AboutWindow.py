from PySide6 import QtCore, QtGui, QtWidgets

from ui.UIUtils import UIUtils

_ = lambda x: x

class AboutWindow(QtWidgets.QWidget):
    """
    The about window for the application.
    """
    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        """
        Initialize the user interface for the about window.
        """
        self.setWindowTitle(' ') # Hack to hide the title bar text. TODO: Find a better solution later.
        self.setGeometry(300, 300, 560, 620)  # Compact standard window size

        # Center the window on the screen. I'm not aware of any methods in UIUtils to do this, so I'll be doing it manually.
        screen = QtWidgets.QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)

        UIUtils.setup_window_and_layout(self)

        # Disable minimize button and icon in title bar
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowMinimizeButtonHint & ~QtCore.Qt.WindowSystemMenuHint | QtCore.Qt.WindowCloseButtonHint | QtCore.Qt.WindowTitleHint)

        # Remove window icon. Has to be done after UIUtils.setup_window_and_layout().
        pixmap = QtGui.QPixmap(32, 32)
        pixmap.fill(QtCore.Qt.transparent)
        self.setWindowIcon(QtGui.QIcon(pixmap))

        content_layout = QtWidgets.QVBoxLayout(self.background)
        content_layout.setContentsMargins(24, 22, 24, 22)
        content_layout.setSpacing(14)

        title_label = QtWidgets.QLabel(_("About Writing Tools"))
        UIUtils.set_label_role(title_label, "title")
        content_layout.addWidget(title_label, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)

        about_text = "<p style='text-align: center;'>" + \
                _("Writing Tools is a free & lightweight desktop app that improves your writing with AI, similar to Apple's Apple Intelligence. It works with an extensive range of AI LLMs, both online and locally run.") + \
                """
                     <br>
                </p>
                <p style='text-align: center;'>""" + \
                "<b>" + _("Maintained by Ulf3000.") + "</b><br><br>" + \
                _("Source: ") + "<a href=\"https://github.com/Ulf3000/WritingToolsRE\"><b>github.com/Ulf3000/WritingToolsRE</b></a><br><br>" + \
                "<b>" + _("Writing Tools would not be where it is today without its <u>amazing</u> contributors") + ":</b><br>" + \
                "<b>1. <a href=\"https://github.com/momokrono\">momokrono</a>:</b><br>" + \
                _("Added Linux support, switched to the pynput API to improve Windows stability. Added Ollama API support, core logic for customizable buttons, and localization. Fixed misc. bugs and added graceful termination support by handling SIGINT signal.") + "<br>" + \
                "<b>2. <a href=\"https://github.com/CameronRedmore\">Cameron Redmore (CameronRedmore)</a>:</b><br>" + \
                _("Extensively refactored Writing Tools and added OpenAI Compatible API support, streamed responses, and the text generation mode when no text is selected.") + "<br>" + \
                '<b>3. <a href="https://github.com/Soszust40">Soszust40 (Soszust40)</a>:</b><br>' + \
                _('Helped add dark mode, tray menu fixes, and UI improvements.') + '</b><br>' + \
                '<b>4. <a href="https://github.com/arsaboo">Alok Saboo (arsaboo)</a>:</b><br>' + \
                _('Helped improve the reliability of text selection.') + '</b><br>' + \
                '<b>5. <a href="https://github.com/raghavdhingra24">raghavdhingra24</a>:</b><br>' + \
                _('Made the rounded corners anti-aliased & prettier.')+'</b><br>' + \
                '<b>6. <a href="https://github.com/ErrorCatDev">ErrorCatDev</a>:</b><br>' + \
                _('Significantly improved the About window, making it scrollable and cleaning things up. Also improved our .gitignore & requirements.txt.') + '</b><br>' + \
                '<b>7. <a href="https://github.com/Vadim-Karpenko">Vadim Karpenko</a>:</b><br>' + \
                _('Helped add the start-on-boot setting.')+ "</b><br><br>" + \
                """</p>
                <p style='text-align: center;'>
                <b>Version:</b> Win v10
                </p>
                <p />
                """

        about_label = QtWidgets.QLabel(about_text)
        about_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        about_label.setWordWrap(True)
        about_label.setOpenExternalLinks(True)  # Allow opening hyperlinks

        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidget(about_label)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll_area.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget { background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        scroll_area.viewport().setAutoFillBackground(False)

        content_layout.addWidget(scroll_area)

    def check_for_updates(self):
        """
        Open the GitHub releases page to check for updates.
        """
        webbrowser.open("https://github.com/theJayTea/WritingTools/releases")