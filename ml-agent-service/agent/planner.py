import re
from dataclasses import dataclass
from datetime import datetime, timedelta, date as ddate, time as dtime
from typing import Optional
from agent.time_utils import parse_datetime, to_utc_z, SCHEDULE_TZ, fmt_local
from agent import constraints as C

VALID_OPS = {"delay", "advance", "unavailable", "move_venue", "move_time", "cancel", "replan"}
MAX_OPTIONS = 3


@dataclass
class Intent:
    operation: str
    shift_minutes: Optional[int]
    target_venue: Optional[str]
    target_time_of_day: Optional[str]
    target_date: Optional[str]
    window: Optional[tuple]
    unavailable_keys: frozenset


def _canonical_venue(name, items):
    """
    Returns the real venue name if it genuinely matches one on the
    schedule, or None if it doesn't - never the raw typed text as a
    fallback. Returning unverified text here is exactly what let "Move
    the match to Ground" (not a real venue) get accepted as a genuine
    destination and cascade into 20 activities colliding at once.
    """
    if not name or not name.strip():
        return None
    norm = re.sub(r"\s+", "", name.lower())
    real_venues = {it.venue for it in items.values() if it.venue}
    exact = {v for v in real_venues if re.sub(r"\s+", "", v.lower()) == norm}
    if len(exact) == 1:
        return exact.pop()
    contains = {v for v in real_venues if norm and norm in re.sub(r"\s+", "", v.lower())}
    if len(contains) == 1:
        return contains.pop()
    return None


def _parse_hhmm(text):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return (h, mi) if 0 <= h <= 23 and 0 <= mi <= 59 else None


def intent_from_understood(u: dict, items) -> Intent:
    window = None
    if u.get("time_window_start") and u.get("time_window_end"):
        try:
            window = (parse_datetime(u["time_window_start"]), parse_datetime(u["time_window_end"]))
        except ValueError:
            window = None

    # Unavailable keys come from the schedule's real venue/resource names, not the raw message text.
    keys = set()
    for v in u.get("mentioned_venues") or []:
        vl = v.strip().lower()
        for it in items.values():
            if vl and vl in it.venue.lower():
                keys.add(f"venue:{it.venue.strip().lower()}")
    for r in u.get("mentioned_resources") or []:
        rl = r.strip().lower()
        for it in items.values():
            for res in it.resources:
                if rl and rl in res.lower():
                    keys.add(f"resource:{res.strip().lower()}")

    try:
        shift = abs(int(u.get("shift_minutes"))) if u.get("shift_minutes") else None
    except (ValueError, TypeError):
        shift = None

    return Intent(
        operation=(u.get("operation") or "unclear").strip().lower(),
        shift_minutes=shift,
        target_venue=_canonical_venue(u.get("target_venue"), items),
        target_time_of_day=u.get("target_time_of_day"),
        target_date=u.get("target_date"),
        window=window,
        unavailable_keys=frozenset(keys),
    )


def no_match_question(u: dict, items: dict = None) -> str:
    parts = []
    if u.get("named_activities"):
        parts.append("activity " + ", ".join(f"'{x}'" for x in u["named_activities"]))
    if u.get("mentioned_venues"):
        parts.append("venue " + ", ".join(f"'{x}'" for x in u["mentioned_venues"]))
    if u.get("mentioned_resources"):
        parts.append("person/resource " + ", ".join(f"'{x}'" for x in u["mentioned_resources"]))
    if u.get("activity_type_keywords"):
        parts.append("type " + ", ".join(f"'{x}'" for x in u["activity_type_keywords"]))

    # If the resolved time window falls entirely outside the schedule's
    # own date range, say so directly - this is what a relative date
    # like "tomorrow" resolving against today's real calendar date,
    # rather than the schedule's actual dates, looks like from outside.
    if items and u.get("time_window_start") and u.get("time_window_end"):
        try:
            w_start = parse_datetime(u["time_window_start"]).astimezone(SCHEDULE_TZ).date()
            w_end = parse_datetime(u["time_window_end"]).astimezone(SCHEDULE_TZ).date()
            sched_dates = [it.start.astimezone(SCHEDULE_TZ).date() for it in items.values()]
            if sched_dates and (w_end < min(sched_dates) or w_start > max(sched_dates)):
                span = f"{min(sched_dates).isoformat()} to {max(sched_dates).isoformat()}"
                return (f"I read that as {w_start.isoformat()}, but this schedule only covers {span}. "
                        f"Try naming the date directly, or say 'Day 1' / 'Day 2' instead of a relative word.")
        except (ValueError, KeyError):
            pass

    if parts:
        where = " in that time window" if u.get("time_window_start") else ""
        return ("I couldn't find anything on this schedule matching " + "; ".join(parts) + where
                + ". Check the spelling, or tell me which activity you mean.")
    return ("I couldn't tell which activities you mean. Name an activity, a venue, a person or resource, "
            "or a time window (for example 'Day 1 morning').")


def missing_info(intent: Intent, affected) -> Optional[str]:
    op = intent.operation
    if op not in VALID_OPS:
        return ("I found the activities you mean, but I'm not sure what change you want. Should I delay them, "
                "move them to another venue or time, cancel them, or find alternatives?")
    if op in ("delay", "advance") and not intent.shift_minutes:
        return "By how many minutes should I shift them?"
    if op == "unavailable" and not (intent.window and intent.unavailable_keys):
        return "Which venue or person is unavailable, and from what time to what time?"
    if op == "move_venue" and not intent.target_venue:
        return "Which venue should they move to?"
    if op == "move_time" and not _parse_hhmm(intent.target_time_of_day):
        return "What time should it start?"
    return None


def build_blocked(intent: Intent, affected) -> list:
    """Windows that new placements must not touch. Shared by the planner and the validator."""
    if intent.operation == "unavailable" and intent.window and intent.unavailable_keys:
        return [(intent.window[0], intent.window[1], intent.unavailable_keys)]
    if intent.operation == "replan":
        return [(a.start, a.end, frozenset({f"venue:{a.venue.strip().lower()}"})) for a in affected]
    return []

def _earliest_start_for(item, items, placements):
    """The floor this activity's start must respect: a dependency's
    just-placed new time if it's ALSO being moved in this same batch,
    otherwise its original unchanged end time. None if no active
    dependencies apply."""
    if not item.dependencies:
        return None
    ends = []
    for dep_id in item.dependencies:
        dep = items.get(dep_id)
        if not dep or dep.status in C.INACTIVE_STATUSES:
            continue
        if dep_id in placements:
            _, dep_start = placements[dep_id]
            ends.append(dep_start + timedelta(minutes=dep.minutes))
        else:
            ends.append(dep.end)
    return max(ends) if ends else None

def _place(items, affected, venues_fn, direction, blocked, bounds, start_fn=None):
    occ = C.occupancy(items, exclude_ids={a.id for a in affected})
    # Later/earlier search direction keeps its existing preference for
    # unrelated activities; a dependency relationship always wins over
    # it, via topological_order.
    tiebreak = (
        (lambda i: (items[i].start, items[i].title)) if direction > 0
        else (lambda i: (-items[i].start.timestamp(), items[i].title))
    )
    ordered_ids = C.topological_order([a.id for a in affected], items, key=tiebreak)

    placements, unresolved = {}, []
    for aid in ordered_ids:
        it = items[aid]
        start0 = start_fn(it) if start_fn else it.start
        earliest = _earliest_start_for(it, items, placements)
        found = C.find_slot(it, venues_fn(it), start0, direction, occ, blocked, bounds, earliest_start=earliest)
        if not found:
            unresolved.append(it.id)
            continue
        venue, start = found
        placements[it.id] = (venue, start)
        occ.append(C.Occ(it.id, start, start + timedelta(minutes=it.minutes), C.keys_for(venue, it.resources)))
    return placements, unresolved


def _changes_of(placements, items):
    changes = []
    for aid, (venue, start) in placements.items():
        it = items[aid]
        if venue != it.venue:
            changes.append({"activity_id": aid, "field": "venue", "new_value": venue})
        if start != it.start:
            changes.append({"activity_id": aid, "field": "scheduledStart", "new_value": to_utc_z(start)})
    return changes


def _target_start(it, intent):
    hh, mm = _parse_hhmm(intent.target_time_of_day)
    try:
        d = ddate.fromisoformat(intent.target_date) if intent.target_date else it.start.astimezone(SCHEDULE_TZ).date()
    except ValueError:
        d = it.start.astimezone(SCHEDULE_TZ).date()
    return datetime.combine(d, dtime(hh, mm), tzinfo=SCHEDULE_TZ)


def _describe(cand, intent, items):
    strategy, changes, placements = cand["strategy"], cand["changes"], cand["placements"]
    n = len({c["activity_id"] for c in changes})
    noun = "activity" if n == 1 else "activities"
    verb = "Delay" if intent.operation == "delay" else "Bring forward"
    if strategy == "later":
        return f"Move {n} {noun} to the next free time, keeping the same venue"
    if strategy == "earlier":
        return f"Move {n} {noun} to the latest free time before the disruption, keeping the same venue"
    if strategy == "relocate":
        same_time = all(start == items[i].start for i, (_, start) in placements.items())
        return (f"Move {n} {noun} to another venue at the same time" if same_time
                else f"Move {n} {noun} to another venue at the nearest free time")
    if strategy == "shift_exact":
        return f"{verb} {n} {noun} by {intent.shift_minutes} min, keeping venues"
    if strategy == "shift_repair":
        return f"{verb} by {intent.shift_minutes} min, then move anything that would collide to the next free time"
    if strategy == "as_requested_venue":
        return f"Move {n} {noun} to {intent.target_venue}, keeping the original times"
    if strategy == "first_free_target":
        return f"Move {n} {noun} to {intent.target_venue} at the nearest free times"
    if strategy == "as_requested_time":
        first = next(iter(placements.values()))[1]
        return f"Start {n} {noun} at {fmt_local(first, with_date=False)}"
    if strategy == "nearest_free_time":
        return f"Move {n} {noun} to the nearest free time after the requested start, keeping the venue"
    if strategy == "cancel":
        return f"Cancel {n} {noun}"
    return f"Change {n} {noun}"


def plan_options(intent: Intent, items, affected):
    op = intent.operation
    bounds = C.day_bounds(items)
    blocked = build_blocked(intent, affected)
    all_venues = sorted({it.venue for it in items.values() if it.venue}, key=C.natural_key)
    candidates, unplaced = [], set()
    same_venue = lambda it: [it.venue]

    def add(strategy, placements, unresolved):
        if unresolved:
            unplaced.update(unresolved)
            return
        changes = _changes_of(placements, items)
        if changes:
            candidates.append({"strategy": strategy, "changes": changes, "placements": placements})

    def has_issue(placements):
        ev = C.evaluate_option(_changes_of(placements, items), items, blocked)
        return bool(ev["new_pairs"] or ev["violations"] or ev.get("dependency_violations"))
    
    if op in ("delay", "advance"):
        sign = 1 if op == "delay" else -1
        delta = timedelta(minutes=sign * intent.shift_minutes)
        exact = {a.id: (a.venue, a.start + delta) for a in affected}
        add("shift_exact", exact, [])
        if has_issue(exact):
            p, u = _place(items, affected, same_venue, sign, blocked, bounds, lambda it: it.start + delta)
            add("shift_repair", p, u)

    elif op in ("unavailable", "replan"):
        p, u = _place(items, affected, same_venue, 1, blocked, bounds)
        add("later", p, u)
        # Relocating only helps when a VENUE is the problem. If a person is unavailable, changing rooms cannot fix it.
        if op == "replan" or any(k.startswith("venue:") for k in intent.unavailable_keys):
            def venues(it):
                cands = C.similar_venues(it.venue, all_venues)
                return [v for v in cands if f"venue:{v.strip().lower()}" not in intent.unavailable_keys]
            p, u = _place(items, affected, venues, 1, blocked, bounds)
            add("relocate", p, u)
        p, u = _place(items, affected, same_venue, -1, blocked, bounds)
        add("earlier", p, u)

    elif op == "move_venue":
        asis = {a.id: (intent.target_venue, a.start) for a in affected}
        add("as_requested_venue", asis, [])
        if has_issue(asis):
            p, u = _place(items, affected, lambda it: [intent.target_venue], 1, blocked, bounds)
            add("first_free_target", p, u)

    elif op == "move_time":
        asis = {a.id: (a.venue, _target_start(a, intent)) for a in affected}
        add("as_requested_time", asis, [])
        if has_issue(asis):
            p, u = _place(items, affected, same_venue, 1, blocked, bounds, lambda it: _target_start(it, intent))
            add("nearest_free_time", p, u)

    elif op == "cancel":
        candidates.append({
            "strategy": "cancel", "placements": None,
            "changes": [{"activity_id": a.id, "field": "status", "new_value": "cancelled"} for a in affected],
        })

    options, seen = [], set()
    for cand in candidates:
        sig = frozenset((c["activity_id"], c["field"], str(c["new_value"])) for c in cand["changes"])
        if not cand["changes"] or sig in seen:
            continue
        seen.add(sig)
        options.append({
            "strategy": cand["strategy"],
            "description": _describe(cand, intent, items),
            "changes": cand["changes"],
        })
    return options[:MAX_OPTIONS], unplaced


def _clock(minutes):
    h, m = divmod(int(minutes), 60)
    return f"{h % 12 or 12}:{m:02d} {'AM' if h % 24 < 12 else 'PM'}"


def failure_message(unplaced_ids, items) -> str:
    open_min, close_min = C.day_bounds(items)
    titles = ", ".join(items[i].title for i in sorted(unplaced_ids)[:5] if i in items)
    if titles:
        return (f"I couldn't find a conflict-free arrangement for {titles} within the hours this schedule already "
                f"uses ({_clock(open_min)} to {_clock(close_min)}). Free up a slot, or tell me a specific time or venue to try.")
    return "I couldn't find a change that would help here. Tell me a specific time or venue to try."