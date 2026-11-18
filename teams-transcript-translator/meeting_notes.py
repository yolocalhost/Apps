"""Extract English project notes from saved transcripts and store them in the memory MCP server."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

LLM_BASE_URL = os.environ.get("MEETING_NOTES_LLM_URL", "https://llm.ecp.erste.ai/v1")
LLM_MODEL = os.environ.get("MEETING_NOTES_MODEL", "ECP-DeepSeek-V4.1-Flash-1017")
LLM_FALLBACK_MODEL = os.environ.get("MEETING_NOTES_FALLBACK_MODEL", "ECP-GLM-5.3-Flash")
LLM_KEYCHAIN = ("ECP LLM API Key", "ecp")
MEMORY_URL = os.environ.get("MEMORY_URL", "http://127.0.0.1:8765/mcp")
MEMORY_KEYCHAIN = ("aiMemory MCP Token", "memory")
PROJECTS_PATH = Path(
    os.environ.get("MEETING_NOTES_PROJECTS_PATH", str(Path.home() / "Downloads" / "Projects"))
).expanduser()
TRANSCRIPTS_PATH = Path(__file__).with_name("transcripts")
FALLBACK_SCOPE = "meetings"
CHUNK_CHARS = 40_000
EXISTING_LIMIT = 50
REQUEST_TIMEOUT = 300
NOTE_TYPES = ("decision", "fact", "action-item", "open-question", "contact")
MEMORY_KINDS = {
    "decision": "decision",
    "fact": "fact",
    "contact": "fact",
    "action-item": "note",
    "open-question": "note",
}
SOURCE_PATTERN = re.compile(r"Source: Teams meeting (\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))?")
DATE_TAG_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
TRANSCRIPT_NAME_PATTERN = re.compile(r"transcript_(\d{2})(\d{2})(\d{2})(?:_\d+)?_original\.txt$")
BADGE_PATTERN = re.compile(r"\s*\b\d{3,8}\s+(?:ED-EXT|ED|EXT|BCR)\w*\s*\|?\s*")
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.?!])\s+|\s+\|\s+")
SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(password|passwd|pwd|secret(?:[ _-]?key)?|token|api[ _-]?key)(\s*[:=]\s*|\s+is\s+)\S+"
    ),
    re.compile(r"(?<=-s )'[^']+'"),
    re.compile(r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{24,}\b"),
)

EXTRACT_PROMPT = """You turn noisy OCR of a Microsoft Teams meeting transcript (English or German)
into durable engineering notes for AI coding agents (Codex, GitHub Copilot) working in the
user's project repositories. The transcript was captured by screen OCR, so it contains repeated
fragments, merged speaker badges, and recognition errors. Infer the intended meaning carefully.

Write every note in clear English, even when the meeting was in German.

Keep only information that will matter in later work sessions:
- decision: something agreed or decided.
- fact: concrete technical or organisational fact (hostnames, IPs, VLANs, ports, versions,
  environments, tickets, constraints, status of something).
- action-item: a task someone must do; include the owner and deadline when stated.
- open-question: an unresolved question or blocker and who can answer it.
- contact: who is responsible for or knows about something.

Rules:
- One atomic, self-contained note per item. A reader without the transcript must understand it.
- Keep concrete identifiers exactly (hostnames, IPs, CIDRs, VLAN ids, ports, versions, ids).
- Use people's names as they appear in the participant list when possible.
- Skip greetings, small talk, audio problems, and anything you cannot read confidently.
- Never include passwords, tokens, API keys, enrollment secrets, or other credentials.
- Do not invent facts. If unsure about a detail, leave it out.
- Assign each note to exactly one project from the project list using its name verbatim, or
  use "{fallback}" when no project fits. Route by what the note is mainly about; general team,
  staffing, budget, or meeting-logistics notes that are not about a listed project go to "{fallback}".
- Prefer fewer, high-value notes over many trivial ones. Return an empty list if nothing is worth keeping.

Projects:
{projects}

Participants: {participants}

Return only JSON:
{{"notes": [{{"project": "...", "type": "decision|fact|action-item|open-question|contact",
"title": "short title, max 80 chars", "content": "1-4 sentences", "tags": ["topic", "..."]}}]}}"""

DEDUP_PROMPT = """You maintain an agent memory store. The new notes come from a meeting held on
{meeting}. Each existing memory shows the date of the meeting it comes from (or "unknown").
Compare each new note with the existing memories (from any project scope) and with earlier new
notes in this list, and decide:
- "skip": the core information is already stored or appears in an earlier new note, even if
  wording, detail level, or project scope differ. Also skip when a memory from a LATER meeting
  covers the same subject, even if it contradicts the new note: the later meeting is more current.
  When in doubt, choose skip.
- "update": an existing memory from the same or an EARLIER meeting covers the same subject and the
  new note materially changes or extends it (new decision, status, owner, date, number,
  identifier). Give that memory's id and merged English content (1-5 sentences) that keeps
  still-valid old details and applies the change; the new note wins where they conflict.
- "new": no existing memory or earlier new note covers the subject.

Existing memories:
{existing}

New notes:
{notes}

Return only JSON with one decision per new note:
{{"decisions": [{{"index": 0, "action": "new|update|skip", "memory_id": "id or null",
"content": "merged content for update, else null"}}]}}"""


def keychain_secret(service, account):
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()


def llm_api_key():
    key = os.environ.get("ECP_API_KEY", "").strip() or keychain_secret(*LLM_KEYCHAIN)
    if not key:
        raise RuntimeError(
            "ECP API key not found. Set ECP_API_KEY or store it with: "
            f"security add-generic-password -U -s '{LLM_KEYCHAIN[0]}' -a {LLM_KEYCHAIN[1]} -w"
        )
    return key


def memory_token():
    token = os.environ.get("MEMORY_TOKEN", "").strip() or keychain_secret(*MEMORY_KEYCHAIN)
    if not token:
        raise RuntimeError("Memory token not found. Set MEMORY_TOKEN or add it to the Keychain.")
    return token


def post_json(url, payload, headers):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.headers, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"{url} returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{url} is not reachable: {exc.reason}") from exc


def parse_json_reply(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("model reply contains no JSON object")
    return json.loads(text[start : end + 1])


def chat_json(prompt, content, model=None):
    models = [model] if model else [LLM_MODEL, LLM_FALLBACK_MODEL]
    errors = []
    for name in dict.fromkeys(models):
        payload = {
            "model": name,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": content},
            ],
            "temperature": 0.1,
            "max_tokens": 16000,
            "response_format": {"type": "json_object"},
            # Without this the ECP reasoning models can spend the whole token budget thinking.
            "chat_template_kwargs": {"enable_thinking": False, "thinking": False},
        }
        try:
            _, body = post_json(
                f"{LLM_BASE_URL}/chat/completions",
                payload,
                {"Authorization": f"Bearer {llm_api_key()}"},
            )
            choice = json.loads(body)["choices"][0]
            reply = choice["message"].get("content") or ""
            if not reply:
                raise ValueError(f"empty reply (finish_reason={choice.get('finish_reason')})")
            return parse_json_reply(reply)
        except (RuntimeError, ValueError, KeyError, IndexError) as exc:
            if "API key not found" in str(exc):
                raise
            errors.append(f"{name}: {exc}")
    raise RuntimeError("LLM request failed. " + " | ".join(errors))


class MemoryClient:
    def __init__(self, url=MEMORY_URL):
        self.url = url
        self.session_id = None
        self.next_id = 1
        self.headers = {
            "Authorization": f"Bearer {memory_token()}",
            "Accept": "application/json, text/event-stream",
        }

    def rpc(self, method, params=None, notify=False):
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            payload["id"] = self.next_id
            self.next_id += 1
        headers = dict(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        response_headers, body = post_json(self.url, payload, headers)
        self.session_id = response_headers.get("mcp-session-id") or self.session_id
        if notify:
            return None
        message = None
        for line in body.splitlines():
            if line.startswith("data:"):
                message = json.loads(line[5:])
        if message is None and body.strip():
            message = json.loads(body)
        if message is None:
            raise RuntimeError(f"memory server returned no reply to {method}")
        if "error" in message:
            raise RuntimeError(f"memory {method} failed: {message['error'].get('message')}")
        return message["result"]

    def connect(self):
        self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "teams-meeting-notes", "version": "1"},
            },
        )
        self.rpc("notifications/initialized", notify=True)
        return self

    def call(self, tool, **arguments):
        result = self.rpc("tools/call", {"name": tool, "arguments": arguments})
        if result.get("isError"):
            text = " ".join(item.get("text", "") for item in result.get("content", []))
            raise RuntimeError(f"{tool} failed: {text}")
        if "structuredContent" in result:
            return result["structuredContent"]
        return json.loads(result["content"][0]["text"])


def available_projects(root=PROJECTS_PATH):
    if not root.is_dir():
        return {}
    projects = {}
    for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_dir() or path.name.startswith("."):
            continue
        projects[path.name] = project_summary(path)
    return projects


def project_summary(path):
    for name in ("AGENTS.md", "README.md"):
        doc = path / name
        if not doc.is_file():
            continue
        try:
            lines = doc.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        text = " ".join(line.strip("# ").strip() for line in lines[:12] if line.strip())
        return text[:240]
    return ""


def project_scope(project, projects):
    return f"repo:{project}" if project in projects else FALLBACK_SCOPE


def load_participants(path=Path(__file__).with_name("names.txt")):
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def comparable(text):
    return " ".join(re.findall(r"[a-z0-9äöüß]+", text.lower()))


def clean_transcript(text):
    """Collapse repeated OCR fragments into one ordered list of unique sentences per speaker."""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    seen = ""
    output = []
    for block in blocks:
        lines = block.splitlines()
        speaker = lines[0].strip() if len(lines) > 1 else ""
        body = " ".join(lines[1:] if speaker else lines)
        body = BADGE_PATTERN.sub(" | ", body)
        sentences = []
        for sentence in SENTENCE_SPLIT_PATTERN.split(body):
            sentence = sentence.strip(" |")
            key = comparable(sentence)
            if len(key) < 12 or key in seen:
                continue
            seen += key + "\n"
            sentences.append(sentence)
        if sentences:
            output.append(f"{speaker}: {' '.join(sentences)}" if speaker else " ".join(sentences))
    return "\n".join(output)


def chunk_text(text, size=CHUNK_CHARS):
    chunks, current = [], []
    length = 0
    for line in text.splitlines():
        if current and length + len(line) > size:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line[:size])
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def redact_secrets(text):
    text = SECRET_PATTERNS[0].sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", text)
    for pattern in SECRET_PATTERNS[1:]:
        text = pattern.sub("<redacted>", text)
    return text


def normalize_note(note, projects):
    if not isinstance(note, dict):
        return None
    content = redact_secrets(str(note.get("content", "")).strip())
    title = redact_secrets(str(note.get("title", "")).strip())[:180]
    if not content or not title:
        return None
    note_type = str(note.get("type", "fact")).strip().lower()
    if note_type not in NOTE_TYPES:
        note_type = "fact"
    project = str(note.get("project", "")).strip()
    matched = {name.lower(): name for name in projects}.get(project.lower())
    tags = note.get("tags") if isinstance(note.get("tags"), list) else []
    return {
        "project": matched or FALLBACK_SCOPE,
        "type": note_type,
        "title": title,
        "content": content,
        "tags": [str(tag).strip().lower() for tag in tags if str(tag).strip()][:8],
    }


def extract_notes(text, projects, participants):
    project_lines = "\n".join(
        f"- {name}: {summary}" if summary else f"- {name}" for name, summary in projects.items()
    )
    prompt = EXTRACT_PROMPT.format(
        projects=project_lines or "(none)",
        participants=", ".join(participants) or "(unknown)",
        fallback=FALLBACK_SCOPE,
    )
    notes = []
    cleaned = clean_transcript(text)
    for index, chunk in enumerate(chunk_text(cleaned), start=1):
        reply = chat_json(prompt, f"Transcript part {index}:\n\n{chunk}")
        for note in reply.get("notes", []):
            normalized = normalize_note(note, projects)
            if normalized:
                notes.append(normalized)
    return notes


def meeting_info(path):
    match = TRANSCRIPT_NAME_PATTERN.search(path.name)
    parts = path.parts[-4:-1]
    date = "-".join(parts) if len(parts) == 3 and all(p.isdigit() for p in parts) else ""
    time = f"{match.group(1)}:{match.group(2)}" if match else ""
    return date, time


def meeting_key(path):
    return " ".join(part for part in meeting_info(path) if part)


def memory_meeting_key(memory):
    """Date and time of the newest meeting a memory comes from, or "" when unknown."""
    keys = [
        f"{date} {time}".strip()
        for date, time in SOURCE_PATTERN.findall(memory.get("content", ""))
    ]
    keys += [tag for tag in memory.get("tags", []) if DATE_TAG_PATTERN.fullmatch(tag)]
    return max(keys, default="")


def dated_title(title, date):
    return f"{date}: {title}" if date else title


def memory_tags(note, date):
    tags = ["meeting", note["type"], *note["tags"]]
    if date:
        tags.append(date)
    return ",".join(dict.fromkeys(tags))


def note_content(note, path):
    date, time = meeting_info(path)
    source = " ".join(part for part in (date, time) if part)
    return f"{note['content']}\n\nSource: Teams meeting {source} ({path.stem.removesuffix('_original')})."


def existing_memories(client, scopes):
    memories = {}
    for scope in scopes:
        reply = client.call("memory_search", query="", scope=scope, limit=EXISTING_LIMIT)
        for item in reply.get("results", []):
            memories.setdefault(item["id"], None)
    return [client.call("memory_get", memory_id=memory_id) for memory_id in memories]


def memory_body(memory):
    return memory["content"].split("\n\nSource:")[0]


def dedup_decisions(notes, existing, meeting):
    if not existing and len(notes) < 2:
        return [{"index": index, "action": "new"} for index in range(len(notes))]
    existing_text = "\n".join(
        f"- id={item['id']} meeting={memory_meeting_key(item) or 'unknown'} "
        f"scope={item['scope']} title={item['title']!r}: {memory_body(item)}"
        for item in existing
    ) or "(none)"
    notes_text = "\n".join(
        f"{index}. [{note['type']}] {note['title']}: {note['content']}"
        for index, note in enumerate(notes)
    )
    reply = chat_json(
        DEDUP_PROMPT.format(meeting=meeting or "unknown", existing=existing_text, notes=notes_text),
        "Decide.",
    )
    meeting_ids = {item["id"] for item in existing if "meeting" in item["tags"]}
    newer_ids = {item["id"] for item in existing if memory_meeting_key(item) > meeting}
    decisions = {}
    for decision in reply.get("decisions", []):
        try:
            index = int(decision.get("index"))
        except (TypeError, ValueError):
            continue
        action = decision.get("action")
        # An older meeting must never overwrite information from a newer one.
        if action == "update" and decision.get("memory_id") in newer_ids:
            action = "skip"
        # Only meeting memories are rewritten; memories written by agents or by hand stay untouched.
        if action == "update" and (
            decision.get("memory_id") not in meeting_ids or not decision.get("content")
        ):
            action = "new"
        if 0 <= index < len(notes) and action in ("new", "update", "skip"):
            decisions[index] = {**decision, "index": index, "action": action}
    return [decisions.get(index, {"index": index, "action": "new"}) for index in range(len(notes))]


def store_notes(notes, path, projects, client):
    date, _ = meeting_info(path)
    for note in notes:
        note["scope"] = project_scope(note["project"], projects)
    scopes = dict.fromkeys([*(note["scope"] for note in notes), FALLBACK_SCOPE])
    existing = existing_memories(client, scopes)
    by_id = {item["id"]: item for item in existing}

    for decision in dedup_decisions(notes, existing, meeting_key(path)):
        note = notes[decision["index"]]
        if decision["action"] == "skip":
            note["memory"] = "skipped (already in memory)"
            continue
        if decision["action"] == "update":
            current = by_id[decision["memory_id"]]
            merged = redact_secrets(str(decision["content"]).strip())
            tags = ",".join(dict.fromkeys([*current["tags"], *memory_tags(note, date).split(",")]))
            result = client.call(
                "memory_update",
                memory_id=current["id"],
                title=dated_title(note["title"], date),
                content=note_content({**note, "content": merged}, path),
                tags=tags,
                expected_revision=current["revision"],
            )
            note["scope"] = current["scope"]
            note["memory"] = f"updated {result['id']}"
            continue
        result = client.call(
            "memory_write",
            content=note_content(note, path),
            title=dated_title(note["title"], date),
            kind=MEMORY_KINDS[note["type"]],
            scope=note["scope"],
            tags=memory_tags(note, date),
        )
        note["memory"] = f"{result['action']} {result['id']}"


def notes_path(path):
    return path.with_name(path.name.replace("_original.txt", "_notes.md"))


def write_local_notes(notes, path, projects, memory_error=None):
    date, time = meeting_info(path)
    lines = [f"# Meeting notes {date} {time}".rstrip(), "", f"Source: `{path.name}`", ""]
    if memory_error:
        lines += [f"Memory not updated: {memory_error}", ""]
    if not notes:
        lines.append("No durable notes found.")
    grouped = {}
    for note in notes:
        grouped.setdefault(note.get("scope") or project_scope(note["project"], projects), []).append(note)
    for scope, scoped_notes in grouped.items():
        lines += [f"## {scope}", ""]
        for note in scoped_notes:
            status = f" _({note['memory']})_" if note.get("memory") else ""
            lines.append(f"- **{note['type']}** — {note['title']}: {note['content']}{status}")
        lines.append("")
    target = notes_path(path)
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def process_transcript(path, dry_run=False):
    path = Path(path)
    projects = available_projects()
    notes = extract_notes(path.read_text(encoding="utf-8"), projects, load_participants())
    if dry_run:
        return notes, None, None
    memory_error = None
    if notes:
        try:
            store_notes(notes, path, projects, MemoryClient().connect())
        except RuntimeError as exc:
            memory_error = str(exc)
    return notes, write_local_notes(notes, path, projects, memory_error), memory_error


def pending_transcripts(root=TRANSCRIPTS_PATH):
    return [
        path
        for path in sorted(root.glob("*/*/*/transcript_*_original.txt"))
        if not notes_path(path).exists()
    ]


def summary_line(notes):
    scopes = sorted({note.get("scope") or note["project"] for note in notes})
    return f"{len(notes)} notes" + (f" ({', '.join(scopes)})" if scopes else "")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Extract English project notes from transcripts into the memory server."
    )
    parser.add_argument("files", nargs="*", type=Path, help="transcript_*_original.txt files")
    parser.add_argument(
        "--all", action="store_true", help="process every transcript without a notes file"
    )
    parser.add_argument("--dry-run", action="store_true", help="print notes, write nothing")
    parser.add_argument(
        "--force", action="store_true", help="process files even when a notes file exists"
    )
    args = parser.parse_args(argv)

    files = list(args.files)
    if args.all:
        files += pending_transcripts()
    if not args.force and not args.dry_run:
        skipped = [path for path in files if notes_path(path).exists()]
        for path in skipped:
            print(f"skip {path} (notes exist, use --force)")
        files = [path for path in files if path not in skipped]
    if not files:
        parser.error("no transcripts to process")

    # Oldest meeting first, so newer meetings always update older notes and never the reverse.
    files = sorted(
        dict.fromkeys(path.resolve() for path in files),
        key=lambda path: (meeting_key(path), path.name),
    )
    failed = False
    for path in files:
        if not path.name.endswith("_original.txt"):
            print(f"skip {path} (not an *_original.txt transcript)")
            continue
        try:
            notes, target, memory_error = process_transcript(path, dry_run=args.dry_run)
        except (OSError, RuntimeError) as exc:
            print(f"error {path}: {exc}", file=sys.stderr)
            failed = True
            continue
        if args.dry_run:
            print(f"# {path}")
            print(json.dumps(notes, indent=2, ensure_ascii=False))
            continue
        print(f"{path}: {summary_line(notes)} -> {target}")
        if memory_error:
            print(f"  memory not updated: {memory_error}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
