from __future__ import annotations

import os
import queue
import re
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk
except ModuleNotFoundError as exc:
    if exc.name == "_tkinter":
        raise SystemExit(
            "Chýba Tkinter. Spusti: brew install python-tk@3.14, "
            "potom znova aktivuj .venv."
        ) from exc
    raise

try:
    import Quartz
    import Vision
except ImportError:
    Quartz = None
    Vision = None


DEFAULT_MODEL_PATH = (
    Path.home()
    / "Downloads"
    / "Resources"
    / "Models"
    / "MiLMMT-46-4B-v0.1-6bit"
)
MODEL_PATH = Path(
    os.environ.get("TEAMS_TRANSLATOR_MODEL_PATH", str(DEFAULT_MODEL_PATH))
).expanduser()
NAMES_PATH = Path(__file__).with_name("names.txt")
TRANSCRIPTS_PATH = Path(__file__).with_name("transcripts")
KNOWN_WORDS_PATH = Path(__file__).with_name("known_words.txt")
VOCABULARY_TRANSLATIONS_PATH = Path(__file__).with_name("vocabulary_translations.txt")
SCAN_INTERVAL = 1.0
VOCABULARY_LIMIT = 20
WORD_PATTERN = re.compile(r"[A-Za-zÀ-ž]+(?:['’-][A-Za-zÀ-ž]+)?")
OCR_TEXT_PATTERN = re.compile(r"[A-Za-zÀ-ž]{2,}")
STOPWORDS = {
    "English": frozenset(
        (
        "a about after all also am an and any are as at be because been before being "
        "but by can could did do does doing for from had has have he her here hers "
        "him his how i if in into is it its just me more most my no not of on or "
        "our ours she should so some than that the their theirs them then there "
        "these they this those to too was we were what when where which who why will "
        "with would you your yours"
        ).split()
    ),
    "German": frozenset(
        (
        "aber alle allem allen aller alles als also an andere auch auf aus bei bin "
        "bis da dann das dass dein dem den der des die dies doch du durch ein eine "
        "einer eines eine es für gegen hat haben hier ich im in ist ja kein keine "
        "mit nach nicht noch nur oder ohne sehr sie sind so über um und vom von vor "
        "war was weil weiter wie wir wo zu zum zur"
        ).split()
    ),
}

events = queue.Queue()
translation_requests = queue.Queue()
translation_worker_lock = threading.Lock()
vocabulary_translation_lock = threading.Lock()
translation_worker = None
vocabulary_translations = {}
state = {
    "running": False,
    "region": None,
    "entries": [],
    "next_entry_id": 0,
    "active_entry_id": None,
    "revision_entry_id": None,
    "current_speaker": "",
    "model": None,
    "tokenizer": None,
    "source_language": "English",
}


def load_names():
    if not NAMES_PATH.exists():
        return []
    return sorted(
        [
            line.strip()
            for line in NAMES_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ],
        key=len,
        reverse=True,
    )


def load_known_words(path=KNOWN_WORDS_PATH):
    if not path.exists():
        return set()

    known_words = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        language, separator, word = line.partition("\t")
        word = word.strip().casefold()
        if separator and language in STOPWORDS and word:
            known_words.add((language, word))
    return known_words


def save_known_word(source_language, word, path=KNOWN_WORDS_PATH):
    key = (source_language, word.casefold().strip())
    if not key[1] or key in load_known_words(path):
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as known_file:
        known_file.write(f"{key[0]}\t{key[1]}\n")
    return True


def load_vocabulary_translations(path=VOCABULARY_TRANSLATIONS_PATH):
    if not path.exists():
        return {}

    translations = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        language, separator, rest = line.partition("\t")
        word, separator, translation = rest.partition("\t")
        word = word.strip().casefold()
        translation = translation.strip()
        if separator and language in STOPWORDS and word and translation:
            translations[(language, word)] = translation
    return translations


def save_vocabulary_translation(source_language, word, translation):
    key = (source_language, word.casefold().strip())
    translation = translation.strip().replace("\t", " ")
    if not key[1] or not translation:
        return False

    with vocabulary_translation_lock:
        if key in vocabulary_translations:
            return False
        VOCABULARY_TRANSLATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with VOCABULARY_TRANSLATIONS_PATH.open("a", encoding="utf-8") as translations_file:
            translations_file.write(f"{key[0]}\t{key[1]}\t{translation}\n")
        vocabulary_translations[key] = translation
    return True


vocabulary_translations.update(load_vocabulary_translations())


def select_region(root, callback):
    overlay = tk.Toplevel(root)
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    screen_x = root.winfo_vrootx()
    screen_y = root.winfo_vrooty()
    overlay.overrideredirect(True)
    overlay.geometry(
        f"{screen_width}x{screen_height}+{screen_x}+{screen_y}"
    )
    overlay.attributes("-topmost", True)
    overlay.attributes("-alpha", 0.25)
    overlay.configure(background="black", cursor="crosshair")

    canvas = tk.Canvas(overlay, background="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    overlay.update_idletasks()
    overlay.lift()
    overlay.focus_force()
    canvas.focus_set()
    overlay.grab_set()
    start = {"x": 0, "y": 0}
    rectangle = {"id": None}

    def begin(event):
        start["x"] = event.x
        start["y"] = event.y
        rectangle["id"] = canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#00ff88", width=3
        )

    def resize(event):
        if rectangle["id"] is not None:
            canvas.coords(
                rectangle["id"], start["x"], start["y"], event.x, event.y
            )

    def finish(event):
        x1 = min(start["x"], event.x)
        y1 = min(start["y"], event.y)
        width = abs(event.x - start["x"])
        height = abs(event.y - start["y"])
        overlay.grab_release()
        overlay.destroy()
        if width >= 10 and height >= 10:
            callback((int(x1), int(y1), int(width), int(height)))

    def cancel(_event):
        overlay.grab_release()
        overlay.destroy()

    canvas.bind("<ButtonPress-1>", begin)
    canvas.bind("<B1-Motion>", resize)
    canvas.bind("<ButtonRelease-1>", finish)
    overlay.bind("<Escape>", cancel)


def capture_image(region):
    if Quartz is None:
        raise RuntimeError(
            "Chýbajú macOS závislosti. Spusti: python3 -m pip install -r requirements.txt"
        )

    x, y, width, height = region
    image = Quartz.CGWindowListCreateImage(
        Quartz.CGRectMake(x, y, width, height),
        Quartz.kCGWindowListOptionOnScreenOnly,
        Quartz.kCGNullWindowID,
        Quartz.kCGWindowImageDefault,
    )
    if image is None:
        raise RuntimeError("Oblasť obrazovky sa nedá nasnímať.")
    return image


def recognize_text(image, source_language):
    if Vision is None:
        raise RuntimeError(
            "Chýba Apple Vision. Spusti: python3 -m pip install -r requirements.txt"
        )

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["en-US" if source_language == "English" else "de-DE"])
    request.setUsesLanguageCorrection_(True)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, {})
    handler.performRequests_error_([request], None)
    observations = list(request.results() or [])
    observations.sort(key=lambda item: (-item.boundingBox().origin.y, item.boundingBox().origin.x))

    lines = []
    for observation in observations:
        candidates = observation.topCandidates_(1)
        if candidates:
            value = str(candidates[0].string()).strip()
            if value:
                lines.append(value)
    return lines


def normalize_line(line):
    clean = re.sub(r"\s+", " ", line).strip()
    if not OCR_TEXT_PATTERN.search(clean):
        return ""
    return clean


def canonical_speaker_label(line, names):
    clean = normalize_line(line)
    if not clean:
        return ""

    timestamp_match = re.match(
        r"^(?P<prefix>\d{1,2}:\d{2}(?::\d{2})?\s+)?(?P<label>.+)$",
        clean,
    )
    prefix = timestamp_match.group("prefix") or ""
    label = timestamp_match.group("label")
    folded_label = label.casefold()
    for name in names:
        folded_name = name.casefold()
        if folded_label == folded_name:
            return prefix + name
        if folded_label.startswith(folded_name + " "):
            suffix = label[len(name):].strip()
            if re.fullmatch(r"\d{3,8}(?:\s+[A-Za-z]{2,6})?", suffix):
                return f"{prefix}{name} {suffix}"
            return prefix + name

    if re.fullmatch(
        r"[A-Za-zÀ-ž][A-Za-zÀ-ž.'-]*(?:\s+[A-Za-zÀ-ž][A-Za-zÀ-ž.'-]*){1,4}"
        r"\s+\d{3,8}(?:\s+[A-Za-zÀ-ž]{2,6})?",
        label,
    ):
        return clean
    return ""


def comparable_line(line):
    return re.sub(r"[^\wÀ-ž]+", " ", line.casefold()).strip()


def line_revision(old_line, new_line):
    old_value = comparable_line(old_line)
    new_value = comparable_line(new_line)
    if not old_value or not new_value:
        return "different"
    if old_value == new_value:
        return "same"
    if new_value.startswith(old_value):
        return "longer"
    if old_value.startswith(new_value):
        return "shorter"
    return "different"


def find_live_entry(line, speaker):
    comparable = comparable_line(line)
    for entry in reversed(state["entries"][-40:]):
        if entry["speaker"] or entry["context"] != speaker:
            continue
        if comparable_line(entry["source"]) == comparable:
            return entry, "same"

    revision_entry_id = state.get("revision_entry_id")
    if revision_entry_id is not None:
        for entry in reversed(state["entries"][-40:]):
            if (
                entry["id"] == revision_entry_id
                and not entry["speaker"]
                and entry["context"] == speaker
            ):
                revision = line_revision(entry["source"], line)
                if revision in ("longer", "shorter"):
                    return entry, revision
    return None, "different"


def add_entry(source, translation, speaker, context):
    entry = {
        "id": state["next_entry_id"],
        "source": source,
        "translation": translation,
        "speaker": speaker,
        "context": context,
    }
    state["next_entry_id"] += 1
    state["entries"].append(entry)
    state["active_entry_id"] = entry["id"]
    return ("upsert", entry["id"], source, translation, speaker, context)


def upsert_ocr_line(line, source_language, names, speaker_context=None):
    clean = normalize_line(line)
    if not clean:
        return None

    speaker_label = canonical_speaker_label(clean, names)
    if speaker_label:
        speaker_context = speaker_label
        state["current_speaker"] = speaker_label
        state["active_entry_id"] = None
        for entry in reversed(state["entries"][-40:]):
            if entry["speaker"] and comparable_line(entry["source"]) == comparable_line(speaker_label):
                return None
        return add_entry(speaker_label, speaker_label, True, speaker_label)

    speaker = (
        state["current_speaker"]
        if speaker_context is None
        else speaker_context
    )
    entry, revision = find_live_entry(clean, speaker)
    if entry is not None:
        state["active_entry_id"] = entry["id"]
        if revision in ("same", "shorter"):
            return None
        entry["source"] = clean
        entry["translation"] = translate_line(clean, source_language, names)
        return (
            "upsert",
            entry["id"],
            entry["source"],
            entry["translation"],
            False,
            entry["context"],
        )

    translated = translate_line(clean, source_language, names)
    return add_entry(clean, translated, False, speaker)


def process_ocr_lines(lines, source_language, names):
    state["revision_entry_id"] = state["active_entry_id"]
    scan_speaker = state["current_speaker"]
    for line in lines:
        clean = normalize_line(line)
        if not clean:
            continue
        speaker_label = canonical_speaker_label(clean, names)
        if speaker_label:
            scan_speaker = speaker_label
        event = upsert_ocr_line(
            clean,
            source_language,
            names,
            speaker_context=scan_speaker,
        )
        if event is not None:
            events.put(event)


def split_speaker(line):
    match = re.match(
        r"^(?P<prefix>(?:(?:\[[^]]+\]|\d{1,2}:\d{2}(?::\d{2})?)\s*)?"
        r"(?P<speaker>[A-Za-zÀ-ž][^:]{0,60}?):\s+)(?P<body>.+)$",
        line,
    )
    if not match:
        return "", line

    speaker = match.group("speaker").strip()
    if len(speaker.split()) > 5 or re.fullmatch(r"\d+", speaker):
        return "", line
    return match.group("prefix"), match.group("body")


def is_speaker_label(line, names):
    return bool(canonical_speaker_label(line, names))


def available_transcripts(root=TRANSCRIPTS_PATH):
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.rglob("*_original.txt") if path.is_file()),
        reverse=True,
    )


def transcript_label(path, root=TRANSCRIPTS_PATH):
    relative = path.relative_to(root).as_posix()
    return relative.removesuffix("_original.txt")


def load_transcript_entries(path, names):
    entries = []
    current_speaker = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = normalize_line(line)
        if not clean:
            continue

        speaker_label = canonical_speaker_label(clean, names)
        speaker = bool(speaker_label)
        if speaker:
            clean = speaker_label
            current_speaker = speaker_label
        entries.append(
            {
                "source": clean,
                "translation": clean if speaker else "",
                "speaker": speaker,
                "context": current_speaker,
            }
        )
    return entries


def transcript_text(entries, field):
    lines = []
    displayed_speaker = ""
    for entry in entries:
        value = entry.get(field, "").strip()
        if not value:
            continue
        if entry.get("speaker") and lines:
            lines.append("")
        if entry.get("speaker"):
            displayed_speaker = entry.get("context") or value
        else:
            context = entry.get("context", "")
            if context and context != displayed_speaker:
                if lines:
                    lines.append("")
                lines.append(context)
                displayed_speaker = context
        lines.append(value)
    return "\n".join(lines).strip() + "\n" if lines else ""


def save_transcript(entries, root=TRANSCRIPTS_PATH, saved_at=None):
    original = transcript_text(entries, "source")
    translation = transcript_text(entries, "translation")
    if not original and not translation:
        return None

    saved_at = saved_at or datetime.now()
    day_path = root / f"{saved_at:%Y}" / f"{saved_at:%m}" / f"{saved_at:%d}"
    day_path.mkdir(parents=True, exist_ok=True)

    stem = f"transcript_{saved_at:%H%M%S}"
    original_path = day_path / f"{stem}_original.txt"
    translation_path = day_path / f"{stem}_translation.txt"
    suffix = 1
    while original_path.exists() or translation_path.exists():
        stem_with_suffix = f"{stem}_{suffix}"
        original_path = day_path / f"{stem_with_suffix}_original.txt"
        translation_path = day_path / f"{stem_with_suffix}_translation.txt"
        suffix += 1

    original_path.write_text(original, encoding="utf-8")
    translation_path.write_text(translation, encoding="utf-8")
    return original_path, translation_path


def protect_names(text, names):
    replacements = {}
    protected = text
    for index, name in enumerate(names):
        token = f"__NAME_{index}__"
        pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
        if pattern.search(protected):
            protected = pattern.sub(token, protected)
            replacements[token] = name
    return protected, replacements


def restore_names(text, replacements):
    result = text
    for token, name in replacements.items():
        result = result.replace(token, name).replace(token.lower(), name)
    return result


def load_model():
    if not MODEL_PATH.is_dir():
        raise RuntimeError(f"Model neexistuje: {MODEL_PATH}")
    from mlx_lm import load

    state["model"], state["tokenizer"] = load(str(MODEL_PATH))


def translate_text_in_worker(text, source_language):
    if state["model"] is None:
        events.put(("status", "Načítavam MLX model..."))
        load_model()

    from mlx_lm import generate

    prompt = (
        f"Translate this from {source_language} to Slovak:\n"
        f"{source_language}: {text}\n"
        "Slovak:"
    )
    encoded = state["tokenizer"].encode(prompt, add_special_tokens=False)
    response = generate(
        state["model"],
        state["tokenizer"],
        prompt=encoded,
        max_tokens=128,
        verbose=False,
    ).strip()
    if "Slovak:" in response:
        response = response.rsplit("Slovak:", 1)[-1].strip()
    return response.splitlines()[0].strip() if response else ""


def translation_loop():
    while True:
        text, source_language, result_queue = translation_requests.get()
        try:
            result = translate_text_in_worker(text, source_language)
        except Exception as exc:
            result = exc
        result_queue.put(result)


def ensure_translation_worker():
    global translation_worker
    with translation_worker_lock:
        if translation_worker is None or not translation_worker.is_alive():
            translation_worker = threading.Thread(
                target=translation_loop,
                name="mlx-translation",
                daemon=True,
            )
            translation_worker.start()


def translate_text(text, source_language):
    ensure_translation_worker()
    result_queue = queue.Queue(maxsize=1)
    translation_requests.put((text, source_language, result_queue))
    result = result_queue.get()
    if isinstance(result, Exception):
        raise result
    return result


def translate_line(line, source_language, names):
    speaker_label = canonical_speaker_label(line, names)
    if speaker_label:
        return speaker_label
    prefix, body = split_speaker(line)
    protected, replacements = protect_names(body, names)
    translated = translate_text(protected, source_language)
    return prefix + restore_names(translated, replacements)


def frequent_words(
    entries,
    source_language,
    names,
    limit=VOCABULARY_LIMIT,
    known_words=None,
):
    counts = Counter()
    first_seen = {}
    known_words = known_words if known_words is not None else load_known_words()
    name_words = {
        word.casefold()
        for name in names
        for word in WORD_PATTERN.findall(name)
    }
    stopwords = STOPWORDS[source_language]

    for entry in entries:
        if entry.get("speaker"):
            continue
        _, body = split_speaker(entry["source"])
        for token in WORD_PATTERN.findall(body):
            word = token.casefold()
            if (
                len(word) < 3
                or word in stopwords
                or word in name_words
                or (source_language, word) in known_words
            ):
                continue
            if word not in first_seen:
                first_seen[word] = len(first_seen)
            counts[word] += 1

    return sorted(
        counts.items(), key=lambda item: (-item[1], first_seen[item[0]])
    )[:limit]


def translate_vocabulary(words, source_language, request_id):
    try:
        for index, (word, _count) in enumerate(words):
            key = (source_language, word.casefold().strip())
            with vocabulary_translation_lock:
                translation = vocabulary_translations.get(key)
            if translation is None:
                translation = translate_text(word, source_language)
                save_vocabulary_translation(source_language, word, translation)
            events.put(
                (
                    "vocabulary-row",
                    request_id,
                    index,
                    translation,
                )
            )
    except Exception as exc:
        events.put(("vocabulary-error", request_id, str(exc)))
        return
    events.put(("vocabulary-done", request_id))


def capture_loop():
    names = load_names()
    while state["running"]:
        started = time.monotonic()
        try:
            source_language = state["source_language"]
            image = capture_image(state["region"])
            lines = recognize_text(image, source_language)
            process_ocr_lines(lines, source_language, names)
            events.put(("status", "OCR beží"))
        except Exception as exc:
            state["running"] = False
            events.put(("error", str(exc)))
            break

        remaining = SCAN_INTERVAL - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)


def start_capture(source_var, status_var, start_button, stop_button, learn_button):
    if state["region"] is None:
        messagebox.showwarning("Oblasť nie je vybraná", "Najprv vyber oblasť transcriptu.")
        return
    if state["running"]:
        return

    state["running"] = True
    state["source_language"] = source_var.get()
    state["entries"].clear()
    state["next_entry_id"] = 0
    state["active_entry_id"] = None
    state["current_speaker"] = ""
    state["revision_entry_id"] = None
    events.put(("reset",))
    status_var.set("Spúšťam OCR...")
    start_button.configure(state="disabled")
    stop_button.configure(state="normal")
    learn_button.configure(state="disabled")
    threading.Thread(target=capture_loop, daemon=True).start()


def stop_capture(status_var, start_button, stop_button, learn_button):
    state["running"] = False
    status_var.set("Zastavené")
    start_button.configure(state="normal")
    stop_button.configure(state="disabled")
    learn_button.configure(state="normal")


def build_ui():
    root = tk.Tk()
    root.title("Teams Transcript Translator")
    root.geometry("1100x650")
    root.minsize(800, 450)

    source_var = tk.StringVar(value="English")
    status_var = tk.StringVar(value="Vyber oblasť transcriptu.")
    region_var = tk.StringVar(value="Oblasť: nevybraná")

    toolbar = ttk.Frame(root, padding=10)
    toolbar.pack(fill="x")
    ttk.Label(toolbar, text="Vstup:").pack(side="left")
    source_menu = ttk.Combobox(
        toolbar, textvariable=source_var, values=("English", "German"), state="readonly", width=10
    )
    source_menu.pack(side="left", padx=(6, 12))

    def source_changed(_event=None):
        state["source_language"] = source_var.get()
        state["active_entry_id"] = None
        status_var.set(f"Vstupný jazyk: {state['source_language']}")

    source_menu.bind("<<ComboboxSelected>>", source_changed)

    def choose_region():
        select_region(root, set_region)

    def set_region(region):
        state["region"] = region
        region_var.set(f"Oblasť: {region[2]} x {region[3]} px")
        status_var.set("Oblasť vybraná. Pripravené na OCR.")

    ttk.Button(toolbar, text="Select area", command=choose_region).pack(side="left")
    start_button = ttk.Button(toolbar, text="Start", command=lambda: start_capture(
        source_var, status_var, start_button, stop_button, learn_button
    ))
    start_button.pack(side="left", padx=(6, 0))
    stop_button = ttk.Button(toolbar, text="Stop", state="disabled", command=lambda: stop_capture(
        status_var, start_button, stop_button, learn_button
    ))
    stop_button.pack(side="left", padx=6)
    learn_button = ttk.Button(
        toolbar, text="Learn words", state="disabled", command=lambda: open_vocabulary()
    )
    learn_button.pack(side="left")
    ttk.Label(toolbar, textvariable=region_var).pack(side="left", padx=8)
    ttk.Label(toolbar, textvariable=status_var).pack(side="right")

    content = ttk.Frame(root, padding=(10, 0, 10, 10))
    content.pack(fill="both", expand=True)
    content.columnconfigure(0, weight=1)
    content.columnconfigure(1, weight=1)
    content.rowconfigure(1, weight=1)
    ttk.Label(content, text="OCR vstup").grid(row=0, column=0, sticky="w", padx=(0, 5))
    ttk.Label(content, text="Slovenský preklad").grid(row=0, column=1, sticky="w", padx=(5, 0))
    input_box = scrolledtext.ScrolledText(content, wrap="word", state="disabled")
    output_box = scrolledtext.ScrolledText(content, wrap="word", state="disabled")
    input_box.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
    output_box.grid(row=1, column=1, sticky="nsew", padx=(5, 0))

    input_box.tag_configure("speaker", font=("Menlo", 13, "bold"))
    output_box.tag_configure("speaker", font=("Menlo", 13, "bold"))

    ui_entries = []
    ui_indexes = {}
    vocabulary = {"id": 0, "window": None, "tree": None, "items": []}

    def open_vocabulary():
        if state["running"]:
            return
        existing = vocabulary["window"]
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return

        saved_sessions = available_transcripts()
        if not state["entries"] and not saved_sessions:
            messagebox.showinfo("Session words", "V tejto session sa nenašli žiadne nové slová.")
            return

        names = load_names()
        current_session = "Aktuálna session"
        session_paths = {current_session: None}
        session_options = [current_session]
        for path in saved_sessions:
            label = transcript_label(path)
            session_paths[label] = path
            session_options.append(label)

        window = tk.Toplevel(root)
        window.title("Session words")
        window.geometry("760x560")
        window.minsize(560, 360)
        window.transient(root)

        controls = ttk.Frame(window)
        controls.pack(fill="x", padx=12, pady=(12, 6))
        session_var = tk.StringVar(value=current_session)
        language_var = tk.StringVar(value=source_var.get())
        ttk.Label(controls, text="Session:").grid(row=0, column=0, sticky="w")
        session_menu = ttk.Combobox(
            controls,
            textvariable=session_var,
            values=session_options,
            state="readonly",
            width=54,
        )
        session_menu.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(controls, text="Jazyk:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        language_menu = ttk.Combobox(
            controls,
            textvariable=language_var,
            values=("English", "German"),
            state="readonly",
            width=12,
        )
        language_menu.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        controls.columnconfigure(1, weight=1)

        summary_var = tk.StringVar()
        ttk.Label(
            window,
            textvariable=summary_var,
        ).pack(anchor="w", padx=12, pady=(0, 6))

        tree = ttk.Treeview(
            window,
            columns=("word", "count", "translation"),
            show="headings",
            selectmode="extended",
        )
        tree.heading("word", text="Slovo")
        tree.heading("count", text="Počet")
        tree.heading("translation", text="Slovenský preklad")
        tree.column("word", width=180, anchor="w")
        tree.column("count", width=70, anchor="center")
        tree.column("translation", width=300, anchor="w")
        tree.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        actions = ttk.Frame(window)
        actions.pack(fill="x", padx=12, pady=(0, 4))
        mark_known_button = ttk.Button(actions, text="Už viem", state="disabled")
        mark_known_button.pack(side="left")
        ttk.Button(actions, text="Obnoviť", command=lambda: refresh_vocabulary()).pack(
            side="left", padx=(8, 0)
        )
        ttk.Label(
            window,
            text="Vyber slová, ktoré už vieš, a klikni na Už viem. Nabudúce sa nezobrazia.",
        ).pack(anchor="w", padx=12, pady=(0, 12))

        vocabulary.update({"window": window, "tree": tree, "items": []})

        def refresh_vocabulary():
            vocabulary["id"] += 1
            request_id = vocabulary["id"]
            selected_path = session_paths[session_var.get()]
            try:
                if selected_path is None:
                    entries = state["entries"]
                else:
                    entries = load_transcript_entries(selected_path, names)
                language = language_var.get()
                rows = frequent_words(entries, language, names)
            except Exception as exc:
                summary_var.set("Session sa nedá načítať.")
                mark_known_button.configure(state="disabled")
                for item in tree.get_children():
                    tree.delete(item)
                vocabulary["items"] = []
                messagebox.showerror("Session words", str(exc))
                return

            for item in tree.get_children():
                tree.delete(item)
            items = []
            for word, count in rows:
                items.append(
                    tree.insert("", "end", values=(word, count, "Prekladám..."))
                )
            vocabulary["items"] = items
            mark_known_button.configure(
                state="normal" if rows else "disabled"
            )
            summary_var.set(
                f"{session_var.get()} - {language} -> Slovak ({len(rows)} nových slov)"
            )
            if not rows:
                status_var.set("V tejto session sa nenašli žiadne nové slová.")
                return

            status_var.set("Prekladám najčastejšie slová...")
            threading.Thread(
                target=translate_vocabulary,
                args=(rows, language, request_id),
                daemon=True,
            ).start()

        def mark_known():
            selected_items = tree.selection()
            if not selected_items:
                status_var.set("Najprv vyber slová, ktoré už vieš.")
                return

            try:
                saved_count = sum(
                    save_known_word(language_var.get(), tree.set(item, "word"))
                    for item in selected_items
                )
            except Exception as exc:
                messagebox.showerror("Uloženie známych slov", str(exc))
                return

            refresh_vocabulary()
            status_var.set(f"Uložené ako známe: {saved_count}.")

        mark_known_button.configure(command=mark_known)
        session_menu.bind("<<ComboboxSelected>>", lambda _event: refresh_vocabulary())
        language_menu.bind("<<ComboboxSelected>>", lambda _event: refresh_vocabulary())

        def close_vocabulary():
            vocabulary.update({"window": None, "tree": None, "items": []})
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", close_vocabulary)
        refresh_vocabulary()

    def render_entries():
        for box in (input_box, output_box):
            box.configure(state="normal")
            box.delete("1.0", "end")

        displayed_speaker = ""
        for index, entry in enumerate(ui_entries):
            if entry["speaker"]:
                if index:
                    input_box.insert("end", "\n")
                    output_box.insert("end", "\n")
                input_box.insert("end", entry["source"] + "\n", "speaker")
                output_box.insert("end", entry["translation"] + "\n", "speaker")
                displayed_speaker = entry["source"]
                continue

            context = entry.get("context", "")
            if context and context != displayed_speaker:
                input_box.insert("end", "\n")
                output_box.insert("end", "\n")
                input_box.insert("end", context + "\n", "speaker")
                output_box.insert("end", context + "\n", "speaker")
                displayed_speaker = context
            input_box.insert("end", "  " + entry["source"] + "\n")
            output_box.insert("end", "  " + entry["translation"] + "\n")

        for box in (input_box, output_box):
            box.see("end")
            box.configure(state="disabled")

    def process_events():
        try:
            while True:
                event = events.get_nowait()
                if event[0] == "status":
                    status_var.set(event[1])
                elif event[0] == "reset":
                    ui_entries.clear()
                    ui_indexes.clear()
                    render_entries()
                elif event[0] == "upsert":
                    entry_id, source, translation, speaker, context = event[1:]
                    entry = {
                        "id": entry_id,
                        "source": source,
                        "translation": translation,
                        "speaker": speaker,
                        "context": context,
                    }
                    if entry_id in ui_indexes:
                        ui_entries[ui_indexes[entry_id]] = entry
                    else:
                        ui_indexes[entry_id] = len(ui_entries)
                        ui_entries.append(entry)
                    render_entries()
                elif event[0] == "error":
                    status_var.set("Chyba")
                    start_button.configure(state="normal")
                    stop_button.configure(state="disabled")
                    learn_button.configure(state="normal")
                    messagebox.showerror("Aplikácia", event[1])
                elif event[0] == "vocabulary-row":
                    _, request_id, index, translation = event
                    if (
                        request_id == vocabulary["id"]
                        and vocabulary["tree"] is not None
                        and vocabulary["tree"].winfo_exists()
                        and index < len(vocabulary["items"])
                    ):
                        vocabulary["tree"].set(
                            vocabulary["items"][index], "translation", translation
                        )
                elif event[0] == "vocabulary-done":
                    _, request_id = event
                    if request_id == vocabulary["id"]:
                        status_var.set("Slová pripravené.")
                elif event[0] == "vocabulary-error":
                    _, request_id, error = event
                    if request_id == vocabulary["id"]:
                        status_var.set("Preklad slov zlyhal")
                        messagebox.showerror("Session words", error)
        except queue.Empty:
            pass
        root.after(200, process_events)

    def close():
        state["running"] = False
        try:
            save_transcript(state["entries"])
        except Exception as exc:
            messagebox.showerror("Uloženie transcriptu", str(exc))
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(200, process_events)
    return root


if __name__ == "__main__":
    build_ui().mainloop()
