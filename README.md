 <p align="center">
  <b>A system-wide AI writing assistant for Windows.</b><br>
  Select text, press <kbd>Ctrl</kbd>+<kbd>Space</kbd>, and use a ready-made preset or your own instruction to proofread, rewrite, translate, summarise, and more.
</p>

<p align="center">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-0078D6?style=flat-square">
  <img alt="License" src="https://img.shields.io/badge/license-GPLv3-2EA043?style=flat-square">
  <a href="https://github.com/Ulf3000/WritingToolsRE/releases"><img alt="Releases" src="https://img.shields.io/badge/releases-download-57606A?style=flat-square"></a>
  
</p>

<p align="center"><sub>Maintained by <a href="https://github.com/Ulf3000">Ulf3000</a></sub></p>

## What is Writing Tools?

<img width="228" height="261" alt="image" src="https://github.com/user-attachments/assets/d9d23d64-711c-41a1-b41c-71b0853f2eba" />


Writing Tools is a free, open-source desktop app that brings AI assistance to almost any Windows app: your browser, email client, editor, office suite, or chat app.

Select text and choose a preset, or write your own instruction. The result replaces your selection in place. You can also summarise longer content, ask follow-up questions, or start a chat without selecting text.

The original project was created by [Jesai Tarun](https://github.com/theJayTea). The original version is no longer actively developed; this maintained fork continues the project with provider updates, fixes, and new builds.

## Presets, your way

Writing Tools comes with useful presets so you can get started immediately:

- **Proofread** — fix grammar, spelling, and punctuation.
- **Rewrite** — improve phrasing while preserving meaning.
- **Friendly**, **Professional**, **Concise** — adjust tone or length.
- **Summary**, **Key Points**, **Table** — turn selected content into a structured result.



The main idea is customisation: **every action is an instruction you control.** Use **Custom** to enter anything you want, such as “translate to French”, “explain this simply”, “make this title case”, or “add comments to this code”. Add your own actions, then rename, change icons, reorder, or remove them. You can also assign actions their own hotkeys.

Summary, Key Points, and Table open in a separate Markdown-rendered window. Select a whole article or use a YouTube transcript, then ask follow-up questions about the content.

Press `Ctrl+Space` with no text selected to start a chat. Chat history lasts while the window is open.

## AI providers

<img width="508" height="544" alt="image" src="https://github.com/user-attachments/assets/2ed97f4d-cc9c-4d36-ba04-58c1daf9e123" />


Choose a provider in **Settings → Choose AI Provider**. Switch providers without losing your presets.

| Provider | Access |
|---|---|
| Gemini | Free tier |
| OpenRouter | Free and paid models |
| Groq | Free tier |
| Cerebras | Free tier |
| Mistral | Free tier |
| Together AI | Free and paid models |
| Hugging Face | Free credits |
| Cohere | Free trial |
| SambaNova | Free tier |
| NVIDIA NIM | Free credits |
| Cloudflare Workers AI | Free daily allowance |
| Zhipu GLM | Free tier |
| OpenCode | Pay-as-you-go access to multiple model providers |
| LM Studio | Local models |
| Ollama | Local models |
| OpenAI Compatible | Compatible APIs and self-hosted servers |

Model availability changes. In Settings, refresh the selected provider’s model list to see what is currently available. Some providers require an API key; local Ollama and LM Studio can run without one.

## Why Writing Tools?

- **Works across apps:** use AI wherever you can select text.
- **Customisable:** start with useful presets, then create actions for your own workflow.
- **Flexible models:** use cloud providers or local models.
- **Clipboard-friendly:** the app temporarily uses the clipboard to read and replace your selection, then restores its previous contents.
- **Privacy-conscious:** text is sent only to the provider you choose when you invoke an action. Local models can keep processing on your device.
- **Free and open source:** GPLv3, with no subscription or built-in ads.

## Install

1. Download the latest `Writing.Tools.zip` from the [Releases](https://github.com/Ulf3000/WritingToolsRE/releases) page.
2. Extract it and run `Writing Tools.exe`.

Writing Tools is portable. If you extract it into a protected folder such as `Program Files`, run it as administrator on first launch so it can create its configuration files.

Open Writing Tools from the system tray to enable **Start on boot** in Settings.

## Notes

- In Microsoft Word, `Ctrl+Space` clears formatting by default. Change Writing Tools’ shortcut or remap Word’s shortcut if needed.
- Replacing selected text in Word may remove rich-text formatting such as bold, italics, and colours.
- Cloud providers have their own privacy policies and terms. API keys and settings are stored locally in `config.json`; local providers process text on your device.

## Run or build from source

- [Run Writing Tools from source](https://github.com/Ulf3000/WritingToolsRE/blob/main/README's%20Linked%20Content/To%20Run%20Writing%20Tools%20Directly%20from%20the%20Source%20Code.md)
- [Compile the application](https://github.com/Ulf3000/WritingToolsRE/blob/main/README's%20Linked%20Content/To%20Compile%20the%20Application%20Yourself.md)
