"""Task classification, change evidence, and retry detection."""

from __future__ import annotations

try:
    from ._common import *
except ImportError:  # pragma: no cover
    from _common import *

def _tool_text(call: Mapping[str, Any]) -> str:
    arguments = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
    action_values = [
        arguments.get(key)
        for key in ("action", "command", "code", "mode", "operation", "subcommand", "type")
    ]
    return " ".join([str(call.get("name") or ""), *(str(value or "") for value in action_values)]).lower()


def _paths_from_tool_call(call: Mapping[str, Any], extra_text: Any = None) -> List[str]:
    name = str(call.get("name") or "")
    arguments = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
    paths = [item["path"] for item in _files_from_call(name, arguments)]
    searchable = [
        arguments.get(key)
        for key in ("command", "code", "diff", "patch")
        if isinstance(arguments.get(key), str)
    ]
    if isinstance(extra_text, str):
        searchable.append(extra_text)
    for value in searchable:
        for match in _PATCH_FILE_RE.finditer(value):
            paths.append(match.group("path"))
        for match in _FILE_REFERENCE_RE.finditer(value):
            paths.append(match.group("path"))
    return list(dict.fromkeys(path for path in paths if path))


def _path_artifact_kind(value: Any) -> Optional[str]:
    path = str(value or "").strip(" \t\r\n\"'`()[]{}.,;").replace("\\", "/").lower()
    if not path:
        return None
    parts = [part for part in path.split("/") if part]
    basename = parts[-1] if parts else path
    if basename == "skill.md" and any(part in {"plugin", "plugins", "skill", "skills"} for part in parts[:-1]):
        return "Coding"
    suffix = Path(basename).suffix.lower()
    if suffix in _CODE_FILE_EXTENSIONS:
        return "Coding"
    if suffix in _WRITING_FILE_EXTENSIONS:
        return "Writing"
    if any(part in {"notes", "vault", "wiki"} for part in parts[:-1]):
        return "Writing"
    return None


def _is_file_mutation(call: Mapping[str, Any]) -> bool:
    name = str(call.get("name") or "").lower()
    return any(
        marker in name
        for marker in ("apply_patch", "edit_file", "patch_file", "replace_in_file", "write_file")
    ) or name.rsplit(".", 1)[-1] == "patch"


def _is_git_commit_call(call: Mapping[str, Any]) -> bool:
    arguments = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
    command = str(arguments.get("command") or arguments.get("code") or "")
    return bool(_GIT_COMMIT_RE.search(command))


def _is_orchestration_call(call: Mapping[str, Any]) -> bool:
    name = str(call.get("name") or "").lower()
    if any(marker in name for marker in ("delegate_task", "spawn_agent", "wait_agent", "handoff")):
        return True
    if "kanban" in name and any(
        marker in name for marker in ("create", "assign", "dispatch", "complete", "block")
    ):
        return True
    arguments = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
    command = str(arguments.get("command") or arguments.get("code") or "")
    return bool(command and _WORKER_COMMAND_RE.search(command))


def _is_mutating_git_command(segment: str) -> bool:
    match = re.search(r"(?i)\b(?P<tool>git|gh)(?:\.exe)?\b\s+(?P<args>.+)", segment)
    if not match:
        return False
    tool = match.group("tool").lower()
    tokens = re.findall(r'''"[^"]*"|'[^']*'|[^\s]+''', match.group("args"))
    tokens = [token.strip("\"'").lower() for token in tokens]
    while tokens and tokens[0].startswith("-"):
        option = tokens.pop(0)
        if option in {"-c", "--git-dir", "--work-tree", "--namespace"} and tokens:
            tokens.pop(0)
    if not tokens:
        return False
    if tool == "gh" and tokens[0] in {"pr", "repo", "release"}:
        tokens.pop(0)
    if not tokens:
        return False
    subcommand = tokens[0]
    if subcommand in {
        "apply",
        "cherry-pick",
        "commit",
        "merge",
        "push",
        "rebase",
        "revert",
    }:
        return True
    if subcommand == "stash":
        return len(tokens) > 1 and tokens[1] in {"apply", "pop"}
    if subcommand == "tag":
        arguments = tokens[1:]
        if not arguments or any(item in {"-l", "--list", "-n"} for item in arguments):
            return False
        return any(not item.startswith("-") for item in arguments) or any(
            item in {"-a", "-d", "-f", "-s"} for item in arguments
        )
    return False


def _is_code_mutating_command(command: Any) -> bool:
    text = str(command or "")
    for segment in re.split(r"(?:&&|\|\||[;&|])", text):
        if _is_mutating_git_command(segment) or _CODE_RUNNER_RE.search(segment):
            return True
    return False


def _is_coding_call(call: Mapping[str, Any]) -> bool:
    name = str(call.get("name") or "").lower()
    extra_text = call.get("result_content") if _is_git_commit_call(call) else None
    paths = _paths_from_tool_call(call, extra_text)
    path_kinds = {_path_artifact_kind(path) for path in paths}
    if _is_file_mutation(call) and "Coding" in path_kinds:
        return True
    if "skill_manage" in name:
        action = _tool_text(call)
        if any(marker in action for marker in ("create", "edit", "patch", "replace", "update")):
            return True
    if ("github" in name or re.search(r"(?:^|[_:.])gh(?:$|[_:.])", name)) and any(
        marker in _tool_text(call)
        for marker in ("commit", "create", "delete", "edit", "merge", "mutate", "patch", "push", "update")
    ):
        return True
    if "execute_code" in name:
        return True
    if any(marker in name for marker in ("exec_command", "terminal", "shell")):
        arguments = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
        command = str(arguments.get("command") or arguments.get("code") or "")
        if _is_git_commit_call(call) and path_kinds and path_kinds <= {"Writing", None}:
            return False
        return _is_code_mutating_command(command)
    return False


def _is_read_action(call: Mapping[str, Any]) -> bool:
    return bool(
        re.search(
            r"(?:^|[_:.\s-])(?:analy(?:se|sis|ze)?|extract|inspect|preview|query|read|search|view)(?:$|[_:.\s-])",
            _tool_text(call),
        )
    )


def _is_writing_call(call: Mapping[str, Any]) -> bool:
    name = str(call.get("name") or "").lower()
    extra_text = call.get("result_content") if _is_git_commit_call(call) else None
    paths = _paths_from_tool_call(call, extra_text)
    path_kinds = {_path_artifact_kind(path) for path in paths}
    if _is_file_mutation(call) and "Writing" in path_kinds:
        return True
    if _is_git_commit_call(call) and "Writing" in path_kinds and "Coding" not in path_kinds:
        return True
    if any(marker in name for marker in ("image_generate", "imagegen")):
        return True
    if any(
        marker in name
        for marker in ("document", "docx", "nano-pdf", "nano_pdf", "pdf", "powerpoint", "presentation", "slides", "spreadsheet", "xlsx")
    ):
        return not _is_read_action(call)
    if any(marker in name for marker in ("email", "gmail")):
        return any(marker in _tool_text(call) for marker in ("compose", "create", "draft", "forward", "reply", "send", "write"))
    return False


def _is_analysis_call(call: Mapping[str, Any]) -> bool:
    name = str(call.get("name") or "").lower()
    return any(
        marker in name
        for marker in (
            "arxiv",
            "browser_exec",
            "capture",
            "computer_use",
            "drive_preview",
            "firecrawl",
            "hermes_web_search",
            "maps",
            "ocr",
            "pdf",
            "read_file",
            "read_preview",
            "scrape",
            "session_search",
            "vision_analyze",
            "web_extract",
            "web_search",
            "x_search",
        )
    )


def _enriched_session_calls(
    rows: Iterable[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    material = [dict(row) for row in rows]
    results_by_call = {
        str(row.get("tool_call_id")): row
        for row in material
        if str(row.get("role") or "").lower() == "tool" and row.get("tool_call_id")
    }
    calls: List[Dict[str, Any]] = []
    for row in material:
        if str(row.get("role") or "").lower() != "assistant":
            continue
        for call in _iter_tool_calls(row.get("tool_calls")):
            enriched = dict(call)
            result = results_by_call.get(str(call.get("call_id") or ""))
            enriched["result"] = result
            enriched["result_content"] = str((result or {}).get("content") or "")
            calls.append(enriched)
    return material, calls


def _session_task_type(
    rows: Iterable[Mapping[str, Any]],
    enriched_calls: Optional[Iterable[Mapping[str, Any]]] = None,
) -> str:
    calls = list(enriched_calls) if enriched_calls is not None else _enriched_session_calls(rows)[1]
    checks = (
        ("Orchestration", _is_orchestration_call),
        ("Coding", _is_coding_call),
        ("Writing", _is_writing_call),
        ("Analysis", _is_analysis_call),
    )
    for task_type, predicate in checks:
        if any(predicate(call) for call in calls):
            return task_type
    return "General"


def _task_role(value: Any) -> str:
    return str(value or "").strip().lower() or "main"


def _normalised_retry_prompt(value: Any) -> str:
    text = _clean_text(value, 12000).strip().lower()
    if not text or text.startswith("[important: you are running as a scheduled cron job."):
        return ""
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))[:500]


def _has_near_identical_prompt_retry(rows: Iterable[Mapping[str, Any]]) -> bool:
    prompts = sorted(
        (
            (_number(row.get("timestamp")), _normalised_retry_prompt(row.get("content")))
            for row in rows
            if str(row.get("role") or "").lower() == "user"
        ),
        key=lambda item: item[0],
    )
    prompts = [(timestamp, prompt) for timestamp, prompt in prompts if len(prompt) >= 12]
    for index, (timestamp, prompt) in enumerate(prompts):
        for prior_timestamp, prior_prompt in reversed(prompts[:index]):
            age = timestamp - prior_timestamp
            if age > 300:
                break
            if age <= 0:
                continue
            shorter = min(len(prompt), len(prior_prompt))
            longer = max(len(prompt), len(prior_prompt))
            if not shorter or longer / shorter > 1.20:
                continue
            if prompt == prior_prompt or SequenceMatcher(None, prior_prompt, prompt).ratio() >= 0.90:
                return True
    return False


def _change_evidence_by_type(
    rows: Iterable[Mapping[str, Any]],
    enriched_calls: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Dict[str, Optional[bool]]:
    calls = list(enriched_calls) if enriched_calls is not None else _enriched_session_calls(rows)[1]
    detected_results = {"Coding": 0, "Writing": 0}
    successful_change = {"Coding": False, "Writing": False}
    for call in calls:
        result = call.get("result")
        if not isinstance(result, Mapping):
            continue
        result_content = str(result.get("content") or "")
        paths = _paths_from_tool_call(call, result_content if _is_git_commit_call(call) else None)
        kinds = {_path_artifact_kind(path) for path in paths}
        kinds.discard(None)
        if "skill_manage" in str(call.get("name") or "").lower() and _is_coding_call(call):
            kinds.add("Coding")
        writing_artifact_change = _is_writing_call(call)
        if writing_artifact_change and not paths:
            kinds.add("Writing")
        if not (
            _is_file_mutation(call)
            or _is_git_commit_call(call)
            or writing_artifact_change
            or "skill_manage" in str(call.get("name") or "").lower()
        ):
            kinds.clear()
        if not kinds:
            continue
        failed = _is_failure(
            role="tool",
            content=result.get("content"),
            finish_reason=result.get("finish_reason"),
            effect_disposition=result.get("effect_disposition"),
        )
        for kind in kinds & {"Coding", "Writing"}:
            detected_results[kind] += 1
            if not failed and not _NO_FILE_CHANGE_RE.search(result_content):
                successful_change[kind] = True
    return {
        kind: True if successful_change[kind] else (False if detected_results[kind] else None)
        for kind in ("Coding", "Writing")
    }


def _coding_change_evidence(rows: Iterable[Mapping[str, Any]]) -> Optional[bool]:
    return _change_evidence_by_type(rows)["Coding"]


def _writing_change_evidence(rows: Iterable[Mapping[str, Any]]) -> Optional[bool]:
    return _change_evidence_by_type(rows)["Writing"]


def _classification_facts(
    session: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    session_id = str(session.get("id") or "")
    ended_at = session.get("ended_at")
    cache_key = (session_id, ended_at, _integer(session.get("message_count")))
    if ended_at is not None:
        with _session_classification_cache_lock:
            cached = _session_classification_cache.get(cache_key)
            if cached is not None:
                return dict(cached)

    material, calls = _enriched_session_calls(rows)
    change_evidence = _change_evidence_by_type(material, calls)
    facts = {
        "outcome": _session_outcome(session)["outcome"],
        "task_type": _session_task_type(material, calls),
        "coding_change": change_evidence["Coding"],
        "writing_change": change_evidence["Writing"],
        "near_identical_prompt_retry": _has_near_identical_prompt_retry(material),
    }
    if ended_at is not None:
        with _session_classification_cache_lock:
            for existing_key in list(_session_classification_cache):
                if existing_key[0] == session_id and existing_key != cache_key:
                    _session_classification_cache.pop(existing_key, None)
            _session_classification_cache[cache_key] = dict(facts)
    return facts


def _cached_classification_facts(session: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if session.get("ended_at") is None:
        return None
    cache_key = (
        str(session.get("id") or ""),
        session.get("ended_at"),
        _integer(session.get("message_count")),
    )
    with _session_classification_cache_lock:
        cached = _session_classification_cache.get(cache_key)
        return dict(cached) if cached is not None else None


def _acceptance_for_task(task_type: str, facts: Mapping[str, Any]) -> Tuple[bool, bool]:
    if task_type not in _SESSION_TASK_TYPE_SET:
        return False, False
    if task_type in {"General", "Analysis"}:
        valid = bool(facts.get("eligible_proxy"))
        return valid, bool(valid and facts.get("proxy_accepted"))
    if task_type == "Coding":
        valid = bool(facts.get("closed")) and facts.get("coding_change") is not None
        accepted = bool(valid and facts.get("resolved") and facts.get("coding_change"))
        return valid, accepted
    if task_type == "Writing":
        valid = bool(facts.get("closed")) and facts.get("writing_change") is not None
        accepted = bool(valid and facts.get("resolved") and facts.get("writing_change"))
        return valid, accepted
    return False, False


def _model_for_session_event(
    rows: Iterable[Mapping[str, Any]],
    timestamp: float,
    role: str = "main",
) -> Optional[str]:
    candidates = [row for row in rows if _task_role(row.get("task")) == role]
    models = {str(row.get("model") or "unknown") for row in candidates}
    if len(models) == 1:
        return next(iter(models))
    if not candidates:
        return None

    def distance(row: Mapping[str, Any]) -> float:
        first = _number(row.get("first_seen"), 0)
        last = _number(row.get("last_seen"), first)
        if first and first <= timestamp <= max(first, last) + 300:
            return 0.0
        points = [point for point in (first, last) if point]
        return min((abs(timestamp - point) for point in points), default=float("inf"))

    ordered = sorted(candidates, key=lambda row: (distance(row), -_number(row.get("first_seen"))))
    if not ordered or not math.isfinite(distance(ordered[0])):
        return None
    if len(ordered) > 1 and distance(ordered[0]) == distance(ordered[1]):
        return None
    return str(ordered[0].get("model") or "unknown")


def _model_match(raw_model: Any, known_models: Iterable[str]) -> Optional[str]:
    raw = str(raw_model or "").strip()
    if not raw:
        return None
    material = list(known_models)
    if raw in material:
        return raw
    raw_leaf = raw.rsplit("/", 1)[-1].lower()
    matches = [model for model in material if model.rsplit("/", 1)[-1].lower() == raw_leaf]
    return matches[0] if len(matches) == 1 else None

__all__ = [name for name in globals() if not name.startswith("__")]
