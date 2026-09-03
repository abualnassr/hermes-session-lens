"""Work-reliability statistics and Wilson confidence bounds."""

from __future__ import annotations

try:
    from ._common import *
except ImportError:  # pragma: no cover
    from _common import *

_WORK_RELIABILITY_ELIGIBLE_STATUSES = {"clean", "completed", "recovered", "unrecovered"}


def _wilson_upper_bound(failures: int, samples: int, z_score: float = 1.96) -> Optional[float]:
    """Return the upper Wilson bound for a binomial failure rate."""
    if samples <= 0:
        return None
    failure_count = max(0, min(_integer(failures), _integer(samples)))
    sample_count = _integer(samples)
    proportion = failure_count / sample_count
    z_squared = z_score * z_score
    denominator = 1 + z_squared / sample_count
    centre = proportion + z_squared / (2 * sample_count)
    margin = z_score * math.sqrt(
        proportion * (1 - proportion) / sample_count
        + z_squared / (4 * sample_count * sample_count)
    )
    return max(0.0, min(1.0, (centre + margin) / denominator))


def _work_reliability_counts(
    runs: Iterable[Mapping[str, Any]],
    sample_threshold: int,
) -> Dict[str, Any]:
    material = list(runs)
    statuses = Counter(str(run.get("status") or "unknown") for run in material)
    clean = statuses.get("clean", 0)
    recovered = statuses.get("recovered", 0)
    completed = clean + recovered + statuses.get("completed", 0)
    unrecovered = statuses.get("unrecovered", 0)
    eligible = completed + unrecovered
    recovery_samples = recovered + unrecovered
    threshold = max(1, _integer(sample_threshold, DEFAULT_RATE_SAMPLE_THRESHOLD))
    ineligible_reasons = Counter(
        str(run.get("reason_key") or run.get("status") or "unknown")
        for run in material
        if str(run.get("status") or "unknown") not in _WORK_RELIABILITY_ELIGIBLE_STATUSES
    )
    return {
        "ineligible_reasons": [
            {"label": label, "count": count}
            for label, count in sorted(ineligible_reasons.items(), key=lambda item: (-item[1], item[0]))
        ],
        "eligible_tasks": eligible,
        "completed_tasks": completed,
        "clean_completions": clean,
        "recovered_tasks": recovered,
        "unrecovered_failures": unrecovered,
        "switched_away_tasks": statuses.get("switched_away", 0),
        "unknown_tasks": statuses.get("unknown", 0),
        "excluded_tasks": statuses.get("excluded", 0),
        "completion_rate": completed / eligible if eligible else None,
        "clean_completion_rate": clean / eligible if eligible else None,
        "unrecovered_failure_rate": unrecovered / eligible if eligible else None,
        "recovery_samples": recovery_samples,
        "recovery_rate": recovered / recovery_samples if recovery_samples else None,
        "failure_rate_upper_bound_95": _wilson_upper_bound(unrecovered, eligible),
        "rank_eligible": eligible >= threshold,
        "sample_threshold": threshold,
    }


def _work_reliability_payload(
    runs: Iterable[Mapping[str, Any]],
    route_labels: Mapping[str, str],
    sample_threshold: int,
) -> Dict[str, Any]:
    material = list(runs)
    payload = _work_reliability_counts(material, sample_threshold)

    by_task_type: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    by_route: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for run in material:
        by_task_type[str(run.get("task_type") or "General")].append(run)
        route_key = str(run.get("route_key") or "")
        by_route[route_labels.get(route_key) or "Unmapped (edit in config)"].append(run)

    payload["by_task_type"] = [
        {
            "label": task_type,
            **_work_reliability_counts(task_runs, sample_threshold),
        }
        for task_type, task_runs in sorted(
            by_task_type.items(),
            key=lambda item: (_SESSION_TASK_TYPES.index(item[0]) if item[0] in _SESSION_TASK_TYPE_SET else 99, item[0]),
        )
    ]
    payload["by_route"] = [
        {
            "label": route_label,
            **_work_reliability_counts(route_runs, sample_threshold),
        }
        for route_label, route_runs in sorted(
            by_route.items(),
            key=lambda item: (-len(item[1]), item[0].lower()),
        )
    ]
    payload["rank"] = None
    payload["ranked_models"] = 0
    payload["confidence_level"] = 0.95
    payload["coverage"] = "bounded_logs" if _has_work_reliability_coverage(material) else "unavailable"
    return payload


def _has_work_reliability_coverage(runs: Iterable[Mapping[str, Any]]) -> bool:
    return any(str(run.get("status") or "") in _WORK_RELIABILITY_ELIGIBLE_STATUSES | {"unknown"} for run in runs)

__all__ = [name for name in globals() if not name.startswith("__")]
