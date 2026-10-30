"""
AI Provider Architecture for Writing Tools
--------------------------------------------

This module handles every supported AI provider and manages interactions
with the main application. It uses an abstract base class pattern, so adding
another OpenAI-compatible service only takes a small subclass.

Key Components:
1. AIProviderSetting – Base class for provider settings (e.g. API keys, model names)
    • TextSetting      – A simple text input for settings
    • DropdownSetting  – A dropdown selection setting (with optional custom value)

2. AIProvider – Abstract base class that all providers implement.
   It defines the interface for:
      • Getting a response from the AI model
      • Loading and saving configuration settings
      • Cancelling an ongoing request

3. Provider Implementations:
    • GeminiProvider             – Google's Generative AI API (Gemini / Gemma)
    • OpenAICompatibleProvider   – Any OpenAI-compatible API
    • OllamaProvider             – Local Ollama server
    • PresetOpenAIProvider       – Shared base for the curated free-tier services
      - OpenRouter, Groq, Mistral, Cloudflare Workers AI, Zhipu GLM,
        OpenCode Console, OpenAI, Anthropic, DeepSeek, xAI, LM Studio
    • Every provider can fetch its live model list from Settings, so stale
      hard-coded model IDs do not silently break requests.

Response flow:
   • The main app calls get_response() with a system instruction and a prompt.
   • For operations that are shown in a window (Summary, Key Points, Table),
     the provider returns the full response text.
   • For direct text replacement, the provider emits the text through
     `output_ready_signal` and returns an empty string.
   • Conversation history for follow-up questions is passed as a list of
     OpenAI-style message dictionaries.

All providers are non-streaming. Network errors, missing keys, rate limits and
malformed responses are surfaced as friendly dialogs instead of uncaught
exceptions (a major source of "it randomly closed" reports).
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.error
import urllib.request
import webbrowser
from abc import ABC, abstractmethod
from typing import List, Optional

# External libraries
from google import genai
from google.genai import types as genai_types
from ollama import Client as OllamaClient
from openai import OpenAI
from PySide6 import QtCore, QtWidgets
from PySide6.QtWidgets import QVBoxLayout

from apputils import clean_model_text, normalize_base_url
from ui.UIUtils import NoWheelComboBox, UIUtils

# Obfuscation prefix to identify encrypted API keys
_OBFUSCATION_PREFIX = "enc:"
_XOR_KEY = 0x5A  # Simple XOR key for obfuscation

# Some gateways sit behind Cloudflare, which rejects Python's default
# User-Agent with "error 1010" before the request ever reaches the API.
# A regular desktop-browser UA keeps the catalogue and API reachable.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def obfuscate_api_key(key: str) -> str:
    """
    Obfuscate an API key using XOR + Base64 encoding.
    Returns the obfuscated string with an 'enc:' prefix.
    """
    if not key or not isinstance(key, str) or key.startswith(_OBFUSCATION_PREFIX):
        return key or ""  # Already obfuscated or empty
    try:
        xored = bytes([b ^ _XOR_KEY for b in key.encode("utf-8")])
        return _OBFUSCATION_PREFIX + base64.b64encode(xored).decode("ascii")
    except (UnicodeError, ValueError) as exc:
        logging.error("Could not obfuscate API key: %s", exc)
        return key


def deobfuscate_api_key(obfuscated: str) -> str:
    """
    Deobfuscate an API key that was obfuscated with obfuscate_api_key().
    If the key doesn't have the 'enc:' prefix, returns it as-is (plaintext).

    Corrupt/legacy values are returned unchanged instead of raising — a bad
    config entry should never prevent the app from starting.
    """
    if not obfuscated or not isinstance(obfuscated, str):
        return ""
    if not obfuscated.startswith(_OBFUSCATION_PREFIX):
        return obfuscated  # Not obfuscated, return as-is
    encoded = obfuscated[len(_OBFUSCATION_PREFIX):]
    try:
        xored = base64.b64decode(encoded, validate=True)
        return bytes([b ^ _XOR_KEY for b in xored]).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        logging.error("Could not deobfuscate API key (%s); using raw value", exc)
        return obfuscated


class ModelFetchError(RuntimeError):
    """Raised when a live model list cannot be retrieved.

    The message is already user-facing (it is shown in the Settings window).
    """


def _http_get_json(url: str, headers: dict | None = None,
                   timeout: float = 25.0):
    """GET a JSON document with friendly, non-crashing error handling."""
    request_headers = {"User-Agent": BROWSER_USER_AGENT}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError:
        raise
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ModelFetchError(f"Connection failed: {reason}") from exc
    except OSError as exc:
        raise ModelFetchError(f"Connection failed: {exc}") from exc

    text = raw.decode(charset, errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelFetchError(
            "The provider returned a non-JSON response. Check the API base URL."
        ) from exc


def _format_context_size(length) -> str:
    """Turn a context length into a compact label such as '128K ctx'."""
    try:
        length = int(length)
    except (TypeError, ValueError):
        return ""
    if length <= 0:
        return ""
    if length >= 1_000_000:
        return f"{length / 1_000_000:.1f}M ctx".replace(".0M", "M")
    if length >= 1_000:
        return f"{round(length / 1_000)}K ctx"
    return f"{length} ctx"


def _format_token_count(value, binary: bool = False) -> str:
    """Format a token limit for the model-details panel."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    million = 1024 ** 2 if binary else 1_000_000
    thousand = 1024 if binary else 1_000
    if value >= million:
        return f"{value / million:.1f}M".replace(".0M", "M")
    if value >= thousand:
        return f"{round(value / thousand)}K"
    return str(value)


def _normalize_model_spec(spec) -> dict:
    """Return a JSON-safe model spec with only the fields we understand."""
    if not isinstance(spec, dict):
        return {}
    result = {}

    for key in ("context", "max_output"):
        try:
            value = int(spec.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            result[key] = value

    caps = spec.get("capabilities")
    if caps:
        if isinstance(caps, str):
            caps = [caps]
        cleaned = []
        for cap in caps:
            cap = str(cap).strip().lower()
            if cap and cap not in cleaned:
                cleaned.append(cap)
        if cleaned:
            result["capabilities"] = cleaned

    levels = spec.get("thinking_levels")
    if levels:
        if isinstance(levels, str):
            levels = [levels]
        cleaned = []
        for level in levels:
            level = str(level).strip().lower()
            if level and level not in cleaned:
                cleaned.append(level)
        if cleaned:
            result["thinking_levels"] = cleaned

    note = spec.get("note")
    if note:
        result["note"] = str(note)[:200]
    return result


def _spec_summary(spec: dict) -> str:
    """A compact, human-readable one-line description of a model spec."""
    spec = spec or {}
    parts = []
    context = _format_token_count(spec.get("context"))
    if context:
        parts.append(f"{context} context")
    max_output = _format_token_count(spec.get("max_output"), binary=True)
    if max_output:
        parts.append(f"{max_output} max output")

    caps = [str(cap) for cap in (spec.get("capabilities") or [])]
    if caps:
        parts.append(", ".join(caps))

    levels = spec.get("thinking_levels") or []
    if levels:
        parts.append("thinking: " + "/".join(levels))
    note = spec.get("note")
    if note:
        parts.append(note)
    return " · ".join(parts)


def _looks_like_thinking_level_error(exc: Exception) -> bool:
    """True when the API rejected the requested thinking level/budget."""
    text = str(exc).lower()
    if "thinking" not in text and "thought" not in text:
        return False
    return any(token in text for token in (
        "level", "invalid", "unsupported", "not supported", "not available",
        "unknown", "budget",
    ))


def _is_transient_error(exc: Exception) -> bool:
    """
    True for provider-side hiccups that are worth retrying immediately.

    Google and OpenRouter intermittently answer 500/503/504 for a model that
    works a moment later. Quota, auth, model and validation errors are never
    retried — retrying those just wastes the user's time.
    """
    text = str(exc).lower()
    if any(token in text for token in (
        "429", "resource_exhausted", "quota", "rate limit", "too many requests",
    )):
        return False
    name = type(exc).__name__.lower()
    if name in (
        "internalservererror", "apiconnectionerror", "apitimeouterror",
        "servererror", "serviceunavailableerror",
    ):
        return True
    return any(token in text for token in (
        "error code: 500", "error code: 502", "error code: 503",
        "error code: 504", "internal error", "internal server error",
        "unavailable", "overloaded", "temporarily", "deadline",
        "connection reset", "connection aborted", "remote end closed",
        "server disconnected", "timed out",
    ))


def _system_instruction_unsupported(exc: Exception) -> bool:
    """True when the model refuses a separate system/developer instruction."""
    text = str(exc).lower()
    return (
        "developer instruction is not enabled" in text
        or "system instruction is not supported" in text
        or "system_instruction is not supported" in text
        or "system instruction is not enabled" in text
    )


def _retired_model_error(exc: Exception) -> bool:
    """True when the provider has retired the requested model."""
    text = str(exc).lower()
    if "model" not in text:
        return False
    return any(token in text for token in (
        "no longer available", "decommissioned", "has been retired",
        "model has been retired", "retired model",
    ))


def _missing_model_error(exc: Exception) -> bool:
    """True when the requested model id does not exist for this provider."""
    if _retired_model_error(exc):
        return True
    text = str(exc).lower()
    return any(token in text for token in (
        "no endpoints found", "not a valid model", "model not found",
        "unknown model", "no such model", "does not exist",
    ))


def _friendly_error(exc: Exception, provider_name: str) -> tuple[str, str]:
    """Turn an SDK exception into a title + human-readable message."""
    text = str(exc) or exc.__class__.__name__
    lower = text.lower()

    if any(token in lower for token in (
        "model not found", "model_not_found", "unknown model",
        "invalid model", "no such model", "decommissioned",
        "model has been retired", "does not exist",
    )) or ("404" in lower and "model" in lower):
        return (
            f"{provider_name} – Model Not Available",
            "The selected model is no longer offered by this provider.\n\n"
            "Open Settings, choose this provider, then click "
            "\"Refresh available models\" and select a current model.",
        )
    if any(token in lower for token in (
        "api key", "unauthorized", "authentication", "invalid key",
        "401", "permission", "credential",
    )):
        return (
            f"{provider_name} – API Key Problem",
            "The API key was rejected or is missing.\n\n"
            "Open Settings, paste a valid key for this provider, then try again.",
        )
    if any(token in lower for token in (
        "rate limit", "quota", "429", "resource has been exhausted",
        "too many requests", "free tier", "insufficient",
    )):
        if ("openrouter" in provider_name.lower()
                and "provider returned error" in lower):
            return (
                f"{provider_name} – Free Model Busy",
                "This free model's upstream provider is temporarily busy or "
                "rate-limited (the error comes from OpenRouter, not from "
                "Writing Tools).\n\n"
                "Try again in a minute, pick another free model, or choose "
                "\"Free Models Router\", which picks a working one for you.",
            )
        return (
            f"{provider_name} – Free Tier Limit Reached",
            "This provider's free quota or rate limit has been reached.\n\n"
            "Wait a little, switch models, or pick another free provider in Settings.",
        )
    if any(token in lower for token in (
        "error code: 500", "error code: 502", "error code: 503",
        "error code: 504", "internal error", "internal server error",
        "unavailable", "overloaded", "temporarily",
    )):
        return (
            f"{provider_name} – Provider Temporarily Unavailable",
            "The provider's server had a temporary problem (this happens on "
            "their side).\n\n"
            "Writing Tools already retried automatically. Wait a moment and try "
            "again, or switch to another model or provider in Settings.",
        )
    if any(token in lower for token in ("timeout", "timed out", "deadline")):
        return (
            f"{provider_name} – Request Timed Out",
            "The provider took too long to answer. Check your internet connection "
            "and try again.",
        )
    if "only supports interactions api" in lower:
        return (
            f"{provider_name} – Model Not Usable for Chat",
            "This model is only available through Google's separate Interactions "
            "API, so it cannot answer normal chat requests.\n\n"
            "Pick a Gemini Flash, Flash Lite or Pro model in Settings.",
        )
    if "thinking" in lower and any(
        token in lower for token in ("level", "invalid", "unsupported", "budget")
    ):
        return (
            f"{provider_name} – Thinking Option Not Supported",
            "This model does not support the selected thinking level.\n\n"
            "Open Settings, choose this provider, and set Thinking Level to "
            "\"Auto\" (or pick a level listed in the model details panel).",
        )
    if any(token in lower for token in (
        "context length", "context window", "too many tokens",
        "maximum context", "token limit",
    )):
        return (
            f"{provider_name} – Text Too Long",
            "The selected text is longer than this model's context window.\n\n"
            "Try a model with a larger context, or split the text into smaller "
            "parts.",
        )
    if any(token in lower for token in (
        "connection", "network", "getaddrinfo", "ssl", "certificate",
        "proxy", "unreachable", "name resolution",
    )):
        return (
            f"{provider_name} – Connection Problem",
            "Could not reach the provider. Check your internet connection "
            "(or the API Base URL for local/custom providers) and try again.",
        )
    return (
        f"{provider_name} – Request Failed",
        f"The provider returned an error:\n\n{text[:700]}",
    )


def normalize_tier_filter(value) -> str:
    """Return a valid Free/Paid/All model filter value ("all" by default)."""
    value = str(value or "all").strip().lower()
    return value if value in ("all", "free", "paid") else "all"


class AIProviderSetting(ABC):
    """Abstract base class for a provider setting (e.g., API key, model)."""

    def __init__(self, name: str, display_name: str = None,
                 default_value: str = None, description: str = None):
        self.name = name
        self.display_name = display_name if display_name else name
        self.default_value = default_value if default_value else ""
        self.description = description if description else ""

    @abstractmethod
    def render_to_layout(self, layout: QVBoxLayout):
        """Render the setting widget(s) into the provided layout."""
        pass

    @abstractmethod
    def set_value(self, value):
        """Set the internal value from configuration."""
        pass

    @abstractmethod
    def get_value(self):
        """Return the current value from the widget."""
        pass

    def release_ui(self):
        """
        Forget Qt widget references before the settings layout is rebuilt.

        Widgets are deleted with ``deleteLater()``; keeping a stale Python
        reference to a deleted C++ object raises RuntimeError on the next
        access, which used to break provider switching.
        """
        pass


class TextSetting(AIProviderSetting):
    """A text-based setting (API keys, URLs, model names, etc.)."""

    def __init__(self, name: str, display_name: str = None,
                 default_value: str = None, description: str = None):
        super().__init__(name, display_name, default_value, description)
        self.internal_value = default_value or ""
        self.input = None

    def render_to_layout(self, layout: QVBoxLayout):
        row_layout = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel(self.display_name)
        UIUtils.set_label_role(label, "section")
        row_layout.addWidget(label)
        self.input = QtWidgets.QLineEdit(str(self.internal_value or ""))
        self.input.setPlaceholderText(self.description)
        # API keys are secret-ish: hide them unless the user is typing.
        if "key" in self.name.lower() or "token" in self.name.lower():
            self.input.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        row_layout.addWidget(self.input)
        layout.addLayout(row_layout)

    def set_value(self, value):
        self.internal_value = value if value is not None else ""
        if self.input is not None:
            self.input.setText(str(self.internal_value))

    def get_value(self):
        if self.input is not None:
            return self.input.text()
        return str(self.internal_value or "")

    def release_ui(self):
        self.input = None


class DropdownSetting(AIProviderSetting):
    """
    A dropdown setting (e.g., for selecting a model).

    Optionally supports a "Custom" option that reveals a text input for
    arbitrary values. When allow_custom=True, users can select "Custom" from
    the dropdown and enter any value. If the loaded config value doesn't match
    any preset option, "Custom" is auto-selected.
    """

    # Sentinel value used internally to identify the "Custom" dropdown option
    _CUSTOM_SENTINEL = "__custom__"

    def __init__(self, name: str, display_name: str = None,
                 default_value: str = None, description: str = None,
                 options: list = None, allow_custom: bool = False,
                 custom_placeholder: str = "Enter custom value",
                 provider=None):
        super().__init__(name, display_name, default_value, description)
        self.options = options if options else []
        self.full_options = list(self.options)
        self.internal_value = default_value or ""
        self.dropdown = None
        self.allow_custom = allow_custom
        self.custom_placeholder = custom_placeholder
        self.custom_input = None
        self.custom_input_container = None
        # The owning provider supplies both the free/paid classification of
        # a model and the user's Free/Paid/All choice, so every provider
        # keeps its own filter.
        self.provider = provider

    # Shown when the current filter leaves nothing to choose from.
    _EMPTY_TEXT = "No models match this filter"

    def render_to_layout(self, layout: QVBoxLayout):
        row_layout = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel(self.display_name)
        UIUtils.set_label_role(label, "section")
        row_layout.addWidget(label)
        self.dropdown = NoWheelComboBox()
        row_layout.addWidget(self.dropdown)
        layout.addLayout(row_layout)

        if self.allow_custom:
            self.custom_input_container = QtWidgets.QWidget()
            custom_row_layout = QtWidgets.QHBoxLayout(self.custom_input_container)
            custom_row_layout.setContentsMargins(0, 5, 0, 0)

            self.custom_input = QtWidgets.QLineEdit()
            self.custom_input.setPlaceholderText(self.custom_placeholder)
            custom_row_layout.addWidget(self.custom_input)
            layout.addWidget(self.custom_input_container)

        self._populate_dropdown()
        self.dropdown.currentIndexChanged.connect(self._on_dropdown_changed)
        self._update_custom_input_visibility()

    # -- dropdown contents (one place for every rebuild) -------------------

    def _populate_dropdown(self):
        """Rebuild the items for the current filter and select the value."""
        if self.dropdown is None:
            return
        filtered = self._filtered_options()
        self.dropdown.blockSignals(True)
        try:
            self.dropdown.clear()
            for label, value in filtered:
                self.dropdown.addItem(label, value)
            if not filtered:
                # Strict filter: this provider has no model of that kind.
                # Say so instead of quietly showing a model from the other
                # tier, which would look like the selection moved over.
                self.dropdown.addItem(self._EMPTY_TEXT, None)
                item = self.dropdown.model().item(0)
                if item is not None:
                    item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            if self.allow_custom:
                self.dropdown.addItem("🔧 Custom…", self._CUSTOM_SENTINEL)
            self._select_current()
        finally:
            self.dropdown.blockSignals(False)

    def _select_current(self):
        """Select ``internal_value``; never invent a different model."""
        if self.dropdown is None:
            return
        if self.internal_value:
            index = self.dropdown.findData(self.internal_value)
            if index != -1 and self.dropdown.itemData(index) is not None:
                self.dropdown.setCurrentIndex(index)
                if self.custom_input is not None:
                    self.custom_input.setText("")
                return

            # A genuinely custom id (not part of the catalogue) stays editable.
            known = {value for _label, value in (self.full_options or [])}
            if self.allow_custom and self.internal_value not in known:
                custom_index = self.dropdown.findData(self._CUSTOM_SENTINEL)
                if custom_index != -1:
                    self.dropdown.setCurrentIndex(custom_index)
                    if self.custom_input is not None:
                        self.custom_input.setText(str(self.internal_value))
                    return

        # The saved model is hidden by the filter (or there is none yet):
        # show the placeholder / an empty selection, but keep the value.
        if self.dropdown.count() and self.dropdown.itemData(0) is None:
            self.dropdown.setCurrentIndex(0)
        else:
            self.dropdown.setCurrentIndex(-1)

    def _on_dropdown_changed(self):
        if self.dropdown is not None:
            data = self.dropdown.currentData()
            if data is not None and data != self._CUSTOM_SENTINEL:
                self.internal_value = str(data)
        self._update_custom_input_visibility()

    def _update_custom_input_visibility(self):
        if self.custom_input_container and self.dropdown:
            is_custom = self.dropdown.currentData() == self._CUSTOM_SENTINEL
            self.custom_input_container.setVisible(is_custom)
            if is_custom and self.custom_input:
                self.custom_input.setFocus()

    def set_value(self, value):
        self.internal_value = value if value is not None else ""
        if self.dropdown is not None:
            self.dropdown.blockSignals(True)
            try:
                self._select_current()
            finally:
                self.dropdown.blockSignals(False)
            self._update_custom_input_visibility()

    def set_options(self, options):
        """
        Replace the catalogue (e.g. after "Refresh available models").

        The selected value is kept as-is: the list only decides what can be
        picked next, never what is currently configured.
        """
        options = [(str(label), str(value)) for label, value in (options or [])]
        self.options = options
        self.full_options = list(options)
        if self.dropdown is None:
            return
        self._populate_dropdown()
        self._update_custom_input_visibility()

    @property
    def tier_filter(self):
        """This provider's Free/Paid/All choice ("all" when unknown)."""
        return getattr(self.provider, "model_tier_filter", "all")

    def _filtered_options(self):
        """Catalogue entries that match this provider's Free/Paid/All filter."""
        options = list(getattr(self, "full_options", None) or self.options or [])
        mode = self.tier_filter
        if mode == "all" or self.provider is None:
            return options
        want_free = (mode == "free")
        result = []
        for label, value in options:
            try:
                is_free = self.provider.model_is_free(value)
            except Exception:  # noqa: BLE001
                is_free = None
            # Unknown tier is kept in every filter mode.
            if is_free is None or is_free == want_free:
                result.append((label, value))
        return result

    def get_value(self):
        if self.dropdown is None:
            return str(self.internal_value or "")
        if self.allow_custom and self.dropdown.currentData() == self._CUSTOM_SENTINEL:
            return (self.custom_input.text().strip()
                    if self.custom_input is not None else "")
        data = self.dropdown.currentData()
        if data is None:
            # Nothing selected because the filter hides it: keep the model
            # that was configured, never save an empty value.
            return str(self.internal_value or "")
        return str(data)

    def release_ui(self):
        self.dropdown = None
        self.custom_input = None
        self.custom_input_container = None


class AIProvider(ABC):
    """
    Abstract base class for AI providers.

    All providers must implement:
      • get_response(system_instruction, prompt, return_response=False)
      • after_load() to create their client or model instance
      • before_load() to cleanup any existing client
      • cancel() to cancel an ongoing request
    """

    # Pricing tier used to order providers in the Settings dropdown:
    # free providers first, then paid, then local ones.
    TIER = "free"  # "free" | "paid" | "local"

    # Whether every model this provider offers is free (True), paid (False),
    # or unknown/mixed (None). Used by the model-tier filter in Settings.
    FREE_TIER = None

    # The user's Free/Paid/All choice for this provider's model list. It is
    # stored in this provider's own config block, never shared across providers.
    model_tier_filter = "all"

    def __init__(self, app, provider_name: str, settings: List[AIProviderSetting],
                 description: str = "An unfinished AI provider!",
                 logo: str = None,
                 button_text: str = "Go to URL",
                 button_action: callable = None):
        self.provider_name = provider_name
        self.settings = settings
        self.app = app
        self.description = description if description else "An unfinished AI provider!"
        self.logo = logo
        self.button_text = button_text
        self.button_action = button_action
        self.tier = type(self).TIER
        self.free_tier = type(self).FREE_TIER
        # Live-discovered model list + specs (persisted in config so the
        # fetched catalog survives restarts).
        self.cached_models: list[tuple[str, str]] = []
        self.model_specs: dict[str, dict] = {}

    @property
    def display_name(self) -> str:
        """Clean name shown in the provider dropdown."""
        return _PROVIDER_LABELS.get(self.provider_name, (self.provider_name, ""))[0]

    @property
    def display_note(self) -> str:
        """Short grey hint shown next to the name (e.g. 'free tier available')."""
        return _PROVIDER_LABELS.get(self.provider_name, (self.provider_name, ""))[1]

    def model_is_free(self, model_id: str):
        """Return True/False/None for a model id (None = unknown)."""
        return self.free_tier

    @abstractmethod
    def get_response(self, system_instruction: str, prompt,
                     return_response: bool = False) -> str:
        """Send the prompt to the provider and return the response text."""
        pass

    def _call_with_retries(self, call, attempts: int = 3,
                           base_delay: float = 1.5):
        """
        Run a request, retrying only transient provider-side failures.

        Two extra attempts turn the occasional 500/503/504 into a normal
        answer instead of an error dialog. Cancellation is respected, so a
        long retry chain never blocks closing the popup/chat window.
        """
        last_exc = None
        for attempt in range(attempts):
            try:
                return call()
            except Exception as exc:  # noqa: BLE001 - re-raised below
                last_exc = exc
                if (not _is_transient_error(exc)
                        or attempt == attempts - 1
                        or getattr(self, "close_requested", False)):
                    raise
                delay = base_delay * (2 ** attempt)
                logging.warning(
                    "%s transient error (attempt %d/%d); retrying in %.1fs: %s",
                    self.provider_name, attempt + 1, attempts, delay, exc,
                )
                # A deadline/timeout already burned the full request budget.
                # Retrying adds minutes without a better chance of success,
                # so report it immediately.
                text = str(exc).lower()
                if "deadline" in text or "timed out" in text:
                    raise
                time.sleep(delay)
        raise last_exc

    # -- configuration -----------------------------------------------------

    def _model_setting(self):
        """Return the dropdown setting that selects the model, if any."""
        for setting in self.settings:
            if setting.name in ("api_model", "model_name") and hasattr(
                setting, "set_options"
            ):
                return setting
        return None

    def _is_listable_model(self, model_id: str) -> bool:
        """Provider hook: should this discovered model appear in the list?"""
        return True

    def _apply_cached_models(self, config: dict):
        """Restore a previously fetched model list and its specs."""
        setting = self._model_setting()
        if setting is None:
            return
        specs = config.get("model_specs")
        if isinstance(specs, dict):
            cleaned = {}
            for model_id, spec in specs.items():
                normalized = _normalize_model_spec(spec)
                if model_id and normalized:
                    cleaned[str(model_id)] = normalized
            self.model_specs = cleaned
        else:
            self.model_specs = {}

        cached = config.get("model_options")
        if not isinstance(cached, list):
            return
        models = []
        for item in cached:
            if (isinstance(item, (list, tuple)) and len(item) == 2
                    and str(item[1]).strip()):
                models.append((str(item[0]), str(item[1])))
        # Apply the provider's usability filter to refreshed lists too, so a
        # catalog fetched before a filter change does not keep unusable
        # entries in the dropdown after an update.
        models = [(label, value) for label, value in models
                  if self._is_listable_model(value)]
        if not models:
            return

        # Drop stale specs for models that are no longer listed.
        available = {value for _label, value in models}
        self.model_specs = {
            model_id: spec for model_id, spec in self.model_specs.items()
            if model_id in available
        }
        self.cached_models = models
        setting.options = models
        if hasattr(setting, "full_options"):
            setting.full_options = list(models)

    def load_config(self, config: dict):
        """Load configuration settings into the provider."""
        if not isinstance(config, dict):
            config = {}
        self.model_tier_filter = normalize_tier_filter(config.get("model_tier_filter"))
        self._apply_cached_models(config)
        for setting in self.settings:
            value = config.get(setting.name, setting.default_value)
            if setting.name == "api_key":
                value = deobfuscate_api_key(value)
            setattr(self, setting.name, value)
            setting.set_value(value)
        self.after_load()

    def save_config(self, values=None, persist=True):
        """
        Write this provider's configuration into the main config.

        ``values`` lets the Settings window hand over the values the user
        actually saw (its per-provider draft); without it the live widgets are
        read. ``persist=False`` skips writing config.json so a caller can
        update several providers and save once.
        """
        config = {}
        for setting in self.settings:
            if values is not None and setting.name in values:
                value = values[setting.name]
            else:
                value = setting.get_value()
            if setting.name == "api_key":
                value = obfuscate_api_key(value)
            config[setting.name] = value
        if values is not None and "model_tier_filter" in values:
            tier = values["model_tier_filter"]
        else:
            tier = self.model_tier_filter
        config["model_tier_filter"] = normalize_tier_filter(tier)

        # Persist the live model catalog (and its specs) so refreshing once
        # keeps retired models out of the dropdown after a restart, even when
        # the provider cannot be reached the next time.
        setting = self._model_setting()
        if setting is not None:
            models = list(self.cached_models or [])
            if models:
                config["model_options"] = [[label, value]
                                           for label, value in models]
            if self.model_specs:
                available = {value for _label, value in models}
                config["model_specs"] = {
                    model_id: spec
                    for model_id, spec in self.model_specs.items()
                    if model_id in available
                }

        if not isinstance(self.app.config, dict):
            self.app.config = {}
        self.app.config.setdefault("providers", {})[self.provider_name] = config
        if persist:
            self.app.save_config(self.app.config)

    # -- helpers shared by implementations ---------------------------------

    def _emit_or_return(self, text: str, return_response: bool) -> str:
        """Either return the text (window mode) or emit it for pasting."""
        if return_response:
            return text
        if text:
            signal = getattr(self.app, "output_ready_signal", None)
            if signal is not None:
                signal.emit(text)
        return ""

    def _show_error(self, exc: Exception):
        title, message = _friendly_error(exc, self.provider_name)
        logging.error("%s error: %s", self.provider_name, exc, exc_info=True)
        signal = getattr(self.app, "show_message_signal", None)
        if signal is not None:
            signal.emit(title, message)

    def _show_config_error(self, detail: str):
        logging.error("%s is not configured: %s", self.provider_name, detail)
        signal = getattr(self.app, "show_message_signal", None)
        if signal is not None:
            signal.emit(
                f"{self.provider_name} – Not Configured",
                f"{detail}\n\nOpen Settings and complete this provider's setup.",
            )

    @abstractmethod
    def after_load(self):
        """Called after configuration is loaded; create the API client here."""
        pass

    @abstractmethod
    def before_load(self):
        """Called before reloading configuration; cleanup the API client here."""
        pass

    @abstractmethod
    def cancel(self):
        """Cancel any ongoing API request (best-effort for non-streaming)."""
        pass

    # -- model discovery ---------------------------------------------------

    def list_models(self) -> tuple[list[tuple[str, str]], str]:
        """
        Return ``([(label, model_id), ...], note)`` for the provider.

        The note is a short user-facing status line. Implementations should
        never crash the app: network/HTTP failures are raised as
        ``ModelFetchError`` or fall back to the built-in list with a note.
        """
        builtin = list(getattr(self, "MODELS", []) or [])
        if builtin:
            self.cached_models = builtin
            return builtin, "Showing the built-in model list."
        return [], "This provider does not expose a model list."

    def on_model_change(self):
        """Hook for providers whose other settings depend on the model."""
        return


class GeminiProvider(AIProvider):
    """
    Provider for Google's Gemini API (using the unified `google-genai` SDK).

    Uses `client.models.generate_content()` for single-shot generation. The
    same method is used for follow-up chat too — the entire conversation
    history is passed via `contents` as a list of `Content` objects.
    """

    # Safety filtering is relaxed across all categories (best-effort; some
    # models may still soft-refuse).
    _SAFETY_SETTINGS = [
        genai_types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
        genai_types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
        genai_types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
        genai_types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
    ]

    def __init__(self, app):
        self.close_requested = False
        self.client = None
        self.api_key = ""
        default_model = "gemini-flash-latest"
        self.model_name = default_model

        settings = [
            TextSetting(
                name="api_key",
                display_name="API Key",
                description="Paste your Gemini API key here",
            ),
            DropdownSetting(
                name="model_name",
                display_name="Model",
                default_value=default_model,
                description="Select a Gemini model to use",
                options=[
                    ("⭐ Gemini 3.8 Flash (recommended | latest)",
                     "gemini-flash-latest"),
                    ("Gemini 3.8 Flash Lite (fastest | free tier)",
                     "gemini-flash-lite-latest"),
                    ("Gemini 3.8 Flash (explicit version)",
                     "gemini-3.8-flash"),
                    ("Gemini 3.6 Flash (previous generation)",
                     "gemini-3.6-flash"),
                    ("Gemini 3.5 Flash Lite (lightweight)",
                     "gemini-3.5-flash-lite"),
                    ("Gemini 3.1 Pro (preview | paid tier)",
                     "gemini-3.1-pro-preview"),
                    ("Gemma 4 31B (slow | unlimited free use)",
                     "gemma-4-31b-it"),
                    ("Gemma 4 26B A4B (slow | unlimited free use)",
                     "gemma-4-26b-a4b-it"),
                ],
                allow_custom=True,
                custom_placeholder="e.g., gemini-3.8-flash",
                provider=self,
            ),
            DropdownSetting(
                name="thinking_level",
                display_name="Thinking Level",
                default_value="",
                description=(
                    "Auto lets Gemini choose; pick a level for more or less "
                    "reasoning."
                ),
                options=[
                    ("Auto (recommended)", ""),
                    ("Low (fastest)", "low"),
                    ("Medium (balanced)", "medium"),
                    ("High (most thorough)", "high"),
                ],
            ),
        ]
        super().__init__(
            app,
            "Gemini (Recommended)",
            settings,
            "• Works with a free key (generous free tier) and with a paid/billing key.\n"
            "• Get your API key from Google AI Studio.\n"
            "• Use the Model filter to show only free models, only paid models (Pro), or both.\n"
            "• Tip: if you hit the free daily limit, switch to a Gemma model (unlimited but slower).",
            "gemini",
            "Get API Key",
            lambda: webbrowser.open("https://aistudio.google.com/app/apikey"),
        )

    def model_is_free(self, model_id: str):
        mid = (model_id or "").lower()
        return ("flash" in mid or "gemma" in mid or "lite" in mid)

    # Thinking levels documented by Google (October 2026). The API does not
    # expose supported levels per model, so we keep this table and use it to
    # avoid sending an unsupported level (the old "minimal" crash).
    _THINKING_LEVELS_BY_MODEL = {
        "gemini-3.8-flash": ("low", "medium", "high"),
        "gemini-flash-latest": ("low", "medium", "high"),
        "gemini-flash-lite-latest": ("low", "medium", "high"),
        "gemini-3.1-pro-preview": ("low", "medium", "high"),
        "gemini-2.5-pro": ("low", "medium", "high"),
        "gemini-3.6-flash": ("minimal", "low", "medium", "high"),
        "gemini-3.5-flash-lite": ("minimal", "low", "medium", "high"),
        "gemini-3.1-flash-lite-image": ("minimal", "high"),
        "gemini-3-pro-preview": ("low", "high"),
        "gemini-robotics-er-2-preview": ("minimal", "low", "medium", "high"),
    }
    _DEFAULT_THINKING_LEVELS = ("low", "medium", "high")
    DEFAULT_MODEL = "gemini-flash-latest"
    # Calm, fast and free. Used when the configured model has been retired.
    RETIRED_MODEL_FALLBACK = "gemini-flash-lite-latest"

    # Catalogs list image/audio/music/agent models as text-capable, but they
    # either produce non-text output or reject ordinary chat requests. Keeping
    # them out of the dropdown removes a whole class of "this model errors".
    _NON_CHAT_TOKENS = (
        "embedding", "embed", "aqa", "imagen", "veo", "lyria", "music",
        "image", "tts", "native-audio", "-live", "transcribe", "audio",
        "deep-research", "computer-use", "antigravity", "nano-banana", "omni",
    )

    def _is_listable_model(self, model_id: str) -> bool:
        lower = (model_id or "").strip().lower()
        if not lower.startswith(("gemini-", "gemma-")):
            return False
        return not any(token in lower for token in self._NON_CHAT_TOKENS)

    def _thinking_levels(self, model_name: str) -> tuple[str, ...]:
        """Levels this model accepts (live specs first, then built-in table)."""
        model = (model_name or "").strip().lower()
        spec_levels = (
            self.model_specs.get(model_name, {}).get("thinking_levels")
            or self.model_specs.get(model, {}).get("thinking_levels")
        )
        if spec_levels:
            return tuple(str(level) for level in spec_levels)
        if model in self._THINKING_LEVELS_BY_MODEL:
            return self._THINKING_LEVELS_BY_MODEL[model]
        if model.startswith(("gemini-3", "gemini-4")):
            return self._DEFAULT_THINKING_LEVELS
        if model.startswith("gemini-flash"):
            return self._DEFAULT_THINKING_LEVELS
        return ()

    def _setting(self, name: str):
        for setting in self.settings:
            if setting.name == name:
                return setting
        return None

    def on_model_change(self):
        """Restrict the Thinking Level dropdown to what this model supports."""
        setting = self._model_setting()
        if setting is not None and getattr(setting, "dropdown", None) is not None:
            model = setting.get_value()
        else:
            model = self.model_name
        levels = self._thinking_levels(model)

        thinking = self._setting("thinking_level")
        if thinking is None or not hasattr(thinking, "set_options"):
            return

        options = [("Auto (recommended)", "")]
        for level in levels:
            label = {
                "minimal": "Minimal (barely any reasoning)",
                "low": "Low (fastest)",
                "medium": "Medium (balanced)",
                "high": "High (most thorough)",
            }.get(level, level.title())
            options.append((f"{label}", level))
        thinking.set_options(options)
        # Keep the provider attribute in sync with the visible dropdown so an
        # immediate request (before Save) uses the same level the user sees.
        try:
            self.thinking_level = thinking.get_value()
        except (AttributeError, RuntimeError):
            pass

    def _build_config(
        self, system_instruction: str, include_thinking: bool = True,
    ) -> "genai_types.GenerateContentConfig":
        model = (self.model_name or "").strip().lower()
        is_gemma = "gemma" in model

        kwargs = {
            "safety_settings": self._SAFETY_SETTINGS,
            "max_output_tokens": 4096,
        }
        if (system_instruction or "").strip():
            kwargs["system_instruction"] = system_instruction

        # "Auto" (empty) sends no thinking config at all. That is the safe
        # default: rolling aliases such as gemini-flash-latest can point at a
        # model that rejects an explicitly requested level.
        level = (getattr(self, "thinking_level", "") or "").strip().lower()
        if (include_thinking and level and not is_gemma
                and level in self._thinking_levels(model)):
            kwargs["thinking_config"] = genai_types.ThinkingConfig(
                thinking_level=level
            )
        return genai_types.GenerateContentConfig(**kwargs)

    @staticmethod
    def _merge_system_instruction(system_instruction: str, contents):
        """
        Fold the system prompt into the first user turn.

        Some models (agent/tool-specialised ones, and Gemma variants on other
        APIs) reject a separate system instruction with
        "Developer instruction is not enabled". Merging keeps them usable
        instead of failing outright.
        """
        text = (system_instruction or "").strip()
        if not text:
            return contents
        if isinstance(contents, str):
            return f"{text}\n\n{contents}"
        if not isinstance(contents, list) or not contents:
            return contents

        merged = list(contents)
        for index, content in enumerate(merged):
            if getattr(content, "role", "") != "user":
                continue
            parts = [genai_types.Part(text=text)]
            parts.extend(list(getattr(content, "parts", None) or []))
            merged[index] = genai_types.Content(role="user", parts=parts)
            return merged
        return [genai_types.Content(
            role="user", parts=[genai_types.Part(text=text)]
        )] + merged

    @staticmethod
    def _messages_to_contents(messages: list) -> list:
        """Convert OpenAI-style chat history into google-genai Content objects."""
        contents = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "system":
                continue
            text = message.get("content", "")
            if not isinstance(text, str):
                text = clean_model_text(text)
            gemini_role = "model" if role == "assistant" else "user"
            contents.append(
                genai_types.Content(
                    role=gemini_role,
                    parts=[genai_types.Part(text=text)],
                )
            )
        return contents

    @staticmethod
    def _extract_text(response) -> str:
        try:
            text = response.text
        except Exception:
            text = None
        if text:
            return clean_model_text(text)
        # Fallback for blocked/partial responses.
        try:
            candidates = getattr(response, "candidates", None) or []
            for candidate in candidates:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", None) or []:
                    part_text = getattr(part, "text", None)
                    if part_text:
                        return clean_model_text(part_text)
        except Exception:
            pass
        return ""

    def get_response(self, system_instruction: str, prompt,
                     return_response: bool = False) -> str:
        self.close_requested = False

        if not (self.api_key or "").strip():
            self._show_config_error("No Gemini API key has been set.")
            return ""

        if self.client is None:
            self.after_load()
        if self.client is None:
            self._show_config_error("The Gemini client could not be created. Check your API key.")
            return ""

        try:
            contents = (
                self._messages_to_contents(prompt)
                if isinstance(prompt, list)
                else prompt
            )
            if not contents:
                self._show_config_error("There was no text to send.")
                return ""

            config = self._build_config(system_instruction)

            def _generate(cfg, payload, model=None):
                return self.client.models.generate_content(
                    model=model or self.model_name,
                    contents=payload,
                    config=cfg,
                )

            try:
                response = self._call_with_retries(
                    lambda: _generate(config, contents)
                )
            except Exception as exc:  # noqa: BLE001 - SDK raises many types
                if (_looks_like_thinking_level_error(exc)
                        and (getattr(self, "thinking_level", "") or "").strip()):
                    # The model rejected the selected level (common with
                    # rolling aliases). Retry once with Gemini's default
                    # thinking.
                    logging.warning(
                        "Gemini rejected thinking level %r; retrying without it: %s",
                        self.thinking_level, exc,
                    )
                    config = self._build_config(
                        system_instruction, include_thinking=False
                    )
                    response = self._call_with_retries(
                        lambda: _generate(config, contents)
                    )
                elif _system_instruction_unsupported(exc):
                    logging.warning(
                        "Gemini model %s rejects system instructions; merging "
                        "it into the prompt: %s", self.model_name, exc,
                    )
                    contents = self._merge_system_instruction(
                        system_instruction, contents
                    )
                    config = self._build_config("")
                    response = self._call_with_retries(
                        lambda: _generate(config, contents)
                    )
                elif (_retired_model_error(exc)
                        and (self.model_name or "").strip().lower()
                        != self.RETIRED_MODEL_FALLBACK):
                    # Google keeps retired ids in some catalogs, and they only
                    # fail at generation time. Producing an answer with a
                    # current model beats a dead end.
                    fallback = self.RETIRED_MODEL_FALLBACK
                    logging.warning(
                        "Gemini model %s is no longer available; retrying with "
                        "%s: %s", self.model_name, fallback, exc,
                    )
                    response = self._call_with_retries(
                        lambda: _generate(config, contents, model=fallback)
                    )
                else:
                    raise
            response_text = self._extract_text(response)
            if not response_text:
                self._show_config_error(
                    "Gemini returned an empty response. The model may have blocked "
                    "the request or hit a limit."
                )
                return ""
            return self._emit_or_return(response_text, return_response)
        except Exception as exc:  # noqa: BLE001 - SDK raises many types
            self._show_error(exc)
            return ""
        finally:
            self.close_requested = False

    def after_load(self):
        self.client = None
        key = (self.api_key or "").strip()
        if not key:
            logging.info("Gemini provider has no API key yet")
            return
        try:
            self.client = genai.Client(
                api_key=key,
                http_options=genai_types.HttpOptions(
                    timeout=120_000,
                    # Our provider-level retry decides when to try again. The
                    # SDK's own silent retries made a dead model take minutes
                    # before the error reached the user.
                    retry_options=genai_types.HttpRetryOptions(attempts=1),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logging.error("Could not create Gemini client: %s", exc)

    def before_load(self):
        self.client = None

    def cancel(self):
        self.close_requested = True

    def list_models(self) -> tuple[list[tuple[str, str]], str]:
        key = (self.api_key or "").strip()
        if not key:
            raise ModelFetchError(
                "Enter your Gemini API key first, then refresh the model list."
            )
        # Use a dedicated client: the refresh runs on a background thread and
        # must not clear/replace the client an in-flight request is using.
        try:
            client = genai.Client(
                api_key=key,
                http_options=genai_types.HttpOptions(timeout=30_000),
            )
        except Exception as exc:  # noqa: BLE001
            raise ModelFetchError(
                f"Could not create the Gemini client. Check the API key.\n\n{exc}"
            ) from exc

        try:
            models = []
            specs = {}
            for model in client.models.list():
                raw_name = getattr(model, "name", "") or ""
                model_id = raw_name.split("/")[-1].strip()
                if not model_id:
                    continue
                lower = model_id.lower()
                actions = getattr(model, "supported_actions", None) or []
                if actions and "generateContent" not in actions:
                    continue
                if not self._is_listable_model(model_id):
                    continue
                display = (getattr(model, "display_name", None)
                           or model_id).strip()
                # Pro previews are not included in Google's free tier. Say so
                # instead of letting the request fail with a raw 429.
                if (("pro" in lower or lower.startswith("gemini-4-"))
                        and "flash" not in lower):
                    display = f"{display} — paid tier"
                if lower.startswith(("gemini-2.5-", "gemini-2.0-", "gemini-1.5-")):
                    display = f"{display} (legacy)"

                thinking = bool(getattr(model, "thinking", False))
                levels = self._thinking_levels(model_id) if (
                    thinking or lower.startswith(("gemini-3", "gemini-4",
                                                  "gemini-flash"))
                ) else ()
                capabilities = ["text"]
                description = str(getattr(model, "description", "") or "").lower()
                if any(token in description for token in (
                    "vision", "multimodal", "image understanding",
                )):
                    capabilities.append("vision")
                if lower.startswith("gemini"):
                    capabilities.append("tools")
                if thinking or levels:
                    capabilities.append("thinking")

                spec = _normalize_model_spec({
                    "context": getattr(model, "input_token_limit", 0) or 0,
                    "max_output": getattr(model, "output_token_limit", 0) or 0,
                    "capabilities": capabilities,
                    "thinking_levels": list(levels),
                })
                specs[model_id] = spec

                label = display
                context_label = _format_context_size(spec.get("context"))
                if context_label:
                    label = f"{label} · {context_label}"
                if model_id == getattr(self, "model_name", ""):
                    label = f"⭐ {label}"
                models.append((label, model_id))
        except Exception as exc:  # noqa: BLE001
            raise ModelFetchError(
                f"Could not load Gemini models: {exc}"
            ) from exc

        if not models:
            raise ModelFetchError("Gemini returned no usable text models.")
        models.sort(key=lambda item: (
            item[1] != self.DEFAULT_MODEL,
            not item[0].startswith("⭐"), item[0].casefold()
        ))
        self.model_specs = specs
        self.cached_models = models
        return models, f"Found {len(models)} Gemini model(s)."


class OpenAICompatibleProvider(AIProvider):
    """
    Provider for OpenAI-compatible APIs.

    Uses `client.chat.completions.create()`; streaming is never used. This is
    also the shared base class for every curated free-tier provider below.
    """

    # Overridden by presets. "OpenAI Compatible (For Experts)" leaves these
    # unset and relies on the user's `api_base` setting.
    DEFAULT_BASE_URL = ""
    EXTRA_HEADERS = None
    ALLOW_EMPTY_KEY = False
    TEMPERATURE = 0.5
    TIER = "paid"  # custom endpoint: usually paid or self-hosted

    # Model discovery metadata (overridden by presets).
    MODELS = []
    MODELS_PATH = "/models"
    MODELS_URL = ""              # absolute URL override, if the API differs
    PUBLIC_MODEL_LIST = False    # endpoint works without an API key
    PLACEHOLDER_MODEL_KEY = True  # send a dummy bearer when no key is set
    FREE_ONLY_MODELS = False     # keep only free models (e.g. OpenRouter)
    EXCLUDE_FREE_MODELS = False  # hide free-tier-only ids (e.g. OpenCode)
    MODEL_EXCLUDE_TOKENS = ()    # extra id fragments to hide from the list
    SORT_MODELS_BY_CONTEXT = False

    def __init__(self, app, provider_name: str = "OpenAI Compatible (For Experts)",
                 settings: List[AIProviderSetting] = None,
                 description: str = None,
                 logo: str = "openai",
                 button_text: str = "Get OpenAI API Key",
                 button_action: callable = None,
                 allow_empty_key: bool | None = None,
                 extra_headers: dict | None = None):
        self.close_requested = False
        self.client = None
        self.api_key = ""
        self.api_base = ""
        self.api_model = "gpt-4o-mini"
        self.api_organisation = ""
        self.api_project = ""

        self._allow_empty_key = (
            self.ALLOW_EMPTY_KEY if allow_empty_key is None else allow_empty_key
        )
        self._extra_headers = extra_headers if extra_headers is not None else self.EXTRA_HEADERS

        if settings is None:
            settings = [
                TextSetting(
                    name="api_key",
                    display_name="API Key",
                    description="API key for the OpenAI-compatible API.",
                ),
                TextSetting(
                    name="api_base",
                    display_name="API Base URL",
                    default_value="https://api.openai.com/v1",
                    description="E.g. https://api.openai.com/v1",
                ),
                TextSetting(
                    name="api_organisation",
                    display_name="API Organisation",
                    description="Leave blank if not applicable.",
                ),
                TextSetting(
                    name="api_project",
                    display_name="API Project",
                    description="Leave blank if not applicable.",
                ),
                TextSetting(
                    name="api_model",
                    display_name="API Model",
                    default_value="gpt-4o-mini",
                    description="E.g. gpt-4o-mini",
                ),
            ]

        if description is None:
            description = (
                "• Connect to ANY OpenAI-compatible API (v1/chat/completions).\n"
                "• You must abide by the service's Terms of Service."
            )
        if button_action is None and provider_name == "OpenAI Compatible (For Experts)":
            button_action = lambda: webbrowser.open(
                "https://platform.openai.com/account/api-keys"
            )

        super().__init__(
            app,
            provider_name,
            settings,
            description,
            logo,
            button_text,
            button_action,
        )

    # -- URL / client handling --------------------------------------------

    def _resolve_base_url(self) -> str:
        return normalize_base_url(
            getattr(self, "api_base", None) or self.DEFAULT_BASE_URL
        )

    def after_load(self):
        self.client = None
        key = (self.api_key or "").strip()
        if not key and not self._allow_empty_key:
            logging.info("%s has no API key yet", self.provider_name)
            return

        base_url = self._resolve_base_url()
        if not base_url:
            # Never silently fall back to api.openai.com: for providers such
            # as Cloudflare the base URL depends on required settings.
            logging.info("%s has no API base URL yet", self.provider_name)
            return

        try:
            self.client = OpenAI(
                api_key=key or "not-needed",
                base_url=base_url or None,
                organization=(self.api_organisation or None),
                project=(self.api_project or None),
                timeout=120.0,
                max_retries=1,
                default_headers=self._extra_headers or None,
            )
        except Exception as exc:  # noqa: BLE001
            logging.error("Could not create %s client: %s", self.provider_name, exc)

    def before_load(self):
        self.client = None

    def cancel(self):
        self.close_requested = True

    # -- model discovery --------------------------------------------------

    def _model_request_headers(self) -> dict:
        key = (self.api_key or "").strip()
        if not key and not self._allow_empty_key and not self.PUBLIC_MODEL_LIST:
            raise ModelFetchError(
                f"Enter your {self.provider_name} API key first, then refresh."
            )
        headers = {"Accept": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        elif (self._allow_empty_key or self.PUBLIC_MODEL_LIST) and self.PLACEHOLDER_MODEL_KEY:
            # Public model catalogs and local servers do not need a real key.
            headers["Authorization"] = "Bearer not-needed"
        headers.update(self._extra_headers or {})
        return headers

    def _model_list_url(self) -> str:
        if self.MODELS_URL:
            return self.MODELS_URL
        base = self._resolve_base_url()
        if not base:
            raise ModelFetchError(
                "Set the API Base URL (and any required account settings) "
                "before refreshing models."
            )
        return base.rstrip("/") + "/" + self.MODELS_PATH.lstrip("/")

    @staticmethod
    def _item_value(item, *names):
        """Read a field from either a dict or an SDK/pydantic object."""
        for name in names:
            if isinstance(item, dict):
                value = item.get(name)
            else:
                value = getattr(item, name, None)
            if value not in (None, ""):
                return value
        return None

    def _parse_model_items(self, payload) -> list[tuple[str, str, dict]]:
        if isinstance(payload, dict):
            items = payload.get("data")
            if items is None:
                items = payload.get("models")
            if items is None and "result" in payload:
                items = payload.get("result")
        else:
            items = payload
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            items = []

        excluded_tokens = (
            "embedding", "embed-", "-embed", "rerank", "cross-encoder",
            "sentence-transformers", "moderation", "content-safety",
            "safety-guard", "prompt-guard", "whisper", "text-to-speech",
            "tts", "stable-diffusion", "diffusion", "image-generation",
            "flux", "video", "speech", "ocr", "lyria", "music", "audio",
            "voice", "transcribe",
        ) + tuple(self.MODEL_EXCLUDE_TOKENS)

        found = []
        seen = set()
        for item in items:
            model_id = self._item_value(item, "id", "model", "name", "slug")
            if not model_id:
                continue
            model_id = str(model_id).strip()
            if not model_id or model_id in seen:
                continue
            lower_id = model_id.lower()
            if any(token in lower_id for token in excluded_tokens):
                continue
            if self.EXCLUDE_FREE_MODELS and (
                lower_id.endswith("-free") or lower_id.endswith(":free")
            ):
                # Some gateways only allow their free models from their own
                # client, so listing them here would only produce errors.
                continue

            endpoints = self._item_value(item, "endpoints", "supported_actions")
            if endpoints:
                if isinstance(endpoints, (list, tuple)):
                    endpoint_text = " ".join(str(value).lower() for value in endpoints)
                else:
                    endpoint_text = str(endpoints).lower()
                # Cohere/Gemini-style catalogs include embeddings and rerankers.
                if "embed" in endpoint_text or "rerank" in endpoint_text:
                    continue
                if ("chat" not in endpoint_text
                        and "generate" not in endpoint_text):
                    continue

            if self.FREE_ONLY_MODELS:
                pricing = self._item_value(item, "pricing")
                is_free = lower_id.endswith(":free")
                if isinstance(pricing, dict):
                    prompt = str(pricing.get("prompt", "")).strip()
                    completion = str(pricing.get("completion", "")).strip()
                    is_free = is_free or (prompt in ("0", "0.0")
                                          and completion in ("0", "0.0"))
                if not is_free:
                    continue

            name = self._item_value(item, "name", "display_name", "title")
            if not name or str(name).strip() == model_id:
                name = model_id
            name = str(name).strip()

            top_provider = self._item_value(item, "top_provider")
            if not isinstance(top_provider, dict):
                top_provider = {}

            context = self._item_value(
                item, "context_length", "context_window", "max_context_length"
            )
            if context is None:
                context = top_provider.get("context_length")
            if context is None:
                providers = self._item_value(item, "providers")
                if isinstance(providers, list):
                    for provider in providers:
                        context = self._item_value(provider, "context_length")
                        if context:
                            break
            try:
                context = int(context) if context is not None else 0
            except (TypeError, ValueError):
                context = 0

            max_output = self._item_value(
                item, "max_completion_tokens", "max_output_tokens",
                "output_token_limit",
            )
            if max_output is None:
                max_output = top_provider.get("max_completion_tokens")
            try:
                max_output = int(max_output) if max_output is not None else 0
            except (TypeError, ValueError):
                max_output = 0

            capabilities = ["text"]

            modalities = self._item_value(item, "input_modalities", "modalities")
            architecture = self._item_value(item, "architecture")
            if isinstance(architecture, dict):
                modalities = (
                    architecture.get("input_modalities")
                    or architecture.get("modality")
                    or modalities
                )
            if isinstance(modalities, (list, tuple)):
                modality_text = " ".join(str(value) for value in modalities).lower()
            else:
                modality_text = str(modalities or "").lower()
            if "image" in modality_text or "vision" in modality_text:
                capabilities.append("vision")
            if "audio" in modality_text:
                capabilities.append("audio")
            if self._item_value(item, "supports_vision") is True:
                capabilities.append("vision")

            supported = self._item_value(
                item, "supported_parameters", "supported_features", "capabilities"
            )
            if isinstance(supported, (list, tuple)):
                supported_text = " ".join(str(value) for value in supported).lower()
            elif isinstance(supported, dict):
                supported_text = " ".join(str(key) for key in supported).lower()
            else:
                supported_text = str(supported or "").lower()
            if "tool" in supported_text or "function" in supported_text:
                capabilities.append("tools")
            if self._item_value(item, "supports_tools") is True:
                capabilities.append("tools")
            if any(token in supported_text for token in (
                "response_format", "structured", "json",
            )):
                capabilities.append("json")
            if any(token in supported_text for token in (
                "reasoning", "thinking",
            )):
                capabilities.append("thinking")

            spec = _normalize_model_spec({
                "context": context,
                "max_output": max_output,
                "capabilities": capabilities,
            })

            seen.add(model_id)
            found.append((name, model_id, spec))
        return found

    def _fallback_models(self, note: str):
        models = list(self.MODELS)
        self.cached_models = models
        self.model_specs = {}
        return models, note

    def list_models(self) -> tuple[list[tuple[str, str]], str]:
        # Configuration problems (missing key/base URL) are actionable: show
        # them instead of silently falling back to a list that will not work.
        headers = self._model_request_headers()
        url = self._model_list_url()

        try:
            payload = _http_get_json(url, headers, timeout=25.0)
        except ModelFetchError as exc:
            # Connection / non-JSON failures: the built-in list is still a
            # useful starting point, so fall back with an explanatory note.
            if self.MODELS:
                return self._fallback_models(
                    f"{exc} Showing the built-in list."
                )
            raise
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            if exc.code in (401, 403):
                raise ModelFetchError(
                    f"{self.provider_name} rejected the API key while fetching "
                    "models. Check that the key is correct and has access to "
                    "this API."
                ) from exc
            if exc.code == 429:
                raise ModelFetchError(
                    "The provider rate-limited the model-list request. "
                    "Wait a moment and try again."
                ) from exc
            note = f"Live model list unavailable (HTTP {exc.code})."
            if self.MODELS:
                return self._fallback_models(
                    f"{note} Showing the built-in list."
                )
            raise ModelFetchError(
                f"{note} {detail}".strip()
            ) from exc
        except Exception as exc:  # noqa: BLE001
            if self.MODELS:
                return self._fallback_models(
                    f"Could not fetch live models ({exc}). "
                    "Showing the built-in list."
                )
            raise ModelFetchError(f"Could not fetch models: {exc}") from exc

        found = self._parse_model_items(payload)
        if not found:
            if self.MODELS:
                return self._fallback_models(
                    "The provider returned no matching text models. "
                    "Showing the built-in list."
                )
            raise ModelFetchError(
                "The provider returned an empty model list. Check the API "
                "Base URL and key."
            )

        default_model = (getattr(self, "DEFAULT_MODEL", "") or "").strip()
        if self.SORT_MODELS_BY_CONTEXT:
            found.sort(key=lambda item: (
                item[1] != default_model,
                -(item[2].get("context") or 0),
                item[0].casefold(),
            ))
        else:
            found.sort(key=lambda item: (
                item[1] != default_model,
                item[0].casefold(),
            ))

        models = []
        specs = {}
        for name, model_id, spec in found:
            label = name
            context_label = _format_context_size(spec.get("context"))
            if context_label:
                label = f"{label} · {context_label}"
            if model_id == default_model:
                label = f"⭐ {label}"
            models.append((label, model_id))
            specs[model_id] = spec

        self.model_specs = specs
        self.cached_models = models
        return models, f"Found {len(models)} live model(s)."

    # Presets with a catch-all router can recover from retired/stale model ids
    # by switching to it instead of showing an error.
    MODEL_FALLBACK = ""

    # -- request ----------------------------------------------------------

    def get_response(self, system_instruction: str, prompt,
                     return_response: bool = False) -> str:
        self.close_requested = False

        if self.client is None:
            self.after_load()
        if self.client is None:
            if not (self.api_key or "").strip() and not self._allow_empty_key:
                self._show_config_error(f"No {self.provider_name} API key has been set.")
            else:
                self._show_config_error(
                    "The API client could not be created. Check the API Base URL "
                    "and API key in Settings."
                )
            return ""

        if isinstance(prompt, list):
            messages = [
                msg for msg in prompt
                if isinstance(msg, dict) and msg.get("role") and msg.get("content") is not None
            ]
        else:
            messages = [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ]

        if not messages:
            self._show_config_error("There was no text to send.")
            return ""

        model = (getattr(self, "api_model", "") or "").strip()
        if not model:
            self._show_config_error("No model name has been set.")
            return ""

        try:
            kwargs = {
                "model": model,
                "messages": messages,
                "stream": False,
            }
            if self.TEMPERATURE is not None:
                kwargs["temperature"] = self.TEMPERATURE
            try:
                response = self._call_with_retries(
                    lambda: self.client.chat.completions.create(**kwargs)
                )
            except Exception as exc:  # noqa: BLE001
                fallback = (self.MODEL_FALLBACK or "").strip()
                if (not fallback or model.lower() == fallback.lower()
                        or not _missing_model_error(exc)):
                    raise
                logging.warning(
                    "%s model %s is not available; retrying with %s: %s",
                    self.provider_name, model, fallback, exc,
                )
                kwargs = dict(kwargs, model=fallback)
                response = self._call_with_retries(
                    lambda: self.client.chat.completions.create(**kwargs)
                )

            choices = getattr(response, "choices", None) or []
            if not choices:
                self._show_config_error(
                    f"{self.provider_name} returned an empty response."
                )
                return ""
            message = choices[0].message
            content = getattr(message, "content", None)
            if content is None:
                content = getattr(message, "refusal", None)
            response_text = clean_model_text(content)
            if not response_text:
                self._show_config_error(
                    f"{self.provider_name} returned an empty response. "
                    "Try a different model."
                )
                return ""
            return self._emit_or_return(response_text, return_response)
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)
            return ""


class PresetOpenAIProvider(OpenAICompatibleProvider):
    """
    Base class for curated free-tier providers.

    Subclasses only declare metadata + models; client behaviour is inherited.
    Keeping this data-driven makes it trivial to add another free service.
    """

    PROVIDER_NAME = "Free Provider"
    DESCRIPTION = ""
    BASE_URL = ""
    MODELS: list[tuple[str, str]] = []
    DEFAULT_MODEL = ""
    KEY_URL = ""
    KEY_BUTTON_TEXT = "Get Free API Key"
    LOGO = None
    KEY_DESCRIPTION = "Paste your API key here"
    MODEL_PLACEHOLDER = "Enter a model ID"
    EXTRA_HEADERS = None
    ALLOW_EMPTY_KEY = False
    TEMPERATURE = 0.5
    TIER = "free"  # free-tier presets; paid/local subclasses override

    def __init__(self, app):
        settings = [
            TextSetting(
                name="api_key",
                display_name="API Key",
                description=self.KEY_DESCRIPTION,
            ),
            DropdownSetting(
                name="api_model",
                display_name="Model",
                default_value=self.DEFAULT_MODEL,
                description="Select a model",
                options=self.MODELS,
                allow_custom=True,
                custom_placeholder=self.MODEL_PLACEHOLDER,
                provider=self,
            ),
        ]
        super().__init__(
            app,
            provider_name=self.PROVIDER_NAME,
            settings=settings,
            description=self.DESCRIPTION,
            logo=self.LOGO,
            button_text=self.KEY_BUTTON_TEXT,
            button_action=(
                (lambda: webbrowser.open(self.KEY_URL)) if self.KEY_URL else None
            ),
            allow_empty_key=self.ALLOW_EMPTY_KEY,
            extra_headers=self.EXTRA_HEADERS,
        )
        self.api_base = self.BASE_URL


class OpenRouterProvider(PresetOpenAIProvider):
    PROVIDER_NAME = "OpenRouter (Free Models)"
    FREE_TIER = True
    BASE_URL = "https://openrouter.ai/api/v1"
    DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning:free"
    KEY_URL = "https://openrouter.ai/keys"
    LOGO = "openrouter"
    KEY_DESCRIPTION = "Paste your OpenRouter API key (sk-or-v1-...)"
    MODEL_PLACEHOLDER = "e.g., nvidia/nemotron-3.5-lightning:free"
    EXTRA_HEADERS = {
        "HTTP-Referer": "https://github.com/Ulf3000/WritingToolsRE",
        "X-Title": "Writing Tools",
    }
    PUBLIC_MODEL_LIST = True
    FREE_ONLY_MODELS = True
    SORT_MODELS_BY_CONTEXT = True
    MODEL_FALLBACK = "openrouter/free"
    # Current fallback list (October 2026). The Refresh button in Settings
    # fetches the live list, so this only matters while offline.
    MODELS = [
        ("⭐ NVIDIA: Nemotron 3.5 Lightning (free)", "nvidia/nemotron-3.5-lightning:free"),
        ("NVIDIA: Nemotron 3 Ultra (free)", "nvidia/nemotron-3-ultra-550b-a55b:free"),
        ("NVIDIA: Nemotron 3 Super (free)", "nvidia/nemotron-3-super-120b-a12b:free"),
        ("NVIDIA: Nemotron 3.5 Super VL (free)", "nvidia/nemotron-3.5-super-vl:free"),
        ("Google: Gemma 4 31B (free)", "google/gemma-4-31b-it:free"),
        ("Google: Gemma 4 26B A4B (free)", "google/gemma-4-26b-a4b-it:free"),
        ("Poolside: Laguna S 2.1 (free)", "poolside/laguna-s-2.1:free"),
        ("Poolside: Laguna XS 2.1 (free)", "poolside/laguna-xs-2.1:free"),
        ("Dots Studio: Dots3-Note Preview (free)", "dots-studio/dots-3-note-preview:free"),
        ("Recursive: Flint (free)", "recursive/flint-v1.0:free"),
        ("Cohere: North Mini Code (free)", "cohere/north-mini-code:free"),
        ("XingChen: Xing4.0 29B A4B (free)", "xingchen-agi/xing4.0-29b-a4b:free"),
        ("NVIDIA: Nemotron 3 Nano Omni (free)", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"),
        ("Free Models Router (free)", "openrouter/free"),
        ("LiquidAI: LFM2.5-2.6B (free)", "liquid/lfm-2.5-2.6b:free"),
    ]
    DESCRIPTION = (
        "• OpenRouter gives you access to many models, including a rotating\n"
        "  selection of completely free models (the ones ending in \":free\").\n"
        "• Great fallback when Gemini's daily limit runs out.\n"
        "• Free models are rate-limited (~50 requests/day without credits).\n"
        "• Click \"Refresh available models\" in Settings to get the current\n"
        "  free list — OpenRouter changes it frequently."
    )


class GroqProvider(PresetOpenAIProvider):
    PROVIDER_NAME = "Groq (Free Tier)"
    FREE_TIER = True
    BASE_URL = "https://api.groq.com/openai/v1"
    DEFAULT_MODEL = "openai/gpt-oss-120b"
    KEY_URL = "https://console.groq.com/keys"
    LOGO = "groq"
    KEY_DESCRIPTION = "Paste your Groq API key (gsk_...)"
    MODEL_PLACEHOLDER = "e.g., openai/gpt-oss-120b"
    MODELS = [
        ("⭐ GPT-OSS 120B (free)", "openai/gpt-oss-120b"),
        ("GPT-OSS 20B (free | fastest)", "openai/gpt-oss-20b"),
        ("Qwen 3.6 27B (free | preview)", "qwen/qwen3.6-27b"),
        ("Qwen 3.8 27B (free | preview)", "qwen/qwen3.8-27b"),
        ("Llama 3.3 70B Versatile (legacy)", "llama-3.3-70b-versatile"),
    ]
    DESCRIPTION = (
        "• Groq runs open models on custom hardware — extremely fast.\n"
        "• Generous free tier with per-minute/per-day rate limits.\n"
        "• No credit card required.\n"
        "• Excellent choice for instant proofreading."
    )


class MistralProvider(PresetOpenAIProvider):
    PROVIDER_NAME = "Mistral (Free Tier)"
    FREE_TIER = True
    BASE_URL = "https://api.mistral.ai/v1"
    DEFAULT_MODEL = "mistral-small-latest"
    KEY_URL = "https://console.mistral.ai/api-keys/"
    LOGO = "mistral"
    KEY_DESCRIPTION = "Paste your Mistral API key"
    MODEL_PLACEHOLDER = "e.g., mistral-small-latest"
    MODELS = [
        ("⭐ Mistral Small (free experiment tier)", "mistral-small-latest"),
        ("Mistral Nemo (free | fast)", "open-mistral-nemo"),
        ("Ministral 8B (free)", "ministral-8b-latest"),
        ("Ministral 3B (free | fastest)", "ministral-3b-latest"),
        ("Codestral (free | code)", "codestral-latest"),
    ]
    DESCRIPTION = (
        "• Mistral's free \"Experiment\" plan includes several models.\n"
        "• Good multilingual writing quality.\n"
        "• Free tier is rate-limited; a credit card is not required."
    )


class CloudflareProvider(PresetOpenAIProvider):
    PROVIDER_NAME = "Cloudflare Workers AI (Free)"
    FREE_TIER = True
    BASE_URL = ""  # Resolved per-account below
    DEFAULT_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    KEY_URL = "https://dash.cloudflare.com/profile/api-tokens"
    LOGO = "cloudflare"
    KEY_DESCRIPTION = "Paste a Cloudflare API token with Workers AI access"
    MODEL_PLACEHOLDER = "e.g., @cf/meta/llama-3.3-70b-instruct-fp8-fast"
    MODELS = [
        ("⭐ Llama 3.3 70B FP8 Fast (free)", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
        ("Llama 3.1 8B Instruct (free | fast)", "@cf/meta/llama-3.1-8b-instruct"),
        ("Kimi K2.6 (free)", "@cf/moonshotai/kimi-k2.6"),
        ("Qwen 2.5 Coder 32B (free | code)", "@cf/qwen/qwen2.5-coder-32b-instruct"),
        ("Mistral 7B Instruct (free)", "@cf/mistral/mistral-7b-instruct-v0.3"),
        ("Gemma 3 12B (free)", "@cf/google/gemma-3-12b-it"),
    ]
    DESCRIPTION = (
        "• Cloudflare Workers AI includes a free daily allowance.\n"
        "• You need BOTH your Cloudflare Account ID and an API token.\n"
        "• Find the Account ID on your Cloudflare dashboard (right sidebar).\n"
        "• The token needs the \"Workers AI\" permission."
    )

    def __init__(self, app):
        self.account_id = ""
        base_settings = [
            TextSetting(
                name="account_id",
                display_name="Cloudflare Account ID",
                description="Found in the right sidebar of your Cloudflare dashboard",
            ),
        ]
        super().__init__(app)
        # Prepend the account ID field so it appears above the API key.
        self.settings = base_settings + self.settings

    def _resolve_base_url(self) -> str:
        account_id = (getattr(self, "account_id", "") or "").strip()
        if not account_id:
            return ""
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{account_id}/ai/v1"
        )

    def get_response(self, system_instruction: str, prompt,
                     return_response: bool = False) -> str:
        self.after_load()
        if self.client is None and not (getattr(self, "account_id", "") or "").strip():
            self._show_config_error(
                "No Cloudflare Account ID has been set. Add it in Settings."
            )
            return ""
        return super().get_response(system_instruction, prompt, return_response)

    def list_models(self) -> tuple[list[tuple[str, str]], str]:
        account_id = (getattr(self, "account_id", "") or "").strip()
        key = (self.api_key or "").strip()
        if not account_id:
            raise ModelFetchError(
                "Set your Cloudflare Account ID first, then refresh models."
            )
        if not key:
            raise ModelFetchError(
                "Enter your Cloudflare API token first, then refresh models."
            )

        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{account_id}/ai/models/search?per_page=100"
        )
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        }
        try:
            payload = _http_get_json(url, headers, timeout=25.0)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ModelFetchError(
                    "Cloudflare rejected the API token. Make sure it has the "
                    "\"Workers AI\" permission and that the Account ID is correct."
                ) from exc
            return self._fallback_models(
                f"Cloudflare model search failed (HTTP {exc.code}). "
                "Showing the built-in list."
            )
        except Exception as exc:  # noqa: BLE001
            return self._fallback_models(
                f"Could not fetch Cloudflare models ({exc}). "
                "Showing the built-in list."
            )

        results = payload.get("result") if isinstance(payload, dict) else payload
        if not isinstance(results, list):
            results = []

        models = []
        specs = {}
        seen = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            model_id = name or str(item.get("id") or "").strip()
            if not model_id.startswith("@cf/") or model_id in seen:
                continue

            task = item.get("task")
            task_name = ""
            if isinstance(task, dict):
                task_name = str(task.get("name") or "").lower()
            elif task:
                task_name = str(task).lower()
            if task_name and ("text generation" not in task_name
                              and "text-generation" not in task_name):
                continue
            lower = model_id.lower()
            if any(token in lower for token in (
                "embedding", "embed", "rerank", "whisper", "tts",
                "stable-diffusion", "flux", "image", "vision", "ocr",
                "text-to-speech", "automatic-speech",
            )):
                continue

            try:
                context = int(
                    item.get("context_window") or item.get("context_length") or 0
                )
            except (TypeError, ValueError):
                context = 0
            spec = _normalize_model_spec({
                "context": context,
                "capabilities": ["text"],
                "note": "Cloudflare Workers AI",
            })
            specs[model_id] = spec

            seen.add(model_id)
            label = model_id
            context_label = _format_context_size(context)
            if context_label:
                label = f"{label} · {context_label}"
            if model_id == self.DEFAULT_MODEL:
                label = f"⭐ {label}"
            models.append((label, model_id))

        if not models:
            return self._fallback_models(
                "Cloudflare returned no text-generation models. "
                "Showing the built-in list."
            )
        models.sort(key=lambda item: (
            not item[0].startswith("⭐"), item[0].casefold()
        ))
        self.model_specs = specs
        self.cached_models = models
        return models, f"Found {len(models)} Cloudflare model(s)."


class ZhipuProvider(PresetOpenAIProvider):
    PROVIDER_NAME = "Zhipu GLM (Free)"
    FREE_TIER = True
    BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
    DEFAULT_MODEL = "glm-5.3-flash"
    KEY_URL = "https://open.bigmodel.cn/usercenter/apikeys"
    LOGO = "zhipu"
    KEY_DESCRIPTION = "Paste your Zhipu / BigModel API key"
    MODEL_PLACEHOLDER = "e.g., glm-5.3-flash"
    MODELS = [
        ("⭐ GLM-5.3-Flash (free)", "glm-5.3-flash"),
        ("GLM-4.5-Flash (free)", "glm-4.5-flash"),
        ("GLM-4-Flash (free | legacy)", "glm-4-flash"),
        ("GLM-4V-Flash (free | vision)", "glm-4v-flash"),
    ]
    DESCRIPTION = (
        "• Zhipu AI (BigModel) offers free GLM-5.3-Flash models.\n"
        "• Strong Chinese and decent English writing support.\n"
        "• Free tier is rate-limited; no card required."
    )


class OpenCodeProvider(PresetOpenAIProvider):
    """
    OpenCode Console (opencode.ai) — a pay-as-you-go gateway in front of
    OpenAI, Anthropic, Google, DeepSeek, GLM, Kimi and other models.

    Uses the OpenAI-compatible endpoint, so it plugs into the shared preset
    provider without special-casing. The model catalogue is public, but free
    ("-free") models are only usable from the OpenCode app itself, so they are
    filtered out of the picker.
    """

    PROVIDER_NAME = "OpenCode (Console)"
    TIER = "paid"
    FREE_TIER = False
    BASE_URL = "https://opencode.ai/inference/openai/v1"
    DEFAULT_MODEL = "deepseek-v4.1-flash"
    KEY_URL = "https://opencode.ai/console"
    LOGO = "opencode"
    KEY_BUTTON_TEXT = "Get API Key"
    KEY_DESCRIPTION = "Paste your OpenCode Console API key"
    MODEL_PLACEHOLDER = "e.g., deepseek-v4.1-flash"
    PUBLIC_MODEL_LIST = True
    PLACEHOLDER_MODEL_KEY = False  # a dummy bearer is rejected (401)
    MODELS_URL = "https://opencode.ai/inference/v1/models"
    EXCLUDE_FREE_MODELS = True
    MODEL_EXCLUDE_TOKENS = ("jev-",)  # System One, not a chat completion model
    MODEL_FALLBACK = "glm-5.3-flash"
    # Cloudflare in front of opencode.ai rejects Python's default UA.
    EXTRA_HEADERS = {"User-Agent": BROWSER_USER_AGENT}
    # Offline fallback list (October 2026). "Refresh available models" loads
    # the live catalogue.
    MODELS = [
        ("⭐ DeepSeek V4.1 Flash (cheap | fast)", "deepseek-v4.1-flash"),
        ("GLM 5.3 Flash (cheap)", "glm-5.3-flash"),
        ("GLM 5.3", "glm-5.3"),
        ("Qwen3.8 Flash", "qwen3.8-flash"),
        ("Qwen3.8 Max", "qwen3.8-max"),
        ("Kimi K2.6", "kimi-k2.6"),
        ("Kimi K2.7 Code", "kimi-k2.7-code"),
        ("MiniMax M3", "minimax-m3"),
        ("Mistral Large 4", "mistral-large-4"),
        ("Gemini 3.8 Flash (via OpenCode)", "gemini-3.8-flash"),
        ("Gemini 3.5 Flash Lite (via OpenCode)", "gemini-3.5-flash-lite"),
        ("Claude Haiku 5.5 (via OpenCode)", "claude-haiku-5-5"),
        ("Claude Sonnet 5 (via OpenCode)", "claude-sonnet-5"),
        ("GPT 5.6 Luna (via OpenCode)", "gpt-5.6-luna"),
        ("GPT 5.4 Mini (via OpenCode)", "gpt-5.4-mini"),
        ("Grok 4.6 (via OpenCode)", "grok-4.6"),
    ]
    DESCRIPTION = (
        "• OpenCode Console (opencode.ai) is a pay-as-you-go gateway with\n"
        "  models from OpenAI, Anthropic, Google, DeepSeek, GLM and others.\n"
        "• Sign in to the Console, add credits, create an API key, paste it.\n"
        "• Billing is per token; prices are shown in the Console.\n"
        "• The free \"-free\" models only work inside the OpenCode app itself,\n"
        "  so they are hidden here.\n"
        "• Click \"Refresh available models\" in Settings for the current list."
    )


class OpenAIProvider(PresetOpenAIProvider):
    """
    OpenAI (ChatGPT) — pay-as-you-go API.

    Uses the OpenAI-compatible endpoint, so it plugs into the shared preset
    provider without special-casing.
    """

    PROVIDER_NAME = "OpenAI (ChatGPT)"
    TIER = "paid"
    FREE_TIER = False
    BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-4o-mini"
    KEY_URL = "https://platform.openai.com/api-keys"
    LOGO = "openai"
    KEY_BUTTON_TEXT = "Get OpenAI API Key"
    KEY_DESCRIPTION = "Paste your OpenAI API key (sk-...)"
    MODEL_PLACEHOLDER = "e.g., gpt-4o-mini"
    PUBLIC_MODEL_LIST = False
    PLACEHOLDER_MODEL_KEY = False
    MODELS = [
        ("GPT-4o", "gpt-4o"),
        ("GPT-4o mini (cheap | fast)", "gpt-4o-mini"),
        ("GPT-4.1", "gpt-4.1"),
        ("GPT-4.1 mini (cheap | fast)", "gpt-4.1-mini"),
        ("GPT-4.1 nano (cheapest)", "gpt-4.1-nano"),
        ("o4-mini (reasoning)", "o4-mini"),
        ("o3 (reasoning)", "o3"),
    ]
    DESCRIPTION = (
        "• OpenAI (ChatGPT) — pay-as-you-go API.\n"
        "• Create an API key at platform.openai.com.\n"
        "• Click 'Refresh available models' in Settings for the current list."
    )


class AnthropicProvider(PresetOpenAIProvider):
    """
    Anthropic (Claude) — pay-as-you-go API.

    Uses Anthropic's OpenAI-compatible endpoint, so it plugs into the shared
    preset provider without special-casing.
    """

    PROVIDER_NAME = "Anthropic (Claude)"
    TIER = "paid"
    FREE_TIER = False
    BASE_URL = "https://api.anthropic.com/v1"
    DEFAULT_MODEL = "claude-sonnet-4-5"
    KEY_URL = "https://console.anthropic.com/settings/keys"
    LOGO = "claude"
    KEY_BUTTON_TEXT = "Get Anthropic API Key"
    KEY_DESCRIPTION = "Paste your Anthropic API key (sk-ant-...)"
    MODEL_PLACEHOLDER = "e.g., claude-sonnet-4-5"
    PUBLIC_MODEL_LIST = False
    PLACEHOLDER_MODEL_KEY = False
    EXTRA_HEADERS = {"anthropic-version": "2023-06-01"}
    MODELS = [
        ("Claude Sonnet 4.5 (balanced)", "claude-sonnet-4-5"),
        ("Claude Haiku 4.5 (fast | cheap)", "claude-haiku-4-5"),
        ("Claude Opus 4.1 (most capable)", "claude-opus-4-1"),
    ]
    DESCRIPTION = (
        "• Anthropic (Claude) — pay-as-you-go API.\n"
        "• Create an API key at console.anthropic.com.\n"
        "• Click 'Refresh available models' in Settings for the current list."
    )


class DeepSeekProvider(PresetOpenAIProvider):
    """
    DeepSeek - very low-priced pay-as-you-go API with an OpenAI-compatible
    endpoint. Currently the most used provider on developer routers.
    """

    PROVIDER_NAME = "DeepSeek"
    TIER = "paid"
    FREE_TIER = False
    BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-flash"
    KEY_URL = "https://platform.deepseek.com/api_keys"
    LOGO = "deepseek"
    KEY_BUTTON_TEXT = "Get DeepSeek API Key"
    KEY_DESCRIPTION = "Paste your DeepSeek API key (sk-...)"
    MODEL_PLACEHOLDER = "e.g., deepseek-flash"
    PUBLIC_MODEL_LIST = False
    PLACEHOLDER_MODEL_KEY = False
    MODELS = [
        ("DeepSeek Flash (cheap | fast)", "deepseek-flash"),
        ("DeepSeek V4 Pro (most capable)", "deepseek-v4-pro"),
    ]
    DESCRIPTION = (
        "\u2022 DeepSeek - very cheap pay-as-you-go API (prepaid balance).\n"
        "\u2022 Create an API key at platform.deepseek.com and top up credit.\n"
        "\u2022 Click 'Refresh available models' in Settings for the current list."
    )


class XAIProvider(PresetOpenAIProvider):
    """xAI (Grok) - pay-as-you-go API with an OpenAI-compatible endpoint."""

    PROVIDER_NAME = "xAI (Grok)"
    TIER = "paid"
    FREE_TIER = False
    BASE_URL = "https://api.x.ai/v1"
    DEFAULT_MODEL = "grok-4.7"
    KEY_URL = "https://console.x.ai/"
    LOGO = "xai"
    KEY_BUTTON_TEXT = "Get xAI API Key"
    KEY_DESCRIPTION = "Paste your xAI API key (xai-...)"
    MODEL_PLACEHOLDER = "e.g., grok-4.7"
    PUBLIC_MODEL_LIST = False
    PLACEHOLDER_MODEL_KEY = False
    MODELS = [
        ("Grok 4.7 (most capable)", "grok-4.7"),
        ("Grok 4.6", "grok-4.6"),
        ("Grok 4.5", "grok-4.5"),
        ("Grok 4.3 (cheaper)", "grok-4.3"),
        ("Grok 4.20 (non-reasoning)", "grok-4.20-0309-non-reasoning"),
    ]
    DESCRIPTION = (
        "\u2022 xAI (Grok) - pay-as-you-go API.\n"
        "\u2022 Create an API key at console.x.ai and add credit.\n"
        "\u2022 Click 'Refresh available models' in Settings for the current list."
    )


class LMStudioProvider(PresetOpenAIProvider):
    PROVIDER_NAME = "LM Studio (Local, Free)"
    TIER = "local"
    FREE_TIER = True
    BASE_URL = "http://localhost:1234/v1"
    DEFAULT_MODEL = "local-model"
    KEY_URL = "https://lmstudio.ai/"
    KEY_BUTTON_TEXT = "Get LM Studio"
    LOGO = "lmstudio"
    ALLOW_EMPTY_KEY = True
    KEY_DESCRIPTION = "Optional — LM Studio usually does not require a key"
    MODEL_PLACEHOLDER = "Use the model name shown in LM Studio"
    MODELS = [
        ("Local model (LM Studio decides)", "local-model"),
    ]
    DESCRIPTION = (
        "• Completely free and private: runs on your own computer.\n"
        "• Install LM Studio, load a model, then start its Local Server\n"
        "  (Developer tab → Start Server).\n"
        "• Default server URL: http://localhost:1234/v1\n"
        "• No internet or API key needed."
    )


class OllamaProvider(AIProvider):
    """
    Provider for connecting to a local (or remote) Ollama server.

    Uses the /chat endpoint. Streaming is not used.
    """

    TIER = "local"
    FREE_TIER = True

    def __init__(self, app):
        self.api_model = "llama3.1:8b"
        self.keep_alive = "5m"

        settings = [
            TextSetting(
                name="api_base",
                display_name="API Base URL",
                default_value="http://localhost:11434",
                description="E.g. http://localhost:11434",
            ),
            DropdownSetting(
                name="api_model",
                display_name="API Model",
                default_value="llama3.1:8b",
                description="Select an installed model (or click Refresh in Settings)",
                options=[
                    ("llama3.1:8b (common)", "llama3.1:8b"),
                    ("qwen2.5:7b (common)", "qwen2.5:7b"),
                ],
                allow_custom=True,
                custom_placeholder="e.g., llama3.2:3b",
                provider=self,
            ),
            TextSetting(
                name="keep_alive",
                display_name="Keep model loaded (minutes)",
                default_value="5",
                description="E.g. 5 (0 unloads immediately)",
            ),
        ]
        super().__init__(
            app,
            "Ollama (For Experts)",
            settings,
            "• Connect to an Ollama server (local LLM).\n"
            "• Completely free and private once installed.\n"
            "• Set the base URL and the exact model name you pulled.",
            "ollama",
            "Ollama Set-up Instructions",
            lambda: webbrowser.open(
                "https://github.com/Ulf3000/WritingToolsRE#-local-models-with-ollama"
            ),
        )

    def _keep_alive_value(self):
        raw = str(getattr(self, "keep_alive", "") or "").strip()
        if not raw:
            return None
        # Ollama accepts a number of seconds or a duration string ("5m").
        try:
            minutes = float(raw)
            return max(0, int(minutes * 60))
        except ValueError:
            return raw

    def get_response(self, system_instruction: str, prompt,
                     return_response: bool = False) -> str:
        self.close_requested = False

        if self.client is None:
            self.after_load()
        if self.client is None:
            self._show_config_error(
                "Could not connect to Ollama. Is the Ollama server running?"
            )
            return ""

        if isinstance(prompt, list):
            messages = [
                msg for msg in prompt
                if isinstance(msg, dict) and msg.get("role") and msg.get("content") is not None
            ]
        else:
            messages = [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ]

        if not messages:
            self._show_config_error("There was no text to send.")
            return ""

        try:
            response = self.client.chat(
                model=self.api_model,
                messages=messages,
                stream=False,
                keep_alive=self._keep_alive_value(),
            )
            content = None
            message = getattr(response, "message", None)
            if message is not None:
                content = getattr(message, "content", None)
                if content is None and isinstance(message, dict):
                    content = message.get("content")
            if content is None and isinstance(response, dict):
                content = (response.get("message") or {}).get("content")
            response_text = clean_model_text(content)
            if not response_text:
                self._show_config_error(
                    "Ollama returned an empty response. Check that the model is "
                    "installed (`ollama pull <model>`)."
                )
                return ""
            return self._emit_or_return(response_text, return_response)
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)
            return ""

    def after_load(self):
        self.client = None
        base = normalize_base_url(getattr(self, "api_base", "") or
                                  "http://localhost:11434")
        try:
            self.client = OllamaClient(host=base)
        except Exception as exc:  # noqa: BLE001
            logging.error("Could not create Ollama client: %s", exc)

    def before_load(self):
        self.client = None

    def cancel(self):
        self.close_requested = True

    def list_models(self) -> tuple[list[tuple[str, str]], str]:
        base = normalize_base_url(
            getattr(self, "api_base", "") or "http://localhost:11434"
        )
        try:
            client = OllamaClient(host=base)
            response = client.list()
        except Exception as exc:  # noqa: BLE001
            raise ModelFetchError(
                f"Could not reach Ollama at {base}. Is the server running?\n\n"
                f"{exc}"
            ) from exc

        entries = None
        if isinstance(response, dict):
            entries = response.get("models")
        else:
            entries = getattr(response, "models", None)
        if entries is None:
            try:
                entries = list(response)
            except TypeError:
                entries = []

        models = []
        specs = {}
        for entry in entries or []:
            if isinstance(entry, dict):
                name = entry.get("model") or entry.get("name")
                size = entry.get("size")
                details = entry.get("details") or {}
            else:
                name = getattr(entry, "model", None) or getattr(entry, "name", None)
                size = getattr(entry, "size", None)
                details = getattr(entry, "details", None) or {}
            name = str(name or "").strip()
            if not name:
                continue
            label = name
            try:
                if size:
                    label += f" · {float(size) / (1024 ** 3):.1f} GB"
            except (TypeError, ValueError):
                pass

            if isinstance(details, dict):
                parameter_size = details.get("parameter_size")
                quantization = details.get("quantization_level")
                family = details.get("family")
            else:
                parameter_size = getattr(details, "parameter_size", None)
                quantization = getattr(details, "quantization_level", None)
                family = getattr(details, "family", None)
            note = " · ".join(
                str(value) for value in (family, parameter_size, quantization)
                if value
            )
            specs[name] = _normalize_model_spec({
                "capabilities": ["text"],
                "note": note,
            })

            if name == getattr(self, "api_model", ""):
                label = f"⭐ {label}"
            models.append((label, name))

        if not models:
            raise ModelFetchError(
                "Ollama is running, but no models are installed yet.\n\n"
                "Install one with: ollama pull llama3.1:8b"
            )
        models.sort(key=lambda item: (
            not item[0].startswith("⭐"), item[0].casefold()
        ))
        self.model_specs = specs
        self.cached_models = models
        return models, f"Found {len(models)} installed Ollama model(s)."


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Dropdown labels: provider_name (the stable config key, never changed) ->
# (clean display name, grey hint). Only the display changes; saved keys and
# settings stay attached to provider_name.
_PROVIDER_LABELS = {
    "Anthropic (Claude)": ("Anthropic (Claude)", "paid"),
    "Cloudflare Workers AI (Free)": ("Cloudflare Workers AI", "free tier available"),
    "DeepSeek": ("DeepSeek", "paid"),
    "Gemini (Recommended)": ("Gemini", "free tier available"),
    "Groq (Free Tier)": ("Groq", "free tier available"),
    "LM Studio (Local, Free)": ("LM Studio", "local"),
    "Mistral (Free Tier)": ("Mistral", "free tier available"),
    "Ollama (For Experts)": ("Ollama", "local"),
    "OpenAI (ChatGPT)": ("OpenAI (ChatGPT)", "paid"),
    "OpenAI Compatible (For Experts)": ("OpenAI Compatible", "custom endpoint"),
    "OpenCode (Console)": ("OpenCode", "paid"),
    "OpenRouter (Free Models)": ("OpenRouter", "free models available"),
    "xAI (Grok)": ("xAI (Grok)", "paid"),
    "Zhipu GLM (Free)": ("Zhipu GLM", "free tier available"),
}

# Order matters only as a tie-break; the dropdown is sorted by display name.
PROVIDER_CLASSES = [
    GeminiProvider,
    OpenRouterProvider,
    GroqProvider,
    MistralProvider,
    CloudflareProvider,
    ZhipuProvider,
    OpenCodeProvider,
    OpenAIProvider,
    AnthropicProvider,
    DeepSeekProvider,
    XAIProvider,
    LMStudioProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
]


def create_providers(app) -> list[AIProvider]:
    """Instantiate every provider, skipping any that fail to initialise."""
    # One entry per provider, sorted alphabetically by display name. The
    # free/paid distinction lives on the model level (Model filter), not on
    # the provider level.
    providers = []
    for provider_class in PROVIDER_CLASSES:
        try:
            providers.append(provider_class(app))
        except Exception as exc:  # noqa: BLE001
            logging.error("Could not initialise provider %s: %s",
                          provider_class.__name__, exc, exc_info=True)
    if not providers:
        # Absolutely should not happen, but never leave the app provider-less.
        providers.append(GeminiProvider(app))
    # Local providers (LM Studio, Ollama) go to the end of the list.
    providers.sort(key=lambda p: (p.tier == "local", p.display_name.lower()))
    return providers
