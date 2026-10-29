# ✍️ Writing Tools

<p align="center">
  <b>An Apple Intelligence–style writing assistant for Windows and Linux.</b><br>
  Select text anywhere, press <kbd>Ctrl</kbd>+<kbd>Space</kbd>, and let your AI model proofread,
  rewrite, translate or summarise it — right where you are typing.
</p>

<p align="center">
  <img alt="Platforms" src="https://img.shields.io/badge/platforms-Windows%20%7C%20Linux-0078D6?style=flat-square">
  <img alt="License" src="https://img.shields.io/badge/license-GPLv3-2EA043?style=flat-square">
  <a href="https://github.com/Ulf3000/WritingToolsRE/releases"><img alt="Releases" src="https://img.shields.io/badge/releases-download-57606A?style=flat-square"></a>
  <img alt="Providers" src="https://img.shields.io/badge/AI%20providers-16-8250DF?style=flat-square">
</p>

<p align="center">
  <sub>Maintained fork by <a href="https://github.com/Ulf3000">Ulf3000</a></sub>
</p>

<!-- 📸 Screenshots: paste one or two images here when ready, for example:
<p align="center"><img src="PASTE_IMAGE_URL" alt="Writing Tools popup" width="720"></p>
-->

## 📖 What is Writing Tools?

Writing Tools is a free, open-source desktop app that puts a capable AI model behind one
keyboard shortcut. It works system-wide: in your browser, your mail client, your editor,
Word, Slack, or anything else that lets you select text.

It can fix grammar, change tone, translate, summarise long articles and YouTube transcripts,
build tables, and hold a quick chat — without interrupting your flow. It borrows the clipboard
only long enough to read and replace your selection, then puts it back exactly as it was.

The original project has been featured in 🔥 [**30+ publications**](https://github.com/Ulf3000/WritingToolsRE/blob/main/Media%20Coverage.md)
([Beebom](https://beebom.com/high-schooler-app-brings-apple-inteligence-writing-tools-windows/),
[XDA](https://www.xda-developers.com/windows-pc-can-now-deliver-instant-free-writing-help-across-all-apps/),
[How-To Geek](https://www.howtogeek.com/if-you-like-apple-intelligences-writing-tool-try-this-open-source-app-as-its-windows-counterpart/),
[Neowin](https://www.neowin.net/news/apple-intelligence-writing-tools-for-windows-get-new-summarization-features-and-more/),
[Windows Central](https://www.windowscentral.com/software-apps/can-apple-catch-up-apple-intelligence-just-shipped-yet-free-apple-writing-tools-on-github-for-windows-and-linux-make-a-better-alternative)...)
and was also among the [**top 10 trending AI projects worldwide**](https://devface.ai/ranking/top_ai_developers/2024-10)
on GitHub in October 2024.

> **🛠️ A maintained fork.** Writing Tools was originally created by
> [Jesai Tarun](https://github.com/theJayTea). The upstream project is no longer actively
> developed, so this fork is kept alive by [Ulf3000](https://github.com/Ulf3000) — with new
> providers, bug fixes and fresh builds. It stays free and open-source, as always.

## ⚡ What you can do

### 1. Proofread and rewrite — everywhere

- Select any text and press `ctrl+space`.
- Pick **Proofread**, **Rewrite**, **Friendly**, **Professional**, **Concise**, or type your own
  instruction (`translate to French`, `make it title case`, `add comments to this code`, …).
- Your selection is replaced in place. Changed your mind? `ctrl+z` restores the original.

### 2. Summarise long content and chat with it

- Select a whole article with `ctrl+a`, or grab a YouTube transcript from the video description.
- Choose **Summary**, **Key Points**, or **Table**.
- The result opens in a clean, Markdown-rendered window — and you can keep asking follow-up
  questions about it.

### 3. Build your own buttons

Every button is just a prompt with an icon. Add, rename, reorder, and give them their own
hotkeys. Whatever you can describe, you can turn into a one-key action.

### 4. Chat without selecting anything

Press `ctrl+space` with no selection to start a conversation with your model. Chat history is
kept only while the window is open.

## 🌟 Why Writing Tools?

- **Frontier models, not a tiny built-in one.** Use the same class of models that power modern
  coding assistants — free or paid, cloud or local.
- **Free by default.** Twelve providers with free tiers are built in, and everything is
  open-source under the GPLv3. No subscriptions, no ads, no telemetry.
- **One interface, 16 providers.** Switch providers in Settings whenever a quota runs out;
  your prompts and buttons stay exactly the same.
- **System-wide and clipboard-friendly.** It borrows the clipboard just long enough to read
  and replace your selection, then restores what was there before.
- **Private by design.** Your keys and settings stay on your device. Text is only sent to the
  provider you explicitly chose — or processed entirely offline with a local model.
- **Works in any language.** Proofread, rewrite and translate between languages, including
  better-than-Google-Translate results for many pairs.
- **Code-aware.** Add comments, refactor snippets, or translate code explanations.
- **Compact, native-feeling UI.** A Windows 11 Fluent-inspired look that follows your system's
  light or dark mode, with one accent colour, a configurable hotkey and a UI scale from
  70% to 140%.

## 🤖 AI providers

Every provider below is built in and selectable under **Settings → Choose AI Provider**.
If one quota runs out, switch to another and keep writing.

### Free tiers

| Provider | Notes |
|---|---|
| **Gemini (Recommended)** | Generous free tier for Flash models, plus unlimited-but-slower Gemma |
| **OpenRouter (Free Models)** | Rotating `:free` models from many labs |
| **Groq (Free Tier)** | Extremely fast Llama / GPT-OSS models |
| **Cerebras (Free Tier)** | ~1M free tokens per day, very fast |
| **Mistral (Free Tier)** | Free "Experiment" plan |
| **Together AI (Free Model)** | Free Ternary Bonsai 27B, plus paid models |
| **Hugging Face (Free Credits)** | Monthly free inference credits |
| **Cohere (Free Trial)** | Free trial keys |
| **SambaNova (Free Tier)** | Free Llama / DeepSeek tier |
| **NVIDIA NIM (Free Credits)** | Free API credits at build.nvidia.com |
| **Cloudflare Workers AI (Free)** | Free daily allowance |
| **Zhipu GLM (Free)** | Free GLM-5.3-Flash models |

### Paid, local and custom

| Provider | Notes |
|---|---|
| **OpenCode (Console)** | Pay-as-you-go gateway to OpenAI, Anthropic, Google, DeepSeek, GLM, Kimi and more |
| **LM Studio (Local, Free)** | 100% local and private, no key needed |
| **Ollama (For Experts)** | 100% local and private |
| **OpenAI Compatible (For Experts)** | Any OpenAI-compatible API, including self-hosted servers |

### Good to know

- **Model lists change constantly.** Open **Settings**, choose a provider, and press
  **Refresh available models** to load its current catalogue. This works without a key for
  OpenRouter, Hugging Face, SambaNova, NVIDIA NIM, OpenCode and local Ollama/LM Studio; the
  others need their API key first (and Cloudflare also needs the Account ID).
- **Model details** are shown under the dropdown — context window, maximum output, and
  capabilities such as tools, vision or thinking. The list and specs are saved with your
  settings, so retired models stay gone after a restart.
- **Gemini thinking level:** leave it on **Auto** unless you need more reasoning. Only levels
  supported by the selected model are offered, and Writing Tools retries without a thinking
  setting if the API rejects one.
- **OpenCode free models** (`-free`) only work inside the OpenCode app itself, so Writing Tools
  hides them and lists the paid models instead.
- **No restarts needed:** saving Settings (shortcut, provider) and editing, adding, deleting or
  resetting buttons all apply immediately — including their direct hotkeys. External edits to
  `options.json` are picked up live as well.
- **UI scale:** Settings → **UI Scale** scales the whole interface from 70% to 140% (default
  **85%**). Qt reads the global scale at startup, so saving offers to relaunch the app; your
  settings and buttons are kept. The value lives in `config.json` as `ui_scale`.

## ✅ Install

### 🪟 Windows

1. Download the latest `Writing.Tools.zip` from the [Releases](https://github.com/Ulf3000/WritingToolsRE/releases) page.
2. Extract it wherever you like (e.g. `Documents`), run `Writing Tools.exe`, and you're done.

Prefer the very latest changes, or no build for your platform yet? You can also
[run it from source](#-run-or-build-from-source).

Writing Tools is portable. If you extract it into a protected folder such as `Program Files`,
run it as administrator at least on first launch so it can create its config files next to
the executable.

> **Tip:** Open Writing Tools from the tray icon and enable **start on boot** in Settings.

### 🐧 Linux *(work in progress)*

[Run it from the source code](https://github.com/Ulf3000/WritingToolsRE/blob/main/README's%20Linked%20Content/To%20Run%20Writing%20Tools%20Directly%20from%20the%20Source%20Code.md).

It works well on X11. On Wayland there are two caveats, described in the upstream tracker: it
[works in XWayland apps](https://github.com/theJayTea/WritingTools/issues/34#issuecomment-2461633556), and it
[works if you disable Wayland per Flatpak with Flatseal](https://github.com/theJayTea/WritingTools/issues/93#issuecomment-2576511041).

## 👀 Tips

### Summarise a YouTube video

Open the video, expand its description, select the transcript, and run **Summary**. No
transcript in the description? Use the browser extension of your choice to copy it first.

### Make it work better in Microsoft Word

In Word, `ctrl+space` is mapped to **Clear Formatting**, which also drops paragraph
indentation. Either assign Writing Tools a different hotkey, or remap Clear Formatting in
Word's keyboard settings.

> **Note:** Word's rich-text formatting (bold, italics, colours, …) is lost when Writing Tools
> replaces a selection. A Markdown editor such as [Obsidian](https://obsidian.md/) has no such
> issue.

## 🔒 Privacy

- Writing Tools collects and transmits **no** writing data, diagnostics or usage statistics.
  A local log file stays next to the executable and is never uploaded.
- Text is sent **only** to the provider you explicitly invoke, and only when you invoke it.
- API keys and settings stay on your machine, in `config.json` next to the executable
  (keys are lightly obfuscated, never uploaded).
- Local providers (Ollama, LM Studio) keep everything on-device and work offline.

If you use a cloud provider, that provider's privacy policy and terms apply.

## ✨ Buttons and options

Five one-click actions work on a selection:

| Button | What it does |
|---|---|
| **Proofread** | Fixes grammar, spelling and punctuation — the Grammarly replacement |
| **Rewrite** | Improves phrasing without changing the meaning |
| **Friendly** | Warms up the tone |
| **Professional** | Makes it suitable for work |
| **Concise** | Cuts the fluff |

Three more open a window with Markdown rendering, selectable text and a zoom level that is
remembered between launches:

| Button | What it does |
|---|---|
| **Summary** | A clear, short summary of the selection |
| **Key Points** | The most important points as a list |
| **Table** | Turns unstructured text into a table (paste it straight into Word) |

**Custom** takes a free-form instruction (`Describe your change...`) and can do anything the
model can: translate, explain, reformat, add code comments. Any of these can be renamed,
re-iconed, reordered or deleted — and you can add as many new ones as you like.

## 🦙 Local models with Ollama

Run a model entirely on your machine — no key, no internet, no data leaving the device.

1. [Download and install Ollama](https://ollama.com/download).
2. Pick a model from the [library](https://ollama.com/library). `llama3.1:8b` is a good
   starting point (~8 GB VRAM/RAM).
3. Pull it: `ollama pull llama3.1:8b`.
4. In Writing Tools, either:
   - choose **Ollama (For Experts)** and keep the default model, or
   - choose **OpenAI Compatible (For Experts)** and enter:
     - **API Key:** `ollama` (any string works for most local servers)
     - **API Base URL:** `http://localhost:11434/v1`
     - **API Model:** `llama3.1:8b`
5. Keep Ollama and Writing Tools running in the background and enjoy offline writing help. 🎉

## 🐞 Known issues

1. **Hotkey conflicts.** If `ctrl+space` does nothing, another program may already own it.
   Change the shortcut to something like `` ctrl+` `` or `ctrl+j` and press **Save** — it takes
   effect immediately, no restart required.
2. **Slow first launch.** Antivirus software tends to scan the freshly downloaded executable
   on first run. After that it starts instantly.

## 🛠️ Run or build from source

- [Run Writing Tools directly from the source code](https://github.com/Ulf3000/WritingToolsRE/blob/main/README's%20Linked%20Content/To%20Run%20Writing%20Tools%20Directly%20from%20the%20Source%20Code.md)
- [Compile the application yourself](https://github.com/Ulf3000/WritingToolsRE/blob/main/README's%20Linked%20Content/To%20Compile%20the%20Application%20Yourself.md)

> **UI styling:** Writing Tools uses Qt's built-in **Fusion** style plus a small custom
> stylesheet (`ui/UIUtils.py`) — no extra theme dependency. The custom sheet keeps the app
> consistent and portable across Windows and Linux.

## 🌟 Contributors

Writing Tools would not be where it is today without its amazing contributors.

### 🪟🐧 Windows & Linux

**1. [momokrono](https://github.com/momokrono)** — added Linux support, switched to the
pynput API for better Windows stability, added Ollama support, the core logic for customisable
buttons, and localisation. Fixed countless bugs and added graceful shutdown via SIGINT.

**2. [Cameron Redmore (CameronRedmore)](https://github.com/CameronRedmore)** — extensively
refactored Writing Tools and added OpenAI-compatible API support, streamed responses, and chat
mode when no text is selected.

**3. [Soszust40](https://github.com/Soszust40)** — helped add dark mode, tray menu fixes and
UI improvements.

**4. [Alok Saboo (arsaboo)](https://github.com/arsaboo)** — improved the reliability of text
selection.

**5. [raghavdhingra24](https://github.com/raghavdhingra24)** — made the rounded corners
anti-aliased and prettier.

**6. [ErrorCatDev](https://github.com/ErrorCatDev)** — significantly improved the About
window, made it scrollable, and cleaned up `.gitignore` and `requirements.txt`.

**7. [Vadim Karpenko](https://github.com/Vadim-Karpenko)** — added the start-on-boot setting.

## 📬 Contact

- **Maintainer:** [Ulf3000](https://github.com/Ulf3000)
- **Bugs & feature requests:** [open an issue](https://github.com/Ulf3000/WritingToolsRE/issues)

## 📄 License

Distributed under the **GNU General Public License v3.0** — see [LICENSE](LICENSE).
