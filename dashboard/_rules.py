"""Instruction rules: grade recorded turns against user-declared, checkable rules.

The plugin never reads SOUL.md or guesses what an instruction means. The user
declares a rule from a fixed set of templates (each a sentence with blanks),
and this module walks the recorded conversation turns and answers, per turn,
whether the model followed it. Verdicts are deterministic and every failure
points at the turn that produced it. Scores are grouped by the session's
model and ranked with the same Wilson upper bound the AI Models tab uses, so
a model with four turns cannot outrank one with forty on luck.

Rules travel from the desktop as a JSON query parameter (`?rules=`), like
budgets: the backend stores nothing and writes nothing.
"""

from __future__ import annotations

import fnmatch
import json
import re
import sqlite3
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

try:
    from ._common import *
    from ._reliability import _wilson_upper_bound
except ImportError:  # pragma: no cover - direct Hermes file loading
    from _common import *
    from _reliability import _wilson_upper_bound

RULES_MAX_RULES = 40
RULES_MAX_PARAM_CHARS = 24_000
RULES_MAX_SESSIONS = 600
RULES_MAX_EXAMPLES = 6
RULES_DEFAULT_MIN_SAMPLES = 5
RULES_TEXT_CAP = 20_000

_RULE_TYPES = (
    "require_tool",
    "forbid_tool",
    "tool_order",
    "forbid_text",
    "require_text_count",
    "language_match",
    "forbid_tool_mention",
    "path_boundary",
)

RULE_TEMPLATES: List[Dict[str, Any]] = [
    {
        "type": "require_tool",
        "label": "Every reply must call a tool",
        "sentence": "Every reply must call the tool ___",
        "applies_to": "turns where the assistant produced a reply",
        "fields": [
            {"key": "tool", "label": "Tool name", "placeholder": "text_to_speech", "required": True, "hint": "Glob patterns work: browser_*"},
            {"key": "position", "label": "When", "kind": "select", "options": [["any", "anywhere in the reply"], ["before_final", "before the final answer text"]], "default": "any"},
            {"key": "must_succeed", "label": "The call must also succeed", "kind": "bool", "default": False, "hint": "Off measures the model; on also fails turns where the tool itself errored."},
        ],
    },
    {
        "type": "forbid_tool",
        "label": "A tool must never be used",
        "sentence": "The tool ___ must never be used",
        "applies_to": "turns with at least one tool call",
        "fields": [
            {"key": "tool", "label": "Tool name", "placeholder": "computer_use", "required": True, "hint": "Glob patterns work: browser_*"},
        ],
    },
    {
        "type": "tool_order",
        "label": "Try one tool before another",
        "sentence": "Before using the tool ___, the tool ___ must have been tried",
        "applies_to": "turns that used the first tool",
        "fields": [
            {"key": "tool", "label": "Restricted tool", "placeholder": "browser_*", "required": True},
            {"key": "before", "label": "Must have been tried first", "placeholder": "web_search", "required": True},
            {"key": "scope", "label": "Look back", "kind": "select", "options": [["turn", "within the same turn"], ["session", "anywhere earlier in the session"]], "default": "turn"},
        ],
    },
    {
        "type": "forbid_text",
        "label": "Replies must never contain",
        "sentence": "Replies must never contain ___",
        "applies_to": "turns where the assistant wrote visible text",
        "fields": [
            {"key": "patterns", "label": "Forbidden text", "kind": "list", "placeholder": "D:\\\nsaved to", "required": True, "hint": "One per line, case-insensitive. Tick regex to use patterns."},
            {"key": "regex", "label": "Treat as regular expressions", "kind": "bool", "default": False},
        ],
    },
    {
        "type": "require_text_count",
        "label": "Replies must contain something exactly N times",
        "sentence": "Replies must contain ___ exactly ___ times",
        "applies_to": "turns where the assistant wrote visible text",
        "fields": [
            {"key": "pattern", "label": "Text", "placeholder": "Abu Omar | أبو عمر", "required": True, "hint": "Case-insensitive. Separate accepted spellings with |."},
            {"key": "count", "label": "Times", "kind": "number", "default": 1},
        ],
    },
    {
        "type": "language_match",
        "label": "Reply in the user's language",
        "sentence": "The reply language must match the user's language",
        "applies_to": "turns where both sides wrote enough text to tell (Arabic, Turkish, English)",
        "fields": [],
    },
    {
        "type": "forbid_tool_mention",
        "label": "Tool calls must never mention",
        "sentence": "Tool calls and their results must never mention ___",
        "applies_to": "turns with at least one tool call",
        "fields": [
            {"key": "patterns", "label": "Forbidden words", "kind": "list", "placeholder": "Maaden\nAINOTE", "required": True, "hint": "One per line, case-insensitive; checked in call arguments and results."},
        ],
    },
    {
        "type": "path_boundary",
        "label": "Tools must stay inside folders",
        "sentence": "The tools ___ must not touch paths outside ___",
        "applies_to": "turns where those tools received a path in a path-like argument (file content is not scanned)",
        "fields": [
            {"key": "tools", "label": "Tools", "kind": "list", "placeholder": "terminal\nwrite_file\npatch", "default": "terminal\nwrite_file\npatch\nread_file", "hint": "One per line, globs allowed."},
            {"key": "roots", "label": "Allowed folders", "kind": "list", "placeholder": "D:\\Projects\\AssetNerve", "required": True, "hint": "One per line. Paths under any of these pass."},
        ],
    },
]

_TEMPLATE_BY_TYPE = {template["type"]: template for template in RULE_TEMPLATES}


# ── Rule parsing ──────────────────────────────────────────────────────────


def _rules_list_param(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        items = re.split(r"[\r\n,]+", str(value or ""))
    output = []
    for item in items:
        text = item.strip()
        if text and text not in output:
            output.append(text[:400])
        if len(output) >= 40:
            break
    return output


def _parse_rules_param(raw: str) -> List[Dict[str, Any]]:
    """Validate the desktop's rule list. Unknown types and empty rules are dropped."""
    text = str(raw or "").strip()
    if not text or len(text) > RULES_MAX_PARAM_CHARS:
        return []
    try:
        loaded = json.loads(text)
    except (TypeError, ValueError):
        return []
    if isinstance(loaded, Mapping):
        loaded = loaded.get("rules")
    if not isinstance(loaded, list):
        return []
    rules: List[Dict[str, Any]] = []
    seen_ids = set()
    for index, item in enumerate(loaded):
        if not isinstance(item, Mapping):
            continue
        rule_type = str(item.get("type") or "").strip()
        template = _TEMPLATE_BY_TYPE.get(rule_type)
        if template is None:
            continue
        rule_id = re.sub(r"[^A-Za-z0-9_.:-]", "", str(item.get("id") or f"rule-{index + 1}"))[:60] or f"rule-{index + 1}"
        if rule_id in seen_ids:
            rule_id = f"{rule_id}-{index + 1}"
        seen_ids.add(rule_id)
        raw_params = item.get("params") if isinstance(item.get("params"), Mapping) else {}
        params: Dict[str, Any] = {}
        valid = True
        for field in template["fields"]:
            key = field["key"]
            kind = field.get("kind", "text")
            value = raw_params.get(key, field.get("default"))
            if kind == "list":
                value = _rules_list_param(value)
                if field.get("required") and not value:
                    valid = False
            elif kind == "bool":
                value = bool(value) if not isinstance(value, str) else value.strip().lower() in {"1", "true", "yes", "on"}
            elif kind == "number":
                try:
                    value = int(float(value))
                except (TypeError, ValueError):
                    value = int(field.get("default") or 0)
                value = max(0, min(100, value))
            elif kind == "select":
                allowed = [option[0] for option in field.get("options", [])]
                value = str(value or field.get("default") or "").strip()
                if value not in allowed:
                    value = str(field.get("default") or (allowed[0] if allowed else ""))
            else:
                value = _clean_text(value, 400) or ""
                if field.get("required") and not value:
                    valid = False
            params[key] = value
        if not valid:
            continue
        rules.append(
            {
                "id": rule_id,
                "name": _clean_text(item.get("name"), 120) or template["label"],
                "type": rule_type,
                "params": params,
                "profile": _clean_text(item.get("profile"), 80) or None,
                "enabled": item.get("enabled", True) is not False,
            }
        )
        if len(rules) >= RULES_MAX_RULES:
            break
    return rules


# ── Turn building ─────────────────────────────────────────────────────────


def _message_text(content: Any) -> str:
    """Visible text of a message: JSON-quoted strings and part lists unwrapped."""
    if content is None:
        return ""
    if isinstance(content, (list, dict)):
        loaded: Any = content
    else:
        text = str(content)
        stripped = text.strip()
        loaded = None
        if stripped[:1] in {'"', "[", "{"}:
            try:
                loaded = json.loads(stripped)
            except (TypeError, ValueError):
                loaded = None
        if loaded is None:
            return text
    if isinstance(loaded, str):
        return loaded
    if isinstance(loaded, list):
        parts = []
        for part in loaded:
            if isinstance(part, Mapping):
                if part.get("type") in {"text", "output_text", "input_text"} or "text" in part:
                    parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(item for item in parts if item)
    if isinstance(loaded, Mapping):
        return str(loaded.get("text") or loaded.get("content") or "")
    return str(content)


_MARKER_LINE_RE = re.compile(r"^\s*(\[\[[a-z_]+\]\]|MEDIA:.*)\s*$", re.IGNORECASE)


def _visible_text(text: str) -> str:
    """Drop Hermes delivery markers ([[audio_as_voice]], MEDIA: paths) from reply text."""
    return "\n".join(line for line in str(text or "").splitlines() if not _MARKER_LINE_RE.match(line)).strip()


def _tool_calls_from_row(raw: Any) -> List[Dict[str, str]]:
    calls: List[Dict[str, str]] = []
    loaded = _parse_json(raw, None)
    if not isinstance(loaded, list):
        return calls
    for item in loaded:
        if not isinstance(item, Mapping):
            continue
        function = item.get("function") if isinstance(item.get("function"), Mapping) else item
        name = str(function.get("name") or item.get("name") or "").strip()
        if not name:
            continue
        arguments = function.get("arguments", item.get("arguments", ""))
        if not isinstance(arguments, str):
            try:
                arguments = json.dumps(arguments, ensure_ascii=False)
            except (TypeError, ValueError):
                arguments = str(arguments)
        calls.append(
            {
                "name": name,
                "arguments": arguments[:RULES_TEXT_CAP],
                "call_id": str(item.get("call_id") or item.get("id") or ""),
            }
        )
    return calls


def _build_turns(session: Mapping[str, Any], messages: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Split one session's ordered messages into user-initiated turns.

    A turn starts at a user message and runs until the next one. Events keep
    their order: ('text', str) for assistant text, ('call', name, arguments,
    call_id) for a tool call, ('result', name, content, failed, call_id) for
    a tool result. Assistant-only preambles before the first user message
    are dropped: nobody asked for them.
    """
    turns: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for row in messages:
        role = str(row.get("role") or "").lower()
        if role == "user":
            if current is not None:
                turns.append(current)
            current = {
                "index": len(turns) + 1,
                "session_id": str(session.get("id") or ""),
                "model": str(session.get("model") or "") or "unknown",
                "profile": session.get("profile"),
                "timestamp": _number(row.get("timestamp")),
                "user_text": _message_text(row.get("content"))[:RULES_TEXT_CAP],
                "events": [],
            }
            continue
        if current is None:
            continue
        if role == "assistant":
            text = _visible_text(_message_text(row.get("content")))
            if text:
                current["events"].append(("text", text[:RULES_TEXT_CAP]))
            for call in _tool_calls_from_row(row.get("tool_calls")):
                current["events"].append(("call", call["name"], call["arguments"], call["call_id"]))
        elif role == "tool":
            content = str(row.get("content") or "")[:RULES_TEXT_CAP]
            failed = _is_failure(
                role="tool",
                content=content,
                finish_reason=row.get("finish_reason"),
                effect_disposition=row.get("effect_disposition"),
            )
            current["events"].append(("result", str(row.get("tool_name") or ""), content, bool(failed), str(row.get("tool_call_id") or "")))
    if current is not None:
        turns.append(current)
    return turns


def _turn_calls(turn: Mapping[str, Any]) -> List[Tuple[int, str, str, str]]:
    return [(index, event[1], event[2], event[3]) for index, event in enumerate(turn["events"]) if event[0] == "call"]


def _turn_results(turn: Mapping[str, Any]) -> List[Tuple[int, str, str, bool, str]]:
    return [(index, event[1], event[2], event[3], event[4]) for index, event in enumerate(turn["events"]) if event[0] == "result"]


def _turn_text(turn: Mapping[str, Any]) -> str:
    return "\n".join(event[1] for event in turn["events"] if event[0] == "text").strip()


def _turn_has_output(turn: Mapping[str, Any]) -> bool:
    return any(event[0] in {"text", "call"} for event in turn["events"])


def _tool_matches(pattern: str, name: str) -> bool:
    return fnmatch.fnmatchcase(str(name or "").lower(), str(pattern or "").lower())


def _any_tool_matches(patterns: Iterable[str], name: str) -> bool:
    return any(_tool_matches(pattern, name) for pattern in patterns)


# ── Language detection ────────────────────────────────────────────────────

_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")
_LATIN_RE = re.compile(r"[A-Za-z\u00C0-\u024F]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_CJK_RE = re.compile(r"[\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]")
_TURKISH_CHARS_RE = re.compile(r"[ğĞışŞİ]")
_TURKISH_WORDS = {"ve", "bir", "için", "değil", "ile", "bu", "çok", "nasıl", "evet", "hayır", "teşekkür", "teşekkürler", "lütfen", "var", "yok", "ama", "gibi", "daha", "olarak", "merhaba", "tamam", "sonra", "şimdi", "burada", "nerede"}
_ENGLISH_WORDS = {"the", "and", "is", "are", "you", "for", "with", "this", "that", "to", "of", "in", "it", "on", "your", "here", "have", "will", "can", "not"}
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _detect_language(text: str) -> Optional[str]:
    """arabic | turkish | english | latin | other | mixed, or None when too short."""
    sample = str(text or "")
    counts = {
        "arabic": len(_ARABIC_RE.findall(sample)),
        "latin": len(_LATIN_RE.findall(sample)),
        "cyrillic": len(_CYRILLIC_RE.findall(sample)),
        "cjk": len(_CJK_RE.findall(sample)),
    }
    total = sum(counts.values())
    if total < 12:
        return None
    script, share = max(counts.items(), key=lambda item: item[1])
    minority = total - share
    # A second script with a real presence (a fifth of the letters, or a
    # sentence's worth) makes the text mixed rather than "mostly X".
    if minority >= 20 or minority / total >= 0.2:
        return "mixed"
    if script == "arabic":
        return "arabic"
    if script != "latin":
        return "other"
    words = {word.lower() for word in _WORD_RE.findall(sample)}
    turkish_score = len(words & _TURKISH_WORDS) + (2 if _TURKISH_CHARS_RE.search(sample) else 0)
    english_score = len(words & _ENGLISH_WORDS)
    if turkish_score > english_score and turkish_score >= 1:
        return "turkish"
    if english_score > turkish_score and english_score >= 1:
        return "english"
    return "latin"


# ── Path extraction ───────────────────────────────────────────────────────

# Segments may contain spaces ("D:\Bandar Vault\x"); a quote, comma, or line
# break ends the path. JSON-escaped backslashes are collapsed before matching.
_WIN_PATH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\\/\"',<>|\r\n\t]+[\\/]?)+")
# A POSIX path must not be the tail of a URL or namespace ("//schemas.microsoft.com/...").
_POSIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9:./])/(?:[\w.\-]+/)+[\w.\-]*")
_PATH_KEY_RE = re.compile(r"(path|file|dir|folder|cwd|command|cmd|target|dest|src|source|script|args|location|root|workdir)", re.IGNORECASE)


def _path_bearing_text(arguments: str) -> str:
    """The parts of a tool call worth scanning for paths.

    JSON arguments are scanned only under path-like keys (path, file, cwd,
    command, ...), so a file's *content* — which legitimately quotes other
    paths — never trips a boundary rule. Non-JSON arguments are scanned whole.
    """
    loaded = _parse_json(arguments, None)
    if not isinstance(loaded, (dict, list)):
        return str(arguments or "")
    pieces: List[str] = []

    def walk(node: Any, key_matches: bool) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, key_matches or bool(_PATH_KEY_RE.search(str(key))))
        elif isinstance(node, list):
            for value in node:
                walk(value, key_matches)
        elif key_matches and isinstance(node, str):
            pieces.append(node)

    walk(loaded, False)
    return "\n".join(pieces)


def _normalise_path(path: str) -> str:
    text = str(path or "").strip().strip("'\"").replace("\\\\", "\\").replace("\\", "/").rstrip("/").lower()
    return re.sub(r"/+", "/", text)


def _paths_in_text(text: str) -> List[str]:
    found: List[str] = []
    text = str(text or "").replace("\\\\", "\\")
    for match in _WIN_PATH_RE.findall(text) + _POSIX_PATH_RE.findall(text):
        normalised = _normalise_path(match)
        if normalised and normalised not in found:
            found.append(normalised)
    return found[:40]


# ── Evaluators: each returns None (not applicable), (True, None) or (False, reason) ──


def _eval_require_tool(params: Mapping[str, Any], turn: Mapping[str, Any], history: List[Dict[str, Any]]) -> Optional[Tuple[bool, Optional[str]]]:
    if not _turn_has_output(turn):
        return None
    pattern = str(params.get("tool") or "")
    calls = _turn_calls(turn)
    matching = [call for call in calls if _tool_matches(pattern, call[1])]
    if not matching:
        names = sorted({call[1] for call in calls})
        detail = f"called {', '.join(names[:6])}" if names else "replied with text only"
        return False, f"no {pattern} call — {detail}"
    if params.get("position") == "before_final":
        text_indexes = [index for index, event in enumerate(turn["events"]) if event[0] == "text"]
        if text_indexes and not any(call[0] < text_indexes[-1] for call in matching):
            return False, f"{pattern} was called only after the final answer text"
    if params.get("must_succeed"):
        results = {result[4]: result for result in _turn_results(turn) if result[4]}
        by_name = [result for result in _turn_results(turn) if _tool_matches(pattern, result[1])]
        succeeded = False
        failure_note = None
        for call in matching:
            result = results.get(call[3])
            if result is None:
                candidates = [item for item in by_name if item[0] > call[0]]
                result = candidates[0] if candidates else None
            if result is None or not result[3]:
                succeeded = True
                break
            failure_note = _clean_text(result[2], 140)
        if not succeeded:
            return False, f"{pattern} was called but failed: {failure_note or 'error result'}"
    return True, None


def _eval_forbid_tool(params: Mapping[str, Any], turn: Mapping[str, Any], history: List[Dict[str, Any]]) -> Optional[Tuple[bool, Optional[str]]]:
    calls = _turn_calls(turn)
    if not calls:
        return None
    pattern = str(params.get("tool") or "")
    hits = [call[1] for call in calls if _tool_matches(pattern, call[1])]
    if hits:
        return False, f"used {hits[0]}" + (f" ({len(hits)} calls)" if len(hits) > 1 else "")
    return True, None


def _eval_tool_order(params: Mapping[str, Any], turn: Mapping[str, Any], history: List[Dict[str, Any]]) -> Optional[Tuple[bool, Optional[str]]]:
    restricted = str(params.get("tool") or "")
    required = str(params.get("before") or "")
    calls = _turn_calls(turn)
    restricted_calls = [call for call in calls if _tool_matches(restricted, call[1])]
    if not restricted_calls:
        return None
    earlier_in_session = False
    if params.get("scope") == "session":
        earlier_in_session = any(
            _tool_matches(required, call[1]) for previous in history for call in _turn_calls(previous)
        )
    for call in restricted_calls:
        tried_first = earlier_in_session or any(
            _tool_matches(required, other[1]) and other[0] < call[0] for other in calls
        )
        if not tried_first:
            return False, f"{call[1]} used without trying {required} first"
    return True, None


def _compile_patterns(params: Mapping[str, Any]) -> List[Tuple[str, Any]]:
    compiled: List[Tuple[str, Any]] = []
    use_regex = bool(params.get("regex"))
    for pattern in params.get("patterns") or []:
        if use_regex:
            try:
                compiled.append((pattern, re.compile(pattern, re.IGNORECASE)))
                continue
            except re.error:
                pass
        compiled.append((pattern, None))
    return compiled


def _first_pattern_hit(compiled: List[Tuple[str, Any]], text: str) -> Optional[str]:
    lowered = text.lower()
    for pattern, regex in compiled:
        if regex is not None:
            if regex.search(text):
                return pattern
        elif pattern.lower() in lowered:
            return pattern
    return None


def _eval_forbid_text(params: Mapping[str, Any], turn: Mapping[str, Any], history: List[Dict[str, Any]]) -> Optional[Tuple[bool, Optional[str]]]:
    text = _turn_text(turn)
    if not text:
        return None
    hit = _first_pattern_hit(_compile_patterns(params), text)
    if hit:
        return False, f"reply contains “{hit}”"
    return True, None


def _eval_require_text_count(params: Mapping[str, Any], turn: Mapping[str, Any], history: List[Dict[str, Any]]) -> Optional[Tuple[bool, Optional[str]]]:
    text = _turn_text(turn)
    if not text:
        return None
    pattern = str(params.get("pattern") or "")
    expected = _integer(params.get("count"))
    # "Abu Omar | أبو عمر" counts either spelling: alternatives are separated by |.
    alternatives = [item.strip().lower() for item in pattern.split("|") if item.strip()]
    lowered = text.lower()
    count = sum(lowered.count(item) for item in alternatives)
    if count != expected:
        return False, f"“{pattern}” appears {count} time{'s' if count != 1 else ''}, expected {expected}"
    return True, None


def _eval_language_match(params: Mapping[str, Any], turn: Mapping[str, Any], history: List[Dict[str, Any]]) -> Optional[Tuple[bool, Optional[str]]]:
    user_language = _detect_language(turn.get("user_text") or "")
    if user_language not in {"arabic", "turkish", "english"}:
        return None
    reply_language = _detect_language(_turn_text(turn))
    if reply_language is None or reply_language == "latin":
        return None
    if reply_language == user_language:
        return True, None
    return False, f"user wrote {user_language}, reply was {reply_language}"


def _eval_forbid_tool_mention(params: Mapping[str, Any], turn: Mapping[str, Any], history: List[Dict[str, Any]]) -> Optional[Tuple[bool, Optional[str]]]:
    calls = _turn_calls(turn)
    if not calls:
        return None
    compiled = _compile_patterns({"patterns": params.get("patterns") or []})
    for call in calls:
        hit = _first_pattern_hit(compiled, call[2])
        if hit:
            return False, f"{call[1]} arguments mention “{hit}”"
    for result in _turn_results(turn):
        hit = _first_pattern_hit(compiled, result[2])
        if hit:
            return False, f"{result[1] or 'tool'} result mentions “{hit}”"
    return True, None


def _eval_path_boundary(params: Mapping[str, Any], turn: Mapping[str, Any], history: List[Dict[str, Any]]) -> Optional[Tuple[bool, Optional[str]]]:
    tools = params.get("tools") or []
    roots = [_normalise_path(root) for root in params.get("roots") or []]
    roots = [root for root in roots if root]
    if not roots:
        return None
    applicable = False
    for call in _turn_calls(turn):
        if tools and not _any_tool_matches(tools, call[1]):
            continue
        paths = _paths_in_text(_path_bearing_text(call[2]))
        if not paths:
            continue
        applicable = True
        for path in paths:
            if not any(path == root or path.startswith(root + "/") for root in roots):
                return False, f"{call[1]} touched {path}"
    return (True, None) if applicable else None


_EVALUATORS = {
    "require_tool": _eval_require_tool,
    "forbid_tool": _eval_forbid_tool,
    "tool_order": _eval_tool_order,
    "forbid_text": _eval_forbid_text,
    "require_text_count": _eval_require_text_count,
    "language_match": _eval_language_match,
    "forbid_tool_mention": _eval_forbid_tool_mention,
    "path_boundary": _eval_path_boundary,
}


# ── Loading and scoring ───────────────────────────────────────────────────


def _rules_load_turns(
    connection: sqlite3.Connection,
    period_start: float,
    period_end: Optional[float],
    *,
    union: bool,
    single_profile: Optional[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    period_sql, params = _period_sql("s.started_at", period_start, period_end)
    profile_column = ", s.__profile AS profile" if union else ", s.profile_name AS profile"
    session_rows = connection.execute(
        f"""
        SELECT s.id, s.model, s.title, s.display_name, s.started_at{profile_column}
        FROM sessions s
        WHERE {period_sql} AND coalesce(s.hidden,0)=0 AND coalesce(s.archived,0)=0
        ORDER BY s.started_at DESC
        LIMIT {RULES_MAX_SESSIONS + 1}
        """,
        tuple(params),
    ).fetchall()
    sessions = [_row_dict(row) for row in session_rows]
    truncated = len(sessions) > RULES_MAX_SESSIONS
    sessions = sessions[:RULES_MAX_SESSIONS]
    for session in sessions:
        session["profile"] = str(session.get("profile") or single_profile or "") or None
        session["title"] = _clean_text(session.get("title") or session.get("display_name"), 120) or str(session.get("id") or "")
    by_id = {str(session["id"]): session for session in sessions}
    turns: List[Dict[str, Any]] = []
    ids = list(by_id)
    for chunk_start in range(0, len(ids), 400):
        chunk = ids[chunk_start : chunk_start + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            f"""
            SELECT m.session_id, m.role, substr(m.content,1,{RULES_TEXT_CAP}) AS content, m.tool_calls,
                   m.tool_name, m.tool_call_id, m.timestamp, m.finish_reason, m.effect_disposition, m.id
            FROM messages m
            WHERE coalesce(m.active,1)=1 AND m.session_id IN ({placeholders})
            ORDER BY m.session_id, m.timestamp, m.id
            """,
            tuple(chunk),
        ).fetchall()
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            material = _row_dict(row)
            grouped[str(material.get("session_id"))].append(material)
        for session_id, messages in grouped.items():
            session = by_id.get(session_id)
            if session is None:
                continue
            for turn in _build_turns(session, messages):
                turn["title"] = session["title"]
                turns.append(turn)
    coverage = {
        "sessions": len(sessions),
        "sessions_truncated": truncated,
        "turns": len(turns),
    }
    return turns, coverage


def _rules_evaluate_sync(
    rules: List[Dict[str, Any]],
    days: int = 30,
    start_at: Optional[float] = None,
    end_at: Optional[float] = None,
    min_samples: int = RULES_DEFAULT_MIN_SAMPLES,
) -> Dict[str, Any]:
    period_start, period_end = _period_bounds(days, start_at, end_at)
    min_samples = max(1, min(200, _integer(min_samples) or RULES_DEFAULT_MIN_SAMPLES))
    started = time.time()
    with _database() as db:
        connection = _db_connection(db)
        union = bool(getattr(db, "union_profiles", None))
        scope = _get_profile_scope()
        single_profile = scope[0] if scope and len(scope) == 1 else (None if scope else _serving_profile_name_safe())
        turns, coverage = _rules_load_turns(connection, period_start, period_end, union=union, single_profile=single_profile)

    by_session: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for turn in turns:
        by_session[turn["session_id"]].append(turn)

    rule_results: List[Dict[str, Any]] = []
    model_totals: Dict[str, Dict[str, int]] = defaultdict(lambda: {"applicable": 0, "failed": 0, "rules": 0})
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        evaluator = _EVALUATORS.get(rule["type"])
        if evaluator is None:
            continue
        per_model: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"applicable": 0, "passed": 0, "failed": 0, "examples": []})
        skipped_profile = 0
        for session_turns in by_session.values():
            history: List[Dict[str, Any]] = []
            for turn in session_turns:
                if rule.get("profile") and str(turn.get("profile") or "") != rule["profile"]:
                    skipped_profile += 1
                    history.append(turn)
                    continue
                verdict = evaluator(rule["params"], turn, history)
                history.append(turn)
                if verdict is None:
                    continue
                bucket = per_model[turn["model"]]
                bucket["applicable"] += 1
                if verdict[0]:
                    bucket["passed"] += 1
                else:
                    bucket["failed"] += 1
                    if len(bucket["examples"]) < RULES_MAX_EXAMPLES:
                        bucket["examples"].append(
                            {
                                "session_id": turn["session_id"],
                                "title": turn["title"],
                                "turn": turn["index"],
                                "timestamp": turn["timestamp"],
                                "profile": turn.get("profile"),
                                "reason": verdict[1],
                                "search": f"id:{turn['session_id']}",
                            }
                        )
        models: List[Dict[str, Any]] = []
        for model, bucket in per_model.items():
            applicable = bucket["applicable"]
            failed = bucket["failed"]
            upper = _wilson_upper_bound(failed, applicable)
            models.append(
                {
                    "model": model,
                    "applicable": applicable,
                    "passed": bucket["passed"],
                    "failed": failed,
                    "pass_rate": round(bucket["passed"] / applicable, 4) if applicable else None,
                    "failure_rate_upper_bound_95": round(upper, 4) if upper is not None else None,
                    "below_sample_floor": applicable < min_samples,
                    "rank": None,
                    "examples": bucket["examples"],
                }
            )
            totals = model_totals[model]
            totals["applicable"] += applicable
            totals["failed"] += failed
            totals["rules"] += 1
        ranked = sorted(
            [item for item in models if not item["below_sample_floor"]],
            key=lambda item: (item["failure_rate_upper_bound_95"] or 0.0, -item["applicable"], item["model"]),
        )
        for position, item in enumerate(ranked, start=1):
            item["rank"] = position
        models.sort(key=lambda item: (item["below_sample_floor"], item["rank"] or 999, -item["applicable"], item["model"]))
        template = _TEMPLATE_BY_TYPE[rule["type"]]
        applicable_total = sum(item["applicable"] for item in models)
        failed_total = sum(item["failed"] for item in models)
        rule_results.append(
            {
                "id": rule["id"],
                "name": rule["name"],
                "type": rule["type"],
                "template": template["label"],
                "sentence": _rule_sentence(rule),
                "applies_to": template["applies_to"],
                "profile": rule.get("profile"),
                "params": rule["params"],
                "applicable": applicable_total,
                "failed": failed_total,
                "pass_rate": round((applicable_total - failed_total) / applicable_total, 4) if applicable_total else None,
                "skipped_other_profiles": skipped_profile,
                "models": models,
            }
        )

    overall: List[Dict[str, Any]] = []
    for model, totals in model_totals.items():
        upper = _wilson_upper_bound(totals["failed"], totals["applicable"])
        overall.append(
            {
                "model": model,
                "applicable": totals["applicable"],
                "failed": totals["failed"],
                "passed": totals["applicable"] - totals["failed"],
                "pass_rate": round((totals["applicable"] - totals["failed"]) / totals["applicable"], 4) if totals["applicable"] else None,
                "failure_rate_upper_bound_95": round(upper, 4) if upper is not None else None,
                "rules": totals["rules"],
                "below_sample_floor": totals["applicable"] < min_samples,
                "rank": None,
            }
        )
    ranked = sorted(
        [item for item in overall if not item["below_sample_floor"]],
        key=lambda item: (item["failure_rate_upper_bound_95"] or 0.0, -item["applicable"], item["model"]),
    )
    for position, item in enumerate(ranked, start=1):
        item["rank"] = position
    overall.sort(key=lambda item: (item["below_sample_floor"], item["rank"] or 999, -item["applicable"], item["model"]))

    return {
        "period": _period_payload(days, period_start, period_end),
        "rules": rule_results,
        "overall": overall,
        "coverage": {**coverage, "min_samples": min_samples, "rules_evaluated": len(rule_results), "rules_received": len(rules)},
        "generated_at": time.time(),
        "elapsed_seconds": round(time.time() - started, 3),
        "definition": (
            "Each rule is checked by code against the recorded turns: a turn starts at a user message and runs to "
            "the next one. Verdicts are pass, fail, or not applicable; failures link to the turn. Scores group by the "
            "session's model (Hermes does not record the model per message) and rank by the 95% Wilson upper bound of "
            "the failure rate, only for models at or above the sample floor. Nothing here reads SOUL.md or judges tone."
        ),
    }


def _rule_sentence(rule: Mapping[str, Any]) -> str:
    template = _TEMPLATE_BY_TYPE.get(str(rule.get("type") or ""))
    if template is None:
        return ""
    params = rule.get("params") or {}
    rule_type = rule.get("type")
    if rule_type == "require_tool":
        text = f"Every reply must call {params.get('tool')}"
        if params.get("position") == "before_final":
            text += " before the final answer"
        if params.get("must_succeed"):
            text += ", and the call must succeed"
        return text
    if rule_type == "forbid_tool":
        return f"{params.get('tool')} must never be used"
    if rule_type == "tool_order":
        where = "earlier in the session" if params.get("scope") == "session" else "in the same turn"
        return f"Before using {params.get('tool')}, {params.get('before')} must have been tried {where}"
    if rule_type == "forbid_text":
        return "Replies must never contain " + ", ".join(f"“{item}”" for item in params.get("patterns") or [])
    if rule_type == "require_text_count":
        count = _integer(params.get("count"))
        return f"Replies must contain “{params.get('pattern')}” exactly {count} time{'s' if count != 1 else ''}"
    if rule_type == "language_match":
        return "The reply language must match the user's language"
    if rule_type == "forbid_tool_mention":
        return "Tool calls and results must never mention " + ", ".join(f"“{item}”" for item in params.get("patterns") or [])
    if rule_type == "path_boundary":
        tools = ", ".join(params.get("tools") or []) or "all tools"
        return f"{tools} must stay inside " + ", ".join(params.get("roots") or [])
    return template["sentence"]


def _serving_profile_name_safe() -> Optional[str]:
    try:
        from ._routes import _serving_profile_name  # type: ignore
    except Exception:
        try:
            from _routes import _serving_profile_name  # type: ignore
        except Exception:
            return None
    try:
        return _serving_profile_name()
    except Exception:
        return None


def _digest_rules_lines(payload: Optional[Mapping[str, Any]]) -> List[str]:
    if not payload or not payload.get("rules"):
        return []
    lines = ["## Instruction rules"]
    for rule in payload["rules"]:
        models = [item for item in rule.get("models") or []]
        if not models:
            lines.append(f"- {rule['name']}: no applicable turns in this period")
            continue
        parts = []
        for item in models:
            pct = f"{_number(item.get('pass_rate')) * 100:.0f}%"
            note = f"{item['passed']}/{item['applicable']}"
            if item.get("below_sample_floor"):
                note += ", below sample floor"
            parts.append(f"{item['model']} {pct} ({note})")
        lines.append(f"- {rule['name']}: " + " · ".join(parts))
    overall = [item for item in payload.get("overall") or [] if item.get("rank")]
    if overall:
        lines.append("- Overall rank: " + ", ".join(f"#{item['rank']} {item['model']} ({_number(item.get('pass_rate')) * 100:.0f}%)" for item in overall))
    return lines


__all__ = [name for name in globals() if not name.startswith("__")]
