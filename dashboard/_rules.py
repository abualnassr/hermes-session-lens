"""Instruction rules: grade recorded turns against user-declared, checkable rules.

The plugin never reads SOUL.md or guesses what an instruction means. The user
states a rule as WHEN conditions and THEN expectations chosen from a small
catalog of checks (each a sentence with blanks), and this module walks the
recorded conversation turns and answers, per turn, whether the model met it.
Presets (the original eight templates and a few more) are pre-filled rules
over the same catalog, so one engine grades everything.

Verdicts are deterministic and every failure points at the turn that
produced it. Scores are grouped by the session's model and ranked with the
same Wilson upper bound the AI Models tab uses, so a model with four turns
cannot outrank one with forty on luck.

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
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

try:
    from ._common import *
    from ._reliability import _wilson_upper_bound
except ImportError:  # pragma: no cover - direct Hermes file loading
    from _common import *
    from _reliability import _wilson_upper_bound

RULES_MAX_RULES = 40
RULES_MAX_CLAUSES = 12
RULES_MAX_PARAM_CHARS = 32_000
RULES_MAX_SESSIONS = 600
RULES_MAX_EXAMPLES = 6
RULES_DEFAULT_MIN_SAMPLES = 5
RULES_TEXT_CAP = 20_000
RULES_FORMAT = 2

_LANGUAGES = [["arabic", "Arabic"], ["turkish", "Turkish"], ["english", "English"]]

# ── Field definitions shared by conditions, expectations, and presets ─────

_F_TOOL = {"key": "tool", "label": "Tool name", "placeholder": "text_to_speech", "required": True, "hint": "Glob patterns work: browser_*"}
_F_PATTERNS = {"key": "patterns", "label": "Text", "kind": "list", "placeholder": "one per line", "required": True, "hint": "One per line, case-insensitive; any one matches."}
_F_REGEX = {"key": "regex", "label": "Treat as regular expressions", "kind": "bool", "default": False}


# ── Catalog ───────────────────────────────────────────────────────────────
# `sentence` uses {field} placeholders; {field?text} inserts text when the
# field is truthy; select fields render their option label.

CONDITIONS: List[Dict[str, Any]] = [
    {"kind": "has_reply", "label": "The assistant replied", "sentence": "the assistant replied", "fields": []},
    {"kind": "user_says", "label": "The user message contains…", "sentence": "the user message contains {patterns}", "fields": [dict(_F_PATTERNS, placeholder="important\nurgent"), _F_REGEX]},
    {"kind": "reply_says", "label": "The reply contains…", "sentence": "the reply contains {patterns}", "fields": [_F_PATTERNS, _F_REGEX]},
    {"kind": "tool_called", "label": "A tool was called", "sentence": "{tool} was called", "fields": [_F_TOOL]},
    {"kind": "tool_arg_matches", "label": "A tool was called with…", "sentence": "{tool} was called with arguments containing {patterns}", "fields": [_F_TOOL, dict(_F_PATTERNS, placeholder="rm -rf\n--force"), _F_REGEX]},
    {"kind": "tool_failed", "label": "A tool call failed", "sentence": "a {tool} call returned an error", "fields": [dict(_F_TOOL, placeholder="*", required=False, default="*")]},
    {"kind": "has_tool_calls", "label": "Any tool was used", "sentence": "any tool was used", "fields": []},
    {"kind": "user_language_is", "label": "The user wrote in…", "sentence": "the user wrote in {language}", "fields": [{"key": "language", "label": "Language", "kind": "select", "options": _LANGUAGES, "default": "arabic"}]},
]

EXPECTATIONS: List[Dict[str, Any]] = [
    {
        "kind": "call_tool", "label": "Call a tool", "sentence": "call {tool} {position}{must_succeed?, and the call must succeed}",
        "fields": [
            _F_TOOL,
            {"key": "position", "label": "When", "kind": "select", "options": [["any", "at some point"], ["before_final", "before the final answer"]], "default": "any"},
            {"key": "must_succeed", "label": "The call must also succeed", "kind": "bool", "default": False, "hint": "Off measures the model; on also fails turns where the tool itself errored."},
        ],
    },
    {"kind": "avoid_tool", "label": "Never use a tool", "sentence": "never use {tool}", "fields": [dict(_F_TOOL, placeholder="computer_use")]},
    {
        "kind": "try_first", "label": "Try one tool before another", "sentence": "try {before} before using {tool} ({scope})",
        "fields": [
            dict(_F_TOOL, label="Restricted tool", placeholder="browser_*"),
            {"key": "before", "label": "Must have been tried first", "placeholder": "web_search", "required": True},
            {"key": "scope", "label": "Look back", "kind": "select", "options": [["turn", "within the same turn"], ["session", "anywhere earlier in the session"]], "default": "turn"},
        ],
    },
    {
        "kind": "max_calls", "label": "Limit tool calls", "sentence": "call {tool} at most {max} times",
        "fields": [dict(_F_TOOL, placeholder="*", required=False, default="*", hint="* counts every tool"), {"key": "max", "label": "At most", "kind": "number", "default": 10}],
    },
    {"kind": "no_repeat_calls", "label": "Never repeat an identical call", "sentence": "never repeat an identical tool call", "fields": []},
    {"kind": "reply_contains", "label": "Reply contains…", "sentence": "reply with text containing {patterns}", "fields": [_F_PATTERNS, _F_REGEX]},
    {"kind": "reply_avoids", "label": "Reply never contains…", "sentence": "never write {patterns}", "fields": [dict(_F_PATTERNS, placeholder="D:\\\nsaved to"), _F_REGEX]},
    {
        "kind": "reply_count", "label": "Reply contains something exactly N times", "sentence": "write {pattern} exactly {count} time(s)",
        "fields": [{"key": "pattern", "label": "Text", "placeholder": "Abu Omar | أبو عمر", "required": True, "hint": "Case-insensitive. Separate accepted spellings with |."}, {"key": "count", "label": "Times", "kind": "number", "default": 1}],
    },
    {"kind": "reply_max_chars", "label": "Keep the reply short", "sentence": "keep the reply under {max} characters", "fields": [{"key": "max", "label": "Characters", "kind": "number", "default": 1200}]},
    {"kind": "reply_language_matches", "label": "Reply in the user's language", "sentence": "reply in the user's language", "fields": []},
    {"kind": "reply_language_is", "label": "Reply in a given language", "sentence": "reply in {language}", "fields": [{"key": "language", "label": "Language", "kind": "select", "options": _LANGUAGES, "default": "english"}]},
    {"kind": "tools_avoid_mention", "label": "Tool calls never mention…", "sentence": "never mention {patterns} in tool calls or results", "fields": [dict(_F_PATTERNS, placeholder="Maaden\nAINOTE")]},
    {
        "kind": "paths_within", "label": "Tools stay inside folders", "sentence": "keep {tools} inside {roots}",
        "fields": [
            {"key": "tools", "label": "Tools", "kind": "list", "placeholder": "terminal\nwrite_file\npatch", "default": "terminal\nwrite_file\npatch\nread_file", "hint": "One per line, globs allowed."},
            {"key": "roots", "label": "Allowed folders", "kind": "list", "placeholder": "D:\\Projects\\AssetNerve", "required": True, "hint": "One per line. Paths under any of these pass. File content is not scanned."},
        ],
    },
    {"kind": "args_avoid", "label": "Never call a tool with…", "sentence": "never call {tool} with arguments containing {patterns}", "fields": [dict(_F_TOOL, placeholder="terminal"), dict(_F_PATTERNS, placeholder="rm -rf\ngit push --force"), _F_REGEX]},
    {"kind": "ends_with_text", "label": "Finish with a written answer", "sentence": "finish the turn with a written answer", "fields": []},
    {"kind": "reply_within", "label": "Reply quickly", "sentence": "start replying within {seconds} seconds", "fields": [{"key": "seconds", "label": "Seconds", "kind": "number", "default": 60}]},
]

_CONDITION_BY_KIND = {item["kind"]: item for item in CONDITIONS}
_EXPECTATION_BY_KIND = {item["kind"]: item for item in EXPECTATIONS}

# Presets: the friendly sentences. Each compiles to WHEN/THEN over the catalog.
# `map` lists which clause fields the preset form exposes (form key -> field key).
PRESETS: List[Dict[str, Any]] = [
    {"type": "require_tool", "label": "Every reply must call a tool", "when": [], "then": [{"kind": "call_tool"}], "map": {"tool": "tool", "position": "position", "must_succeed": "must_succeed"}},
    {"type": "forbid_tool", "label": "A tool must never be used", "when": [{"kind": "has_tool_calls"}], "then": [{"kind": "avoid_tool"}], "map": {"tool": "tool"}},
    {"type": "tool_order", "label": "Try one tool before another", "when": [{"kind": "tool_called", "copy": "tool"}], "then": [{"kind": "try_first"}], "map": {"tool": "tool", "before": "before", "scope": "scope"}},
    {"type": "forbid_text", "label": "Replies must never contain", "when": [], "then": [{"kind": "reply_avoids"}], "map": {"patterns": "patterns", "regex": "regex"}},
    {"type": "require_text_count", "label": "Replies must contain something exactly N times", "when": [], "then": [{"kind": "reply_count"}], "map": {"pattern": "pattern", "count": "count"}},
    {"type": "language_match", "label": "Reply in the user's language", "when": [], "then": [{"kind": "reply_language_matches"}], "map": {}},
    {"type": "forbid_tool_mention", "label": "Tool calls must never mention", "when": [{"kind": "has_tool_calls"}], "then": [{"kind": "tools_avoid_mention"}], "map": {"patterns": "patterns"}},
    {"type": "path_boundary", "label": "Tools must stay inside folders", "when": [], "then": [{"kind": "paths_within"}], "map": {"tools": "tools", "roots": "roots"}},
    {"type": "conditional_tool", "label": "When the user says X, call a tool", "when": [{"kind": "user_says"}], "then": [{"kind": "call_tool"}], "map": {"patterns": "patterns", "tool": "tool"}},
    {"type": "no_loops", "label": "No tool loops", "when": [{"kind": "has_tool_calls"}], "then": [{"kind": "max_calls"}, {"kind": "no_repeat_calls"}], "map": {"max": "max"}},
    {"type": "cite_when_searching", "label": "Cite when searching", "when": [{"kind": "tool_called", "tool": "web_*"}], "then": [{"kind": "reply_contains", "patterns": ["http"]}], "map": {}},
    {"type": "no_destructive_args", "label": "Never run a destructive command", "when": [{"kind": "has_tool_calls"}], "then": [{"kind": "args_avoid", "tool": "terminal", "patterns": ["rm -rf", "git push --force", "git reset --hard"]}], "map": {"patterns": "patterns"}},
]
_PRESET_BY_TYPE = {preset["type"]: preset for preset in PRESETS}


def _preset_fields(preset: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The editable fields of a preset: the clause fields it exposes through `map`."""
    fields: List[Dict[str, Any]] = []
    exposed = preset.get("map") or {}
    for clause in list(preset.get("when") or []) + list(preset.get("then") or []):
        catalog = _CONDITION_BY_KIND.get(clause["kind"]) or _EXPECTATION_BY_KIND.get(clause["kind"]) or {}
        for field in catalog.get("fields", []):
            if field["key"] in exposed.values() and all(item["key"] != field["key"] for item in fields):
                fields.append(field)
    return fields


def _preset_compile(preset: Mapping[str, Any], params: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    exposed = preset.get("map") or {}

    def clause(spec: Mapping[str, Any]) -> Dict[str, Any]:
        catalog = _CONDITION_BY_KIND.get(spec["kind"]) or _EXPECTATION_BY_KIND.get(spec["kind"]) or {"fields": []}
        values: Dict[str, Any] = {}
        for field in catalog["fields"]:
            key = field["key"]
            if key in spec:
                values[key] = spec[key]
            elif key in exposed.values():
                source = next((src for src, dst in exposed.items() if dst == key), key)
                if source in params:
                    values[key] = params[source]
            if key not in values and "copy" in spec and key == "tool":
                values[key] = params.get(spec["copy"])
        return {"kind": spec["kind"], "params": values, "negate": False}

    return [clause(spec) for spec in preset.get("when") or []], [clause(spec) for spec in preset.get("then") or []]


def _rules_catalog() -> Dict[str, Any]:
    presets = []
    for preset in PRESETS:
        presets.append({"type": preset["type"], "label": preset["label"], "fields": _preset_fields(preset), "when": preset.get("when") or [], "then": preset.get("then") or [], "map": preset.get("map") or {}})
    return {
        "format": RULES_FORMAT,
        "presets": presets,
        "conditions": CONDITIONS,
        "expectations": EXPECTATIONS,
        "max_rules": RULES_MAX_RULES,
        "max_clauses": RULES_MAX_CLAUSES,
        "default_min_samples": RULES_DEFAULT_MIN_SAMPLES,
    }


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


def _coerce_fields(fields: Iterable[Mapping[str, Any]], raw: Mapping[str, Any]) -> Tuple[Dict[str, Any], bool]:
    params: Dict[str, Any] = {}
    valid = True
    for field in fields:
        key = field["key"]
        kind = field.get("kind", "text")
        value = raw.get(key, field.get("default"))
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
            value = max(0, min(1_000_000, value))
        elif kind == "select":
            allowed = [option[0] for option in field.get("options", [])]
            value = str(value or field.get("default") or "").strip()
            if value not in allowed:
                value = str(field.get("default") or (allowed[0] if allowed else ""))
        else:
            value = _clean_text(value, 400) or ""
            if not value and field.get("default"):
                value = str(field["default"])
            if field.get("required") and not value:
                valid = False
        params[key] = value
    return params, valid


def _parse_clause(item: Any, catalog: Mapping[str, Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, Mapping):
        return None
    entry = catalog.get(str(item.get("kind") or ""))
    if entry is None:
        return None
    raw = item.get("params") if isinstance(item.get("params"), Mapping) else {}
    params, valid = _coerce_fields(entry["fields"], raw)
    if not valid:
        return None
    return {"kind": entry["kind"], "params": params, "negate": bool(item.get("negate"))}


def _parse_rules_param(raw: str) -> List[Dict[str, Any]]:
    """Validate the desktop's rule list. Presets compile to WHEN/THEN; a bad clause drops its rule."""
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
        rule_id = re.sub(r"[^A-Za-z0-9_.:-]", "", str(item.get("id") or f"rule-{index + 1}"))[:60] or f"rule-{index + 1}"
        if rule_id in seen_ids:
            rule_id = f"{rule_id}-{index + 1}"
        seen_ids.add(rule_id)
        name = _clean_text(item.get("name"), 120)
        rule_type = str(item.get("type") or "").strip()
        match = "all"
        if isinstance(item.get("then"), list):
            when_block = item.get("when")
            if isinstance(when_block, Mapping):
                when_raw = list(when_block.get("conditions") or [])
                match = "any" if str(when_block.get("match") or "all") == "any" else "all"
            else:
                when_raw = list(when_block) if isinstance(when_block, list) else []
            then_raw = list(item.get("then"))
            label = name or "Custom rule"
        elif rule_type in _PRESET_BY_TYPE:
            preset = _PRESET_BY_TYPE[rule_type]
            raw_params = item.get("params") if isinstance(item.get("params"), Mapping) else {}
            when_raw, then_raw = _preset_compile(preset, raw_params)
            label = name or preset["label"]
        else:
            continue
        when_raw = when_raw[:RULES_MAX_CLAUSES]
        then_raw = then_raw[:RULES_MAX_CLAUSES]
        when = [clause for clause in (_parse_clause(entry, _CONDITION_BY_KIND) for entry in when_raw) if clause]
        then = [clause for clause in (_parse_clause(entry, _EXPECTATION_BY_KIND) for entry in then_raw) if clause]
        if len(when) != len(when_raw) or len(then) != len(then_raw) or not then:
            continue
        rules.append(
            {
                "id": rule_id,
                "name": label,
                "type": rule_type or "custom",
                "when": {"match": match, "conditions": when},
                "then": then,
                "profile": _clean_text(item.get("profile"), 80) or None,
                "enabled": item.get("enabled", True) is not False,
            }
        )
        if len(rules) >= RULES_MAX_RULES:
            break
    return rules


# ── Sentences ─────────────────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)(\?([^}]*))?\}")


def _render_sentence(entry: Mapping[str, Any], params: Mapping[str, Any]) -> str:
    fields = {field["key"]: field for field in entry.get("fields", [])}

    def fill(match: re.Match) -> str:
        key, conditional, text = match.group(1), match.group(2), match.group(3)
        value = params.get(key)
        if conditional is not None:
            return text if value else ""
        field = fields.get(key, {})
        kind = field.get("kind", "text")
        if kind == "list":
            items = value if isinstance(value, list) else _rules_list_param(value)
            return ", ".join(f"“{item}”" for item in items) if items else "…"
        if kind == "select":
            for option, label in field.get("options", []):
                if option == value:
                    return label
            return str(value or "…")
        if kind == "bool":
            return "yes" if value else "no"
        text_value = str(value if value is not None else "").strip()
        if key == "tool" and text_value == "*":
            return "any tool"
        return text_value if text_value else "…"

    return re.sub(r"\s{2,}", " ", _PLACEHOLDER_RE.sub(fill, entry.get("sentence", ""))).strip()


def _clause_sentence(clause: Mapping[str, Any], catalog: Mapping[str, Mapping[str, Any]]) -> str:
    entry = catalog.get(str(clause.get("kind") or ""))
    if entry is None:
        return str(clause.get("kind") or "")
    text = _render_sentence(entry, clause.get("params") or {})
    return f"not ({text})" if clause.get("negate") else text


def _rule_sentence(rule: Mapping[str, Any]) -> str:
    conditions = [clause for clause in (rule.get("when") or {}).get("conditions", []) if clause.get("kind") != "has_reply"]
    then_text = " and ".join(_clause_sentence(clause, _EXPECTATION_BY_KIND) for clause in rule.get("then") or [])
    if not conditions:
        return f"Every reply must {then_text}"
    joiner = " or " if (rule.get("when") or {}).get("match") == "any" else " and "
    when_text = joiner.join(_clause_sentence(clause, _CONDITION_BY_KIND) for clause in conditions)
    return f"When {when_text}, {then_text}"


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
        calls.append({"name": name, "arguments": arguments[:RULES_TEXT_CAP], "call_id": str(item.get("call_id") or item.get("id") or "")})
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
                "first_reply_at": None,
                "user_text": _message_text(row.get("content"))[:RULES_TEXT_CAP],
                "events": [],
            }
            continue
        if current is None:
            continue
        if role == "assistant":
            if current["first_reply_at"] is None and row.get("timestamp") is not None:
                current["first_reply_at"] = _number(row.get("timestamp"))
            text = _visible_text(_message_text(row.get("content")))
            if text:
                current["events"].append(("text", text[:RULES_TEXT_CAP]))
            for call in _tool_calls_from_row(row.get("tool_calls")):
                current["events"].append(("call", call["name"], call["arguments"], call["call_id"]))
        elif role == "tool":
            content = str(row.get("content") or "")[:RULES_TEXT_CAP]
            failed = _is_failure(role="tool", content=content, finish_reason=row.get("finish_reason"), effect_disposition=row.get("effect_disposition"))
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


def _tool_matches(pattern: Any, name: str) -> bool:
    return fnmatch.fnmatchcase(str(name or "").lower(), str(pattern or "*").lower())


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


# ── Pattern helpers ───────────────────────────────────────────────────────


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


# ── Conditions: (params, turn, history) -> bool ───────────────────────────

Turn = Mapping[str, Any]
History = List[Dict[str, Any]]


def _cond_has_reply(params: Mapping[str, Any], turn: Turn, history: History) -> bool:
    return _turn_has_output(turn)


def _cond_user_says(params: Mapping[str, Any], turn: Turn, history: History) -> bool:
    return _first_pattern_hit(_compile_patterns(params), turn.get("user_text") or "") is not None


def _cond_reply_says(params: Mapping[str, Any], turn: Turn, history: History) -> bool:
    return _first_pattern_hit(_compile_patterns(params), _turn_text(turn)) is not None


def _cond_tool_called(params: Mapping[str, Any], turn: Turn, history: History) -> bool:
    return any(_tool_matches(params.get("tool"), call[1]) for call in _turn_calls(turn))


def _cond_tool_arg_matches(params: Mapping[str, Any], turn: Turn, history: History) -> bool:
    compiled = _compile_patterns(params)
    return any(_tool_matches(params.get("tool"), call[1]) and _first_pattern_hit(compiled, call[2]) for call in _turn_calls(turn))


def _cond_tool_failed(params: Mapping[str, Any], turn: Turn, history: History) -> bool:
    return any(result[3] and _tool_matches(params.get("tool") or "*", result[1]) for result in _turn_results(turn))


def _cond_has_tool_calls(params: Mapping[str, Any], turn: Turn, history: History) -> bool:
    return bool(_turn_calls(turn))


def _cond_user_language_is(params: Mapping[str, Any], turn: Turn, history: History) -> bool:
    return _detect_language(turn.get("user_text") or "") == params.get("language")


_CONDITION_FUNCTIONS: Dict[str, Callable[..., bool]] = {
    "has_reply": _cond_has_reply,
    "user_says": _cond_user_says,
    "reply_says": _cond_reply_says,
    "tool_called": _cond_tool_called,
    "tool_arg_matches": _cond_tool_arg_matches,
    "tool_failed": _cond_tool_failed,
    "has_tool_calls": _cond_has_tool_calls,
    "user_language_is": _cond_user_language_is,
}


# ── Expectations: (params, turn, history) -> None (n/a) | (ok, reason) ────

Verdict = Optional[Tuple[bool, Optional[str]]]


def _exp_call_tool(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
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
        failure_note = None
        for call in matching:
            result = results.get(call[3])
            if result is None:
                candidates = [item for item in by_name if item[0] > call[0]]
                result = candidates[0] if candidates else None
            if result is None or not result[3]:
                return True, None
            failure_note = _clean_text(result[2], 140)
        return False, f"{pattern} was called but failed: {failure_note or 'error result'}"
    return True, None


def _exp_avoid_tool(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
    hits = [call[1] for call in _turn_calls(turn) if _tool_matches(params.get("tool"), call[1])]
    if hits:
        return False, f"used {hits[0]}" + (f" ({len(hits)} calls)" if len(hits) > 1 else "")
    return True, None


def _exp_try_first(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
    restricted = str(params.get("tool") or "")
    required = str(params.get("before") or "")
    calls = _turn_calls(turn)
    restricted_calls = [call for call in calls if _tool_matches(restricted, call[1])]
    if not restricted_calls:
        return None
    earlier_in_session = params.get("scope") == "session" and any(
        _tool_matches(required, call[1]) for previous in history for call in _turn_calls(previous)
    )
    for call in restricted_calls:
        tried_first = earlier_in_session or any(_tool_matches(required, other[1]) and other[0] < call[0] for other in calls)
        if not tried_first:
            return False, f"{call[1]} used without trying {required} first"
    return True, None


def _exp_max_calls(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
    pattern = str(params.get("tool") or "*")
    count = sum(1 for call in _turn_calls(turn) if _tool_matches(pattern, call[1]))
    limit = _integer(params.get("max"))
    if count > limit:
        return False, f"{count} {pattern if pattern != '*' else 'tool'} calls in one turn, limit {limit}"
    return True, None


def _exp_no_repeat_calls(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
    seen: Dict[Tuple[str, str], int] = defaultdict(int)
    for call in _turn_calls(turn):
        seen[(call[1], call[2].strip())] += 1
    repeated = [(key, count) for key, count in seen.items() if count > 1]
    if repeated:
        (name, _arguments), count = max(repeated, key=lambda item: item[1])
        return False, f"{name} called {count} times with identical arguments"
    return True, None


def _exp_reply_contains(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
    text = _turn_text(turn)
    if _first_pattern_hit(_compile_patterns(params), text) is None:
        wanted = ", ".join(f"“{item}”" for item in params.get("patterns") or [])
        return False, f"reply lacks {wanted}" if text else f"no written reply to carry {wanted}"
    return True, None


def _exp_reply_avoids(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
    text = _turn_text(turn)
    if not text:
        return None
    hit = _first_pattern_hit(_compile_patterns(params), text)
    if hit:
        return False, f"reply contains “{hit}”"
    return True, None


def _exp_reply_count(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
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


def _exp_reply_max_chars(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
    text = _turn_text(turn)
    if not text:
        return None
    limit = _integer(params.get("max"))
    if len(text) > limit:
        return False, f"reply is {len(text):,} characters, limit {limit:,}"
    return True, None


def _exp_reply_language_matches(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
    user_language = _detect_language(turn.get("user_text") or "")
    if user_language not in {"arabic", "turkish", "english"}:
        return None
    reply_language = _detect_language(_turn_text(turn))
    if reply_language is None or reply_language == "latin":
        return None
    if reply_language == user_language:
        return True, None
    return False, f"user wrote {user_language}, reply was {reply_language}"


def _exp_reply_language_is(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
    reply_language = _detect_language(_turn_text(turn))
    if reply_language is None or reply_language == "latin":
        return None
    wanted = str(params.get("language") or "")
    if reply_language == wanted:
        return True, None
    return False, f"reply was {reply_language}, expected {wanted}"


def _exp_tools_avoid_mention(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
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


def _exp_paths_within(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
    tools = params.get("tools") or []
    roots = [root for root in (_normalise_path(item) for item in params.get("roots") or []) if root]
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


def _exp_args_avoid(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
    pattern = str(params.get("tool") or "*")
    calls = [call for call in _turn_calls(turn) if _tool_matches(pattern, call[1])]
    if not calls:
        return None
    compiled = _compile_patterns(params)
    for call in calls:
        hit = _first_pattern_hit(compiled, call[2])
        if hit:
            return False, f"{call[1]} was called with “{hit}”"
    return True, None


def _exp_ends_with_text(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
    events = turn["events"]
    if not events:
        return False, "no reply at all"
    if events[-1][0] == "text":
        return True, None
    last = events[-1]
    return False, f"turn ended on a {last[1]} {'call' if last[0] == 'call' else 'result'} with no written answer"


def _exp_reply_within(params: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
    started = _number(turn.get("timestamp"))
    first = turn.get("first_reply_at")
    if not started or first is None:
        return None
    elapsed = _number(first) - started
    limit = _integer(params.get("seconds"))
    if elapsed > limit:
        return False, f"first reply after {elapsed:.0f}s, limit {limit}s"
    return True, None


_EXPECTATION_FUNCTIONS: Dict[str, Callable[..., Verdict]] = {
    "call_tool": _exp_call_tool,
    "avoid_tool": _exp_avoid_tool,
    "try_first": _exp_try_first,
    "max_calls": _exp_max_calls,
    "no_repeat_calls": _exp_no_repeat_calls,
    "reply_contains": _exp_reply_contains,
    "reply_avoids": _exp_reply_avoids,
    "reply_count": _exp_reply_count,
    "reply_max_chars": _exp_reply_max_chars,
    "reply_language_matches": _exp_reply_language_matches,
    "reply_language_is": _exp_reply_language_is,
    "tools_avoid_mention": _exp_tools_avoid_mention,
    "paths_within": _exp_paths_within,
    "args_avoid": _exp_args_avoid,
    "ends_with_text": _exp_ends_with_text,
    "reply_within": _exp_reply_within,
}


def _evaluate_rule(rule: Mapping[str, Any], turn: Turn, history: History) -> Verdict:
    """WHEN decides applicability; THEN decides the verdict. Negation flips either."""
    when = rule.get("when") or {}
    conditions = when.get("conditions") or []
    if not conditions:
        if not _turn_has_output(turn):
            return None
    else:
        outcomes = []
        for clause in conditions:
            function = _CONDITION_FUNCTIONS.get(clause["kind"])
            if function is None:
                return None
            held = bool(function(clause.get("params") or {}, turn, history))
            outcomes.append(not held if clause.get("negate") else held)
        applicable = any(outcomes) if when.get("match") == "any" else all(outcomes)
        if not applicable:
            return None
    checked = False
    for clause in rule.get("then") or []:
        function = _EXPECTATION_FUNCTIONS.get(clause["kind"])
        if function is None:
            continue
        verdict = function(clause.get("params") or {}, turn, history)
        if verdict is None:
            continue
        checked = True
        ok, reason = verdict
        if clause.get("negate"):
            ok = not ok
            reason = None if ok else "did the opposite of the negated expectation: " + _render_sentence(_EXPECTATION_BY_KIND[clause["kind"]], clause.get("params") or {})
        if not ok:
            return False, reason or "expectation not met"
    return (True, None) if checked else None


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
    return turns, {"sessions": len(sessions), "sessions_truncated": truncated, "turns": len(turns)}


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
        per_model: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"applicable": 0, "passed": 0, "failed": 0, "examples": []})
        skipped_profile = 0
        for session_turns in by_session.values():
            history: List[Dict[str, Any]] = []
            for turn in session_turns:
                if rule.get("profile") and str(turn.get("profile") or "") != rule["profile"]:
                    skipped_profile += 1
                    history.append(turn)
                    continue
                verdict = _evaluate_rule(rule, turn, history)
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
        applicable_total = sum(item["applicable"] for item in models)
        failed_total = sum(item["failed"] for item in models)
        rule_results.append(
            {
                "id": rule["id"],
                "name": rule["name"],
                "type": rule.get("type") or "custom",
                "sentence": _rule_sentence(rule),
                "when": rule.get("when"),
                "then": rule.get("then"),
                "profile": rule.get("profile"),
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
            "Each rule is WHEN conditions plus THEN expectations, checked by code against the recorded turns: a turn "
            "starts at a user message and runs to the next one. WHEN decides whether a turn counts; THEN decides "
            "pass or fail, and failures link to the turn. Scores group by the session's model (Hermes does not "
            "record the model per message) and rank by the 95% Wilson upper bound of the failure rate, only for "
            "models at or above the sample floor. Nothing here reads SOUL.md or judges tone."
        ),
    }


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
        models = list(rule.get("models") or [])
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



# ── Tool-name directory ───────────────────────────────────────────────────
# The builder's tool fields offer every name Hermes can call (its live tool
# registry, populated only inside Hermes) merged with every name the records
# have seen (with call counts), plus ready-made globs for the families.

TOOL_NAMES_CACHE_TTL_SECONDS = 300.0
_tool_names_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _registry_tool_names() -> Tuple[List[str], Dict[str, str]]:
    """Names and toolsets from Hermes' ToolRegistry; empty outside Hermes."""
    try:
        from tools.registry import registry  # type: ignore

        names = [str(name) for name in (registry.get_all_tool_names() or [])]
        toolsets = {str(key): str(value) for key, value in (registry.get_tool_to_toolset_map() or {}).items()}
        return names, toolsets
    except Exception:
        return [], {}


def _tool_group(name: str, toolsets: Mapping[str, str]) -> str:
    if name in toolsets:
        return toolsets[name]
    match = re.match(r"mcp__([^_]+(?:_[^_]+)*?)__", name)
    if match:
        return f"mcp:{match.group(1)}"
    head = name.split("_", 1)[0]
    return head if "_" in name else "other"


def _tool_globs(names: Iterable[str]) -> List[Dict[str, Any]]:
    """Family globs worth offering: mcp__<server>__* and <prefix>_* with two or more members."""
    families: Dict[str, int] = defaultdict(int)
    for name in names:
        match = re.match(r"(mcp__[^_]+(?:_[^_]+)*?__)", name)
        if match:
            families[match.group(1) + "*"] += 1
        elif "_" in name:
            families[name.split("_", 1)[0] + "_*"] += 1
    return [{"name": glob, "members": count} for glob, count in sorted(families.items(), key=lambda item: (-item[1], item[0])) if count >= 2]


def _tool_names_sync() -> Dict[str, Any]:
    scope = _get_profile_scope()
    cache_key = ",".join(scope) if scope else ""
    now = time.time()
    cached = _tool_names_cache.get(cache_key)
    if cached and now - cached[0] < TOOL_NAMES_CACHE_TTL_SECONDS:
        return dict(cached[1], cached=True)
    registry_names, toolsets = _registry_tool_names()
    recorded: Dict[str, Dict[str, Any]] = {}
    with _database() as db:
        connection = _db_connection(db)
        for row in connection.execute(
            """
            SELECT tool_name, COUNT(*) AS calls, MAX(timestamp) AS last_used_at
            FROM messages
            WHERE role='tool' AND tool_name IS NOT NULL AND tool_name != '' AND coalesce(active,1)=1
            GROUP BY tool_name
            """
        ).fetchall():
            material = _row_dict(row)
            name = str(material.get("tool_name") or "").strip()
            if name:
                recorded[name] = {"calls": _integer(material.get("calls")), "last_used_at": material.get("last_used_at")}
    entries: List[Dict[str, Any]] = []
    for name in sorted(set(registry_names) | set(recorded)):
        record = recorded.get(name)
        entries.append(
            {
                "name": name,
                "group": _tool_group(name, toolsets),
                "recorded_calls": _integer(record["calls"]) if record else 0,
                "last_used_at": record.get("last_used_at") if record else None,
                "source": "both" if record and name in registry_names else ("registry" if name in registry_names else "recorded"),
            }
        )
    entries.sort(key=lambda item: (-item["recorded_calls"], item["group"], item["name"]))
    payload = {
        "tools": entries,
        "globs": _tool_globs(item["name"] for item in entries),
        "registry_available": bool(registry_names),
        "generated_at": now,
        "cached": False,
        "definition": (
            "Every tool Hermes can call right now (its live tool registry, including connected MCP servers) merged with "
            "every tool name the records have seen, ranked by recorded calls. Outside Hermes only recorded names are known."
        ),
    }
    _tool_names_cache[cache_key] = (now, dict(payload))
    return payload


# Kept for callers that address the presets by their old name.
RULE_TEMPLATES = PRESETS

__all__ = [name for name in globals() if not name.startswith("__")]
