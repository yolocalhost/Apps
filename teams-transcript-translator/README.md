# Teams Transcript Translator

Simple local macOS application for OCR of an English or German Teams transcript and translation to Slovak.

## Setup

```bash
brew install python-tk@3.14
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 app.py
```

Run these commands from the repository directory.

The model is loaded from:

```text
~/Downloads/Resources/Models/MiLMMT-46-4B-v0.1-6bit
```

Set `TEAMS_TRANSLATOR_MODEL_PATH` before starting the app to use a different model location.

## Use

1. Select `English` or `German`.
2. Click `Select area` and drag over the transcript on the primary display.
3. Click `Start`.
4. The left panel shows OCR input and the right panel shows the Slovak translation.
5. Click `Participants` to add or edit names, one participant per line. While transcription runs, OCR uses the changes on the next scan and `Stop` saves them to `names.txt`; while stopped, `Apply` saves them immediately.
6. When OCR is stopped, click `Learn words` to see the most frequent transcript words and their Slovak translations. This is available when the app opens, so you can choose an older saved session before starting a new scan. Common helper words, names, and words already marked as known are omitted.
7. In `Session words`, choose `Aktuálna session` or an older saved transcript, select the input language, and click `Už viem` after selecting words you already know. The known-word list is stored locally in `known_words.txt` and is reused in future sessions. Completed vocabulary translations are cached in `vocabulary_translations.txt`, so refreshing the list does not translate the same word again.
8. Click `Transcripts` to open the `transcripts` folder in Finder for quick navigation to saved sessions.

After clicking `Stop`, the current transcript is saved automatically as two UTF-8 text files: one original and one Slovak translation. Each new OCR session is saved to a new file pair under `transcripts/YYYY/MM/DD/` with a time-based filename. If the app is closed while OCR is running, the current transcript is saved then as well.

The `English` and `German` input selector can be changed while OCR is running. The next screen scan uses the selected language.

Speaker context is taken from names visible in the current scan and retained between scans. If the selected area starts in the middle of a message and no previous speaker is known, that first fragment remains without a speaker until a name is recognized.

OCR lines belonging to the same visible speaker block are shown as one paragraph. Repeated fragments, shorter OCR fragments, and overlapping revisions are merged when they clearly refer to the same text.

When a speaker returns after another speaker, the speaker heading is shown again. This keeps a short utterance from absorbing the next speaker's text.

Names from `names.txt` are kept as the canonical speaker labels. Teams speaker badges are matched tolerantly: bullet or punctuation noise before and after the name, separated or joined words, stray badge characters, `Name Surname` alone, and the numeric Teams suffix are all recognized, so a badge such as `Vlad Radu | BCR` or `Vlad Radu BCR 6076 ED` still resolves to the configured participant. Numeric Teams suffixes are kept in the label. A participant badge that OCR reads in the middle of a paragraph starts a new speaker block instead of becoming part of the previous speaker's text.

Obvious OCR fragments without normal text, such as `-**+` or `***`, are ignored.

macOS must allow the terminal or Python process to use Screen Recording in System Settings > Privacy & Security.

The OCR and translation are local-only. Screenshots never leave the machine. Transcript text is sent only to the internal ECP LLM for meeting notes, described below.

## Meeting notes for Codex and Copilot

After `Stop` saves a transcript, `meeting_notes.py` runs in the background. It:

1. Removes repeated OCR fragments from `transcript_*_original.txt`.
2. Asks the ECP LLM (`https://llm.ecp.erste.ai/v1`, default model `ECP-DeepSeek-V4.1-Flash-1017`, fallback `ECP-GLM-5.3-Flash`) for English notes: decisions, facts, action items, open questions, and contacts.
3. Assigns each note to a project folder in `~/Downloads/Projects`, for example `bindplane`. Notes that fit no project use the scope `meetings`.
4. Compares the notes with existing memories in all affected scopes and writes new ones, updates earlier meeting notes, or skips duplicates in the `memory` MCP server (`http://127.0.0.1:8765/mcp`). Memories written by agents or by hand are never rewritten. Each note has scope `repo:<project>` and tags `meeting`, the note type, and the meeting date.
5. Saves a readable copy next to the transcript as `transcript_*_notes.md`. If the memory server is unreachable, the notes are still saved locally and the error is written into this file.

Projects whose `AGENTS.md` tells the agent to run `memory_search` with `tag="meeting"`, such as `bindplane`, then show the notes in every new Codex or Copilot session.

Store the ECP API key once in the macOS Keychain (the command prompts for the key):

```bash
security add-generic-password -U -s "ECP LLM API Key" -a ecp -w
```

`ECP_API_KEY` in the environment overrides the Keychain. The memory token is read from `MEMORY_TOKEN` or the existing Keychain item `aiMemory MCP Token`.

Process older transcripts or retry from the command line:

```bash
python3 meeting_notes.py --all                       # every transcript without a notes file
python3 meeting_notes.py --dry-run transcripts/2026/11/18/transcript_110035_original.txt
python3 meeting_notes.py --force transcripts/2026/11/18/transcript_110035_original.txt
```

Optional settings: `MEETING_NOTES_MODEL`, `MEETING_NOTES_FALLBACK_MODEL`, `MEETING_NOTES_PROJECTS_PATH`, `MEMORY_URL`. Set `TEAMS_TRANSLATOR_MEETING_NOTES=0` to turn off the automatic run after `Stop`.
