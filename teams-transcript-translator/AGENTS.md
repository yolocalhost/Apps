# Repository Guidelines

## Project Structure

- `app.py` contains the complete local Tkinter application, screen capture, Apple Vision OCR, and translation flow.
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

Keep the application local-only. Do not add remote upload or telemetry behavior without an explicit requirement. Treat captured transcript text, speaker names, and model paths as potentially sensitive.
