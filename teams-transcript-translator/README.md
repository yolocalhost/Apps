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
5. Add speaker or other names to `names.txt`, one name per line.
6. After stopping, click `Learn words` to see the most frequent transcript words and their Slovak translations. Common helper words, names, and words already marked as known are omitted.
7. In `Session words`, choose `Aktuálna session` or an older saved transcript, select the input language, and click `Už viem` after selecting words you already know. The known-word list is stored locally in `known_words.txt` and is reused in future sessions. Completed vocabulary translations are cached in `vocabulary_translations.txt`, so refreshing the list does not translate the same word again.

When the app is closed, the current transcript is saved automatically as two UTF-8 text files: one original and one Slovak translation. Files are stored under `transcripts/YYYY/MM/DD/` with a time-based filename.

The `English` and `German` input selector can be changed while OCR is running. The next screen scan uses the selected language.

Speaker context is taken from names visible in the current scan. If the selected area starts in the middle of a message, that first fragment is left without a speaker until the next speaker name is recognized.

Names from `names.txt` are kept as the canonical speaker labels. OCR characters picked up from a Teams badge or other icon after a known name are discarded; numeric Teams suffixes are kept.

Obvious OCR fragments without normal text, such as `-**+` or `***`, are ignored.

macOS must allow the terminal or Python process to use Screen Recording in System Settings > Privacy & Security.

The application is local-only. It does not send screenshots or transcript text to a remote service.
