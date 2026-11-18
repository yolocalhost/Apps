# Repository Guidelines

## Memory

`memory` (MCP server at `http://127.0.0.1:8765/mcp`) is the primary store for durable
knowledge across sessions. Use scope `repo:teams-transcript-translator` for work in this repository.

- Call `memory_search` before starting a task, using this repository's name and the topic.
- Call `memory_write` for durable facts, decisions, and preferences the user states, and
  when you settle a question that will matter again. One atomic fact per memory.
- Call `memory_update` rather than writing a duplicate when a stored fact changes.
- Never store secrets, tokens, credentials, customer data, or transient logs.
- Code, configuration, and the documents named in this file stay authoritative. When a
  memory entry conflicts with them, trust the repository and correct the memory.

## Project Structure

- `app.py` contains the complete local Tkinter application, screen capture, Apple Vision OCR, and translation flow.
- `meeting_notes.py` extracts English project notes from saved `*_original.txt` transcripts with the internal ECP LLM and stores them in the `memory` MCP server (scope `repo:<project>`, tag `meeting`). It runs after `Stop` and as a CLI.
- `names.txt` contains optional speaker names, one per line. Keep this file free of secrets.
- `requirements.txt` lists the small set of Python and macOS dependencies.
- `README.md` documents setup and normal use. `.venv/` is local environment state, not source code.

## Development Commands

Create and activate the local environment, install dependencies, and run the app:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 app.py
```

There is no build step. The application requires macOS permissions for Screen Recording and the model path documented in `README.md`.

## Coding Style

Use Python with four spaces for indentation, `snake_case` for functions and variables, and `UPPER_SNAKE_CASE` for constants. Prefer small functions, standard-library features, and the existing Tkinter, Quartz, Vision, and `mlx` dependencies. Keep the implementation direct and readable: do not add frameworks, unnecessary libraries, classes, abstraction layers, or configuration systems for small changes. Do not overengineer. Update `README.md` when setup or user-visible behavior changes.

## Testing

Do not create tests, test files, test frameworks, fixtures, or coverage configuration. For a change that affects behavior, perform a brief manual check by starting the app and exercising the affected flow when practical. Report what was manually checked and any macOS-dependent behavior that could not be verified.

## Commits and Pull Requests

This checkout has no available Git history to establish an existing convention. Use short, imperative commit subjects such as `Improve OCR line matching`, and keep each commit focused. Pull requests should explain the behavior change, list manual verification, note dependency or permission changes, and include a screenshot only when the UI changed. Never commit transcripts, screenshots containing sensitive text, model files, credentials, or local environment directories.

After every repository change, run the relevant verification, commit all intended source and documentation changes with a short imperative subject, and push the commit to the configured Git remote. Never include generated or local runtime data such as `known_words.txt` or `vocabulary_translations.txt`.

## Security and Configuration

Keep OCR and translation local-only. The only remote services allowed are the internal ECP LLM endpoint and the local `memory` server used by `meeting_notes.py`; do not add other upload or telemetry behavior without an explicit requirement. Read the ECP API key and memory token from the environment or macOS Keychain, never from files in this repository. Treat captured transcript text, speaker names, and model paths as potentially sensitive.
