"""Atomic source-vintage selection; commit order is explicit, never inferred."""
from datetime import date, timedelta
import json
from .request import digest
from .era5.resources import atomic_json


class CacheError(ValueError):
    pass


def scope(request):
    return digest({k: request[k] for k in ("source", "bbox", "evidence_kind")})


def coverage(plan):
    day = date.fromisoformat(plan["request"]["start"])
    last = date.fromisoformat(plan["request"]["end_exclusive"])
    keys = []
    while day < last:
        keys.extend(f"{day}:{p}" for p in plan["required_inputs"])
        day += timedelta(days=1)
    return keys


def read_index(root):
    path = root / "ACTIVE_GENERATIONS.json"
    if not path.exists():
        return dict(version=1, scopes={})
    try:
        state = json.loads(path.read_text())
        if state.get("sha256") != digest({k: v for k, v in state.items() if k != "sha256"}):
            raise ValueError()
        if state["version"] != 1:
            raise ValueError()
        return state
    except (OSError, ValueError, KeyError):
        raise CacheError("Active generation index is corrupt; explicit reconciliation required.") from None


def selected(plan, root):
    state = read_index(root)
    entry = state["scopes"].get(scope(plan["request"]), {})
    active = entry.get("coverage", {})
    selections = {key: active.get(key, {}).get("generation", "retained") for key in coverage(plan)}
    generations = set(selections.values())
    if len(generations) != 1:
        raise CacheError("Requested coverage spans incompatible source generations; refresh the whole request or pin a result_id.")
    witnesses = {active[key]["result_id"] for key in selections if key in active}
    return next(iter(generations)), witnesses, entry.get("results", [])


def promote(plan, root, manifest, *, refresh):
    """Caller holds namespace lease and has validated all immutable evidence."""
    state = read_index(root)
    entry = state["scopes"].setdefault(scope(plan["request"]), dict(coverage={}, results=[]))
    witness = dict(generation=manifest["source_generation"], result_id=manifest["result_id"])
    for key in coverage(plan):
        if refresh or key not in entry["coverage"]:
            entry["coverage"][key] = witness
    if manifest["result_id"] not in entry["results"]:
        entry["results"].append(manifest["result_id"])
    state.pop("sha256", None)
    state["sha256"] = digest(state)
    # Every committed state has immutable history. A crash before the active
    # replace leaves the prior pointer authoritative; orphan history is harmless.
    history = root / "generations"
    history.mkdir(exist_ok=True)
    snapshot = history / (state["sha256"] + ".json")
    if not snapshot.exists():
        atomic_json(snapshot, state)
    atomic_json(root / "ACTIVE_GENERATIONS.json", state)
