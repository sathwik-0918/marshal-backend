"""
Executes an already-extracted QuerySpec against the real activities in
memory. No LLM call here - that happens once, in nodes.py, to produce
the spec. Every count, filter, and lookup below is plain Python, so the
answer can never be arithmetic an LLM made up.
"""
from agent.time_utils import fmt_local, SCHEDULE_TZ
from agent import constraints as C


def _day_label(dt, day_index_by_date):
    d = dt.astimezone(SCHEDULE_TZ).date()
    return f"Day {day_index_by_date.get(d, '?')}"


def _apply_filters(items: dict, spec, day_index_by_date):
    """Every stated filter is ANDed together - this is specifically what
    fixes 'Day 1 games' wrongly including a workshop: day_number=1 alone
    isn't enough, activity_type_keywords must ALSO match, and a
    workshop's type is never in that list."""
    pool = list(items.values())

    if spec.day_number:
        target_dates = {d for d, i in day_index_by_date.items() if i == spec.day_number}
        pool = [it for it in pool if it.start.astimezone(SCHEDULE_TZ).date() in target_dates]

    if spec.venue:
        v = spec.venue.strip().lower()
        pool = [it for it in pool if v in it.venue.lower() or it.venue.lower() in v]

    if spec.activity_type_keywords:
        wanted = {t.strip().lower() for t in spec.activity_type_keywords if t.strip()}
        pool = [it for it in pool if it.activity_type.lower() in wanted]

    if spec.activity_name:
        name = spec.activity_name.strip().lower()
        pool = [it for it in pool if name in it.title.lower()]

    return pool


def _execute_reference_query(spec, reference_entries: list) -> dict | None:
    """Checked before activity logic. Returns None to fall through
    unchanged if this isn't better answered from reference entries -
    never intercepts a question that's actually about an Activity."""
    if not reference_entries:
        return None

    weekday = (spec.weekday or "").strip().lower()
    name = (spec.activity_name or "").strip().lower()
    keywords = [k.strip().lower() for k in (spec.activity_type_keywords or [])]
    if not weekday and not name and not keywords:
        return None

    def matches(entry):
        title = entry.get("title", "").lower()
        if name and name not in title:
            return False
        if keywords and not any(kw in title for kw in keywords):
            return False
        if weekday and entry.get("entryType") == "recurring_weekly" and entry.get("weekday", "").lower() != weekday:
            return False
        return True

    candidates = [e for e in reference_entries if matches(e)]
    if not candidates:
        return None  # let activity search try instead - might genuinely be an activity question

    lines = []
    for e in candidates[:15]:
        desc = f" — {e['description']}" if e.get("description") else ""
        if e.get("entryType") == "recurring_weekly":
            time_part = f" ({e['startTime']}-{e['endTime']})" if e.get("startTime") else ""
            venue_part = f" at {e['venue']}" if e.get("venue") else ""
            lines.append(f"{e['title']} on {e.get('weekday', '')}{time_part}{venue_part}{desc}")
        else:
            lines.append(f"{e['title']}: {e.get('startDate', '?')} to {e.get('endDate', '?')}{desc}")
    return {"answer": "; ".join(lines), "matched_ids": [e.get("_id") for e in candidates]}


def execute(spec, activities: list, reference_entries: list = None) -> dict:
    ref_result = _execute_reference_query(spec, reference_entries or [])
    if ref_result is not None:
        return ref_result

    items = C.build_items(activities)
    if not items:
        return {"answer": "This schedule doesn't have any activities yet.", "matched_ids": []}

    dates = sorted({it.start.astimezone(SCHEDULE_TZ).date() for it in items.values()})
    day_index_by_date = {d: i + 1 for i, d in enumerate(dates)}
    active = {i: it for i, it in items.items() if it.status not in C.INACTIVE_STATUSES}

    filtered = sorted(_apply_filters(active, spec, day_index_by_date), key=lambda it: it.start)
    qt = (spec.query_type or "unsupported").lower()

    scope_desc = []
    if spec.day_number:
        scope_desc.append(f"Day {spec.day_number}")
    if spec.venue:
        scope_desc.append(spec.venue)
    if spec.activity_type_keywords:
        scope_desc.append("/".join(spec.activity_type_keywords))
    scope_label = " ".join(scope_desc) if scope_desc else "the schedule"

    if qt == "count":
        return {"answer": f"{len(filtered)} activities match {scope_label}.", "matched_ids": [it.id for it in filtered]}

    if qt == "total_duration":
        total = sum(it.minutes for it in filtered)
        return {"answer": f"Summed duration for {scope_label}: {total // 60}h {total % 60}m across {len(filtered)} activities. "
                           f"This is the sum of each activity's length, not elapsed calendar time.",
                "matched_ids": [it.id for it in filtered]}

    if qt == "exists":
        if filtered:
            it = filtered[0]
            return {"answer": f"Yes - \"{it.title}\" is on this schedule, {fmt_local(it.start)}, {_day_label(it.start, day_index_by_date)}.", "matched_ids": [it.id]}
        return {"answer": f"I couldn't find anything matching {scope_label}.", "matched_ids": []}

    if qt == "lookup_time":
        if filtered:
            it = filtered[0]
            return {"answer": f"{it.title} is at {fmt_local(it.start)}, {it.minutes} minutes, at {it.venue}.", "matched_ids": [it.id]}
        return {"answer": f"I couldn't find an activity matching {scope_label}.", "matched_ids": []}

    if qt == "lookup_venue":
        if spec.activity_name and filtered:
            it = filtered[0]
            return {"answer": f"{it.title} is at {it.venue}.", "matched_ids": [it.id]}
        lines = "; ".join(f"{it.title} at {fmt_local(it.start)}" for it in filtered)
        return {"answer": f"{scope_label}: {len(filtered)} activities - {lines}" if filtered else f"Nothing is scheduled at {scope_label}.",
                "matched_ids": [it.id for it in filtered]}

    if qt == "lookup_participants":
        if not filtered:
            return {"answer": f"I couldn't find an activity matching {scope_label}.", "matched_ids": []}
        it = filtered[0]
        raw = next((a for a in activities if a["_id"] == it.id), None)
        names = [s.get("userName") or s.get("userId", "someone") for s in (raw.get("stakeholders") or [])] if raw else []
        people = ", ".join(str(n) for n in names) if names else "no one recorded as a stakeholder yet"
        return {"answer": f"{it.title}: {people}.", "matched_ids": [it.id]}

    if qt == "lookup_resources":
        if not filtered:
            return {"answer": f"I couldn't find an activity matching {scope_label}.", "matched_ids": []}
        it = filtered[0]
        res = ", ".join(it.resources) if it.resources else "none recorded"
        return {"answer": f"{it.title} requires: {res}.", "matched_ids": [it.id]}

    if qt == "summary":
        if not filtered:
            return {"answer": f"I couldn't find an activity matching {scope_label}.", "matched_ids": []}
        it = filtered[0]
        return {"answer": f"{it.title}: {fmt_local(it.start)}, {it.minutes} min, at {it.venue}"
                           + (f", requires {', '.join(it.resources)}" if it.resources else "") + ".",
                "matched_ids": [it.id]}

    if qt == "list":
        pool = filtered if (spec.day_number or spec.venue or spec.activity_type_keywords or spec.activity_name) else sorted(active.values(), key=lambda it: it.start)
        lines = "; ".join(f"{it.title} ({_day_label(it.start, day_index_by_date)}, {fmt_local(it.start, with_date=False)})" for it in pool[:25])
        more = f" ...and {len(pool) - 25} more" if len(pool) > 25 else ""
        return {"answer": f"{len(pool)} activities{' on ' + scope_label if scope_desc else ''}: {lines}{more}" if pool else f"Nothing matches {scope_label}.",
                "matched_ids": [it.id for it in pool]}

    return {"answer": "I couldn't match that to a question I know how to answer directly from the schedule. "
                       "Try asking about a specific activity, a venue, a day (like 'Day 2'), or counts/totals.",
            "matched_ids": []}