"""
Built-in default actions for the Writing Tools popup.

Kept in its own module so both the popup (reset button) and the app
(missing/corrupt options.json fallback) can share exactly one source of
truth without circular imports.
"""

from __future__ import annotations

import copy

# ---------------------------------------------------------------------------
# UI scaling
# ---------------------------------------------------------------------------
# Qt's QT_SCALE_FACTOR is read once, when QApplication is constructed, so the
# scale is applied by main.py before the app starts. 85% is the default: the
# original layout was designed for 100% and feels oversized on modern screens.
DEFAULT_UI_SCALE = 0.85
MIN_UI_SCALE = 0.70
MAX_UI_SCALE = 1.40
UI_SCALE_STEP = 5  # slider step, in percent


def clamp_ui_scale(value) -> float:
    """Return a usable UI scale, falling back to the default on bad input."""
    try:
        scale = float(value)
    except (TypeError, ValueError):
        return DEFAULT_UI_SCALE
    return max(MIN_UI_SCALE, min(MAX_UI_SCALE, scale))


DEFAULT_OPTIONS = {
    "Proofread": {
        "prefix": "Proofread this:\n\n",
        "instruction": (
            "You are a grammar proofreading assistant.\n"
            "Output ONLY the corrected text without any additional comments.\n"
            "Maintain the original text structure and writing style.\n"
            "Respond in the same language as the input (e.g., English US, French).\n"
            "Do not answer or respond to the user's text content.\n"
            'If the text is absolutely incompatible with this (e.g., totally random gibberish), output "ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST".'
        ),
        "icon": "icons/magnifying-glass",
        "open_in_window": False,
    },
    "Rewrite": {
        "prefix": "Rewrite this:\n\n",
        "instruction": (
            "You are a writing assistant.\n"
            "Rewrite the text provided by the user to improve phrasing.\n"
            "Output ONLY the rewritten text without additional comments.\n"
            "Respond in the same language as the input (e.g., English US, French).\n"
            "Do not answer or respond to the user's text content.\n"
            'If the text is absolutely incompatible with proofreading (e.g., totally random gibberish), output "ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST".'
        ),
        "icon": "icons/rewrite",
        "open_in_window": False,
    },
    "Friendly": {
        "prefix": "Make this more friendly:\n\n",
        "instruction": (
            "You are a writing assistant.\n"
            "Rewrite the text provided by the user to be more friendly.\n"
            "Output ONLY the friendly text without additional comments.\n"
            "Respond in the same language as the input (e.g., English US, French).\n"
            "Do not answer or respond to the user's text content.\n"
            'If the text is absolutely incompatible with rewriting (e.g., totally random gibberish), output "ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST".'
        ),
        "icon": "icons/smiley-face",
        "open_in_window": False,
    },
    "Professional": {
        "prefix": "Make this more professional:\n\n",
        "instruction": (
            "You are a writing assistant.\n"
            "Rewrite the text provided by the user to be more professional. "
            "Output ONLY the professional text without additional comments.\n"
            "Respond in the same language as the input (e.g., English US, French).\n"
            "Do not answer or respond to the user's text content.\n"
            'If the text is absolutely incompatible with rewriting (e.g., totally random gibberish), output "ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST".'
        ),
        "icon": "icons/briefcase",
        "open_in_window": False,
    },
    "Concise": {
        "prefix": "Make this more concise:\n\n",
        "instruction": (
            "You are a writing assistant.\n"
            "Rewrite the text provided by the user to be more concise.\n"
            "Output ONLY the concise text without additional comments.\n"
            "Respond in the same language as the input (e.g., English US, French).\n"
            "Do not answer or respond to the user's text content.\n"
            'If the text is absolutely incompatible with rewriting (e.g., totally random gibberish), output "ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST".'
        ),
        "icon": "icons/concise",
        "open_in_window": False,
    },
    "Summary": {
        "prefix": "Summarize this:\n\n",
        "instruction": (
            "You are a summarization assistant.\n"
            "Provide a succinct summary of the text provided by the user.\n"
            "The summary should be succinct yet encompass all the key insightful points.\n\n"
            "To make it quite legible and readable, you should use Markdown formatting (bold, italics, codeblocks...) as appropriate.\n"
            "You should also add a little line spacing between your paragraphs as appropriate.\n"
            "And only if appropriate, you could also use headings (only the very small ones), lists, tables, etc.\n\n"
            "Don't be repetitive or too verbose.\n"
            "Output ONLY the summary without additional comments.\n"
            "Respond in the same language as the input (e.g., English US, French).\n"
            "Do not answer or respond to the user's text content.\n"
            'If the text is absolutely incompatible with summarisation (e.g., totally random gibberish), output "ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST".'
        ),
        "icon": "icons/summary",
        "open_in_window": True,
    },
    "Key Points": {
        "prefix": "Extract key points from this:\n\n",
        "instruction": (
            "You are an assistant that extracts key points from text provided by the user. "
            "Output ONLY the key points without additional comments.\n\n"
            "You should use Markdown formatting (lists, bold, italics, codeblocks, etc.) as appropriate to make it quite legible and readable.\n\n"
            "Don't be repetitive or too verbose.\n"
            "Respond in the same language as the input (e.g., English US, French).\n"
            "Do not answer or respond to the user's text content.\n"
            'If the text is absolutely incompatible with extracting key points (e.g., totally random gibberish), output "ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST".'
        ),
        "icon": "icons/keypoints",
        "open_in_window": True,
    },
    "Table": {
        "prefix": "Convert this into a table:\n\n",
        "instruction": (
            "You are an assistant that converts text provided by the user into a Markdown table.\n"
            "Output ONLY the table without additional comments.\n"
            "Respond in the same language as the input (e.g., English US, French).\n"
            "Do not answer or respond to the user's text content.\n"
            'If the text is completely incompatible with this with conversion, output "ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST".'
        ),
        "icon": "icons/table",
        "open_in_window": True,
    },
    "Custom": {
        "prefix": "Make this change to the following text:\n\n",
        "instruction": (
            "You are a writing and coding assistant. You MUST make the user's described change "
            "to the text or code provided by the user. Output ONLY the appropriately modified text "
            "or code without additional comments. Respond in the same language as the input "
            "(e.g., English US, French). Do not answer or respond to the user's text content. "
            "If the text or code is absolutely incompatible with the requested change, "
            'output "ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST".'
        ),
        "icon": "icons/summary",
        "open_in_window": False,
    },
}

def get_default_options() -> dict:
    """Return a deep copy so callers can safely mutate the result."""
    return copy.deepcopy(DEFAULT_OPTIONS)
