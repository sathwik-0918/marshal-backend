import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from agent.time_utils import parse_datetime, SCHEDULE_TZ

STEP_MINUTES = 15
INACTIVE_STATUSES = ("cancelled",)


@dataclass(frozen=True)
class Item:
    id: str
    title: str
    venue: str
    resources: tuple
    dependencies: tuple
    start: datetime
    minutes: int
    activity_type: str
    status: str

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.minutes)


@dataclass(frozen=True)
class Occ:
    id: str
    start: datetime
    end: datetime
    keys: frozenset


def natural_key(text: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def keys_for(venue, resources) -> frozenset:
    keys = set()
    if venue and venue.strip():
        keys.add(f"venue:{venue.strip().lower()}")
    for r in resources or ():
        if r and r.strip():
            keys.add(f"resource:{r.strip().lower()}")
    return frozenset(keys)


def overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def build_items(activities: list) -> dict:
    items = {}
    for a in activities:
        try:
            start = parse_datetime(a["scheduledStart"])
            minutes = int(a.get("durationMinutes") or 0)
        except (ValueError, KeyError, TypeError):
            continue
        items[a["_id"]] = Item(
            id=a["_id"],
            title=a.get("title") or "Untitled",
            venue=a.get("venue") or "",
            resources=tuple(a.get("requiredResources") or ()),
            dependencies=tuple(str(d) for d in (a.get("dependencies") or ())),
            start=start,
            minutes=minutes,
            activity_type=a.get("activityType") or "",
            status=a.get("status") or "scheduled",
        )
    return items


def day_bounds(items: dict) -> tuple:
    starts, ends = [], []
    for it in items.values():
        if it.status in INACTIVE_STATUSES or it.minutes <= 0:
            continue
        local = it.start.astimezone(SCHEDULE_TZ)
        s = local.hour * 60 + local.minute
        starts.append(s)
        ends.append(min(s + it.minutes, 24 * 60))
    if not starts:
        return (0, 24 * 60)
    return (min(starts), max(ends))


def occupancy(items: dict, exclude_ids=()) -> list:
    return [
        Occ(it.id, it.start, it.end, keys_for(it.venue, it.resources))
        for it in items.values()
        if it.id not in exclude_ids and it.status not in INACTIVE_STATUSES and it.minutes > 0
    ]


def topological_order(activity_ids: list, items: dict, key=None) -> list:
    """
    Orders activity_ids so a dependency always comes before its
    dependent, restricted to dependencies that are ALSO in
    activity_ids - one that isn't being moved keeps its existing
    time, so there's nothing to order it against.

    key breaks ties among activities with no dependency relationship,
    re-sorted every time a new one becomes orderable, so the result
    stays deterministic run to run.

    A genuine cycle can't be satisfied by any ordering; cyclic members
    are appended in their original order rather than raising, and
    evaluate_option surfaces whatever inconsistency results - this
    function doesn't pretend to have resolved one.
    """
    key = key or (lambda i: (items[i].start, items[i].title))
    id_set = set(activity_ids)
    in_degree = {i: 0 for i in activity_ids}
    edges = {i: [] for i in activity_ids}
    for i in activity_ids:
        for dep in items[i].dependencies:
            if dep in id_set:
                edges[dep].append(i)
                in_degree[i] += 1

    ready = sorted([i for i in activity_ids if in_degree[i] == 0], key=key)
    ordered = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        newly_ready = []
        for nxt in edges[node]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                newly_ready.append(nxt)
        ready = sorted(ready + newly_ready, key=key)

    remaining = [i for i in activity_ids if i not in ordered]
    return ordered + remaining


def find_slot(item, venues, start0, direction, occ_list, blocked, bounds, step=STEP_MINUTES, earliest_start=None):
    """First (venue, start) with no clash against occupied slots, blocked windows, or the daily
    hours, and never before earliest_start if given - this activity's dependency floor."""
    open_min, close_min = bounds
    local0 = start0.astimezone(SCHEDULE_TZ)
    midnight = datetime.combine(local0.date(), datetime.min.time(), tzinfo=SCHEDULE_TZ)
    day_open = midnight + timedelta(minutes=open_min)
    day_close = midnight + timedelta(minutes=close_min)
    dur = timedelta(minutes=item.minutes)
    delta = timedelta(minutes=step if direction > 0 else -step)
    t = start0
    if earliest_start and t < earliest_start and direction > 0:
        t = earliest_start
    for _ in range((24 * 60) // step + 1):
        if direction > 0 and t + dur > day_close:
            return None
        if direction < 0 and t < day_open:
            return None
        if earliest_start and t < earliest_start:
            if direction < 0:
                return None  # searching earlier hit the dependency floor - nothing valid before it
            t += delta
            continue
        for venue in venues:
            keys = keys_for(venue, item.resources)
            clash = any((o.keys & keys) and overlaps(t, t + dur, o.start, o.end) for o in occ_list)
            hit = any((bk & keys) and overlaps(t, t + dur, bs, be) for bs, be, bk in blocked)
            if not clash and not hit:
                return venue, t
        t += delta
    return None


def similar_venues(venue, all_venues):
    tokens = set(re.findall(r"[a-z]+", venue.lower()))
    scored = []
    for v in all_venues:
        if v == venue:
            continue
        scored.append((len(tokens & set(re.findall(r"[a-z]+", v.lower()))), v))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    pool = [v for s, v in scored if s == best] if best > 0 else [v for _, v in scored]
    return sorted(pool, key=natural_key)


def evaluate_option(changes, items, blocked):
    """Recompute everything from the final change list alone. Used by the planner AND the validator."""
    state = {
        i: {"venue": it.venue, "start": it.start, "cancelled": it.status in INACTIVE_STATUSES}
        for i, it in items.items()
    }
    changed, unverifiable = [], []
    for c in changes:
        aid = c.get("activity_id")
        if aid not in items:
            continue
        st = state[aid]
        field = c.get("field")
        try:
            if field == "venue":
                st["venue"] = str(c["new_value"])
            elif field == "scheduledStart":
                st["start"] = parse_datetime(c["new_value"])
            elif field == "status" and str(c["new_value"]).lower() == "cancelled":
                st["cancelled"] = True
        except (ValueError, KeyError, TypeError):
            unverifiable.append(aid)
        if aid not in changed:
            changed.append(aid)

    def span(i):
        s = state[i]["start"]
        return s, s + timedelta(minutes=items[i].minutes)

    def keys_now(i):
        return keys_for(state[i]["venue"], items[i].resources)

    def keys_orig(i):
        return keys_for(items[i].venue, items[i].resources)

    new_pairs, old_pairs, violations = {}, {}, set()
    for a in changed:
        if state[a]["cancelled"]:
            continue
        a_s, a_e = span(a)
        a_keys = keys_now(a)
        for b in items:
            if b == a or state[b]["cancelled"]:
                continue
            shared = a_keys & keys_now(b)
            if not shared:
                continue
            b_s, b_e = span(b)
            if not overlaps(a_s, a_e, b_s, b_e):
                continue
            was_overlapping = bool(keys_orig(a) & keys_orig(b)) and overlaps(
                items[a].start, items[a].end, items[b].start, items[b].end
            )
            (old_pairs if was_overlapping else new_pairs)[frozenset((a, b))] = sorted(shared)[0]
        for bs, be, bk in blocked:
            if (a_keys & bk) and overlaps(a_s, a_e, bs, be):
                violations.add(a)

    # Runs over EVERY activity, not just changed ones - moving a
    # dependency can break an unchanged activity that relies on it,
    # and that needs to surface just as clearly as a venue conflict.
    dependency_new, dependency_old = set(), set()
    for i, it in items.items():
        if state[i]["cancelled"] or not it.dependencies:
            continue
        active_now = [d for d in it.dependencies if d in items and not state[d]["cancelled"]]
        if not active_now:
            continue
        dep_end_now = max(state[d]["start"] + timedelta(minutes=items[d].minutes) for d in active_now)
        if state[i]["start"] < dep_end_now:
            active_orig = [d for d in it.dependencies if d in items and items[d].status not in INACTIVE_STATUSES]
            was_violated = bool(active_orig) and items[i].start < max(items[d].end for d in active_orig)
            (dependency_old if was_violated else dependency_new).add(i)

    shifts = [abs(int((state[a]["start"] - items[a].start).total_seconds() // 60)) for a in changed]
    return {
        "changed_ids": changed,
        "new_pairs": new_pairs,
        "old_pairs": old_pairs,
        "violations": sorted(violations),
        "dependency_violations": sorted(dependency_new),
        "dependency_preexisting": sorted(dependency_old),
        "unverifiable": unverifiable,
        "has_venue_change": any(state[a]["venue"] != items[a].venue for a in changed),
        "cancelled_ids": [a for a in changed if state[a]["cancelled"] and items[a].status not in INACTIVE_STATUSES],
        "max_shift_minutes": max(shifts) if shifts else 0,
        "final": state,
    }


def _label(key: str) -> str:
    return key.split(":", 1)[1].title()


def describe_evaluation(ev, items):
    warnings, notes = [], []
    for pair, key in ev["new_pairs"].items():
        a, b = sorted(pair, key=lambda i: items[i].title)
        warnings.append(f"{items[a].title} would overlap {items[b].title} ({_label(key)})")
    for aid in ev["violations"]:
        warnings.append(f"{items[aid].title} still lands inside the time or place reported as unavailable")
    for aid in ev.get("dependency_violations", []):
        warnings.append(f"{items[aid].title} would start before the activity it depends on has finished")
    for aid in ev["unverifiable"]:
        warnings.append(f"Couldn't verify the new time for {items[aid].title} - check it manually")
    for pair, key in ev["old_pairs"].items():
        a, b = sorted(pair, key=lambda i: items[i].title)
        notes.append(
            f"{items[a].title} and {items[b].title} already overlapped in the original schedule "
            f"({_label(key)}); this option doesn't change that"
        )
    for aid in ev.get("dependency_preexisting", []):
        notes.append(f"{items[aid].title} already started before its dependency finished; this option doesn't change that")
    return warnings, notes


def ml_overrun_notes(ev, items, ml):
    notes = []
    final = ev["final"]
    for aid in ev["changed_ids"]:
        pred = ml.get(aid)
        it = items[aid]
        if not pred or final[aid]["cancelled"]:
            continue
        extra = int(pred.get("predicted_duration_minutes", it.minutes)) - it.minutes
        if extra < 15:
            continue
        end = final[aid]["start"] + timedelta(minutes=it.minutes)
        keys = keys_for(final[aid]["venue"], it.resources)
        for bid, b in items.items():
            if bid == aid or final[bid]["cancelled"]:
                continue
            if not (keys & keys_for(final[bid]["venue"], b.resources)):
                continue
            if end <= final[bid]["start"] < end + timedelta(minutes=extra):
                notes.append(f"{it.title} is predicted to run about {extra} min long and could overlap {b.title}")
                break
    return notes


def risk_level(warnings, ev, has_ml_notes=False) -> str:
    if warnings:
        return "high"
    if (ev["cancelled_ids"] or ev["has_venue_change"] or len(ev["changed_ids"]) > 3
            or ev["max_shift_minutes"] >= 30 or has_ml_notes):
        return "medium"
    return "low"