import logging

from PySide6 import QtCore, QtWidgets

from ui.UIUtils import UIUtils

_ = lambda x: x

class OnboardingWindow(QtWidgets.QWidget):
    # Closing signal
    close_signal = QtCore.Signal()

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.shortcut = 'ctrl+space'
        self.content_layout = None
        self.shortcut_input = None
        self.init_ui()
        self.self_close = False

    def init_ui(self):
        logging.debug('Initializing onboarding UI')
        self.setWindowTitle(_('Welcome to Writing Tools'))
        self.resize(560, 560)

        UIUtils.setup_window_and_layout(self)

        self.content_layout = QtWidgets.QVBoxLayout()
        self.content_layout.setContentsMargins(24, 22, 24, 22)
        self.content_layout.setSpacing(12)

        self.background.setLayout(self.content_layout)

        self.show_welcome_screen()

    def show_welcome_screen(self):
        UIUtils.clear_layout(self.content_layout)

        title_label = QtWidgets.QLabel(_("Welcome to Writing Tools")+"!")
        UIUtils.set_label_role(title_label, "title")
        self.content_layout.addWidget(title_label, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)

        features_text = (
            f"• {_('Instantly optimize your writing with AI by selecting your text and invoking Writing Tools with \"ctrl+space\", anywhere.')}\n\n"
            f"• {_('Get a summary you can chat with of articles, YouTube videos, or documents by selecting all text with \"ctrl+a\" (or select the YouTube transcript from its description), invoking Writing Tools, and choosing Summary.')}\n\n"
            f"• {_('Chat with AI anytime by invoking Writing Tools without selecting any text.')}\n\n"
            f"• {_('Lots of free tiers built in:')}\n"
            f"    – {_('Google Gemini + Gemma')}\n"
            f"    – {_('OpenRouter, Groq, Cerebras, Mistral, Together AI')}\n"
            f"    – {_('Hugging Face, Cohere, SambaNova, NVIDIA NIM')}\n"
            f"    – {_('Cloudflare Workers AI, Zhipu GLM')}\n"
            f"    – {_('Click \"Refresh available models\" in Settings to load the current model list for each provider')}\n"
            f"    – {_('LM Studio and Ollama (fully local & free)')}\n"
            f"    – {_('ANY OpenAI-compatible API, including OpenCode Console')}"
        )
        features_label = QtWidgets.QLabel(features_text)
        UIUtils.set_label_role(features_label, "muted")
        features_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        features_label.setWordWrap(True)
        self.content_layout.addWidget(features_label)

        shortcut_label = QtWidgets.QLabel("Customize your shortcut key (default: \"ctrl+space\"):")
        UIUtils.set_label_role(shortcut_label, "section")
        self.content_layout.addWidget(shortcut_label)

        self.shortcut_input = QtWidgets.QLineEdit(self.shortcut)
        self.content_layout.addWidget(self.shortcut_input)

        self.content_layout.addStretch()
        next_button = QtWidgets.QPushButton(_('Next'))
        UIUtils.set_variant(next_button, "primary")
        next_button.clicked.connect(self.on_next_clicked)
        self.content_layout.addWidget(next_button)

    def on_next_clicked(self):
        self.shortcut = self.shortcut_input.text().strip() or 'ctrl+space'
        logging.debug(f'User selected shortcut: {self.shortcut}')
        # Merge instead of replacing: keeps any migration flags / provider data
        # if onboarding is ever opened over an existing config.
        config = self.app.config if isinstance(self.app.config, dict) else {}
        config.update({
            'shortcut': self.shortcut,
            'locale': config.get('locale', 'en'),
        })
        self.app.config = config
        self.show_api_key_input()

    def show_api_key_input(self):
        self.app.show_settings(providers_only=True)
        self.self_close = True
        self.close()

    def closeEvent(self, event):
        # Emit the close signal
        if not self.self_close:
            self.close_signal.emit()
        super().closeEvent(event)
