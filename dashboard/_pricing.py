"""Per-token rates derived from the costs Hermes itself recorded.

Hermes' price tables are not a published plugin API (agent.usage_pricing
exposes only its result type), so Session Lens does not import them. It does
not need to: every session_model_usage row carries the token counts and the
cost Hermes computed for them with those tables. Cost there is linear in the
four token kinds Hermes accounts separately (uncached input, output, cache
reads, cache writes), so a least-squares fit over a model's recent rows
recovers Hermes' own per-token rates, and anything priced with them
reconciles with the session costs Hermes records by construction.

A model Hermes never priced on this install (a subscription route, a model
never run) has no rows and therefore no rate: the caller shows it unpriced
rather than guessing. Long-context surcharges make a fit an average, which is
what every caller here wants.
"""

from __future__ import annotations

from types import SimpleNamespace

from ._common import *
from ._hermes_compat import _database, _db_connection

_RATE_TTL_SECONDS = 3600.0
_RATE_SAMPLE_SIZES = (400, 120, 40, 12)
_RATE_MIN_ROWS = 3
# A fit must reproduce Hermes' recorded costs to within this median relative error.
_RATE_MAX_MEDIAN_ERROR = 0.02
_RATE_COLUMNS = ("input", "output", "cache_read", "cache_write")
_rate_cache: Dict[Tuple[str, str], Tuple[float, Optional[SimpleNamespace]]] = {}


def _solve_linear(matrix: List[List[float]], vector: List[float]) -> Optional[List[float]]:
    """Gaussian elimination with partial pivoting; None when singular."""
    size = len(vector)
    rows = [list(matrix[index]) + [vector[index]] for index in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda index: abs(rows[index][column]))
        if abs(rows[pivot][column]) < 1e-12:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for index in range(size):
            if index == column:
                continue
            factor = rows[index][column] / rows[column][column]
            if factor:
                rows[index] = [left - factor * right for left, right in zip(rows[index], rows[column])]
    return [rows[index][size] / rows[index][index] for index in range(size)]


def _fit_rates(samples: List[Tuple[float, float, float, float, float]]) -> Optional[Dict[str, float]]:
    """USD per million tokens for each token kind present, or None.

    Columns the samples never use are left out; a column whose fitted rate
    comes out negative (collinear with another, e.g. a provider that bills
    cache reads at the input rate) is dropped and the fit repeated.
    """
    active = [index for index in range(4) if any(sample[index] > 0 for sample in samples)]
    if 0 not in active:
        return None
    while active:
        size = len(active)
        normal = [[0.0] * size for _ in range(size)]
        target = [0.0] * size
        for sample in samples:
            features = [sample[index] / 1e6 for index in active]
            cost = sample[4]
            for row in range(size):
                target[row] += features[row] * cost
                for column in range(size):
                    normal[row][column] += features[row] * features[column]
        solution = _solve_linear(normal, target)
        if solution is None:
            if len(active) == 1:
                return None
            active.pop()
            continue
        negative = [(value, position) for position, value in enumerate(solution) if value < 0]
        if not negative:
            return {_RATE_COLUMNS[index]: max(0.0, value) for index, value in zip(active, solution)}
        _value, position = min(negative)
        if active[position] == 0:
            return None
        active.pop(position)
    return None


def _fit_quality_ok(rates: Mapping[str, float], samples: List[Tuple[float, float, float, float, float]]) -> bool:
    """Input and output must both be priced, and the fit must reproduce the recorded costs."""
    uses_output = any(sample[1] > 0 for sample in samples)
    if "input" not in rates or (uses_output and "output" not in rates):
        return False
    errors = []
    for sample in samples:
        predicted = sum(sample[index] / 1e6 * rates.get(_RATE_COLUMNS[index], 0.0) for index in range(4))
        if sample[4] > 0:
            errors.append(abs(predicted - sample[4]) / sample[4])
    if not errors:
        return False
    errors.sort()
    return errors[len(errors) // 2] <= _RATE_MAX_MEDIAN_ERROR


def _best_fit(samples: List[Tuple[float, float, float, float, float]]) -> Optional[Dict[str, float]]:
    """Newest rows first; shrink the sample until one price version fits cleanly."""
    for size in _RATE_SAMPLE_SIZES:
        window = samples[:size]
        if len(window) < _RATE_MIN_ROWS:
            break
        rates = _fit_rates(window)
        if rates and _fit_quality_ok(rates, window):
            return rates
    return None


def _recorded_samples(model: str, provider: str) -> List[Tuple[float, float, float, float, float]]:
    clauses = ["model = ?"]
    params: List[Any] = [model]
    if provider:
        clauses.append("LOWER(billing_provider) = ?")
        params.append(provider.lower())
    with _database() as db:
        rows = _db_connection(db).execute(
            f"""
            SELECT COALESCE(input_tokens, 0), COALESCE(output_tokens, 0),
                   COALESCE(cache_read_tokens, 0), COALESCE(cache_write_tokens, 0),
                   CASE WHEN actual_cost_usd > 0 THEN actual_cost_usd ELSE estimated_cost_usd END AS cost
            FROM session_model_usage
            WHERE {' AND '.join(clauses)}
              AND (actual_cost_usd > 0 OR estimated_cost_usd > 0)
              AND COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) > 0
            ORDER BY COALESCE(last_seen, first_seen, 0) DESC
            LIMIT {_RATE_SAMPLE_SIZES[0]}
            """,
            params,
        ).fetchall()
    return [tuple(float(value or 0) for value in row) for row in rows]


def _recorded_pricing_entry(model: Any, provider: Any = None, base_url: Any = None) -> Optional[SimpleNamespace]:
    """A price entry shaped like Hermes' own, fitted from Hermes' recorded costs.

    Attributes match what callers read from Hermes' entries:
    ``input_cost_per_million``, ``output_cost_per_million``,
    ``cache_read_cost_per_million``, ``cache_write_cost_per_million``
    (None when this install never recorded that token kind for the model),
    ``source`` and ``pricing_version``. None when there is nothing to fit.
    """
    model_id = str(model or "").strip()
    provider_id = str(provider or "").strip()
    if not model_id:
        return None
    key = (model_id, provider_id.lower())
    now = time.time()
    cached = _rate_cache.get(key)
    if cached and now - cached[0] < _RATE_TTL_SECONDS:
        return cached[1]
    entry: Optional[SimpleNamespace] = None
    try:
        samples = _recorded_samples(model_id, provider_id)
        if len(samples) < _RATE_MIN_ROWS and provider_id:
            samples = _recorded_samples(model_id, "")
        rates = _best_fit(samples)
    except Exception:
        rates = None
    if rates:
        entry = SimpleNamespace(
            input_cost_per_million=rates.get("input"),
            output_cost_per_million=rates.get("output"),
            cache_read_cost_per_million=rates.get("cache_read"),
            cache_write_cost_per_million=rates.get("cache_write"),
            source="fitted from costs Hermes recorded",
            pricing_version="hermes-recorded",
        )
    _rate_cache[key] = (now, entry)
    return entry


__all__ = [name for name in globals() if not name.startswith("__")]
