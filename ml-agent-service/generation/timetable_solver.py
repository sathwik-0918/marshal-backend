import math
import re
from ortools.sat.python import cp_model
from schemas import GenerationSpec

DEFAULT_SOLVE_SECONDS = 15.0
OBJECTIVE_MODES = ["balanced", "front_load_special", "back_load_special"]

OBJECTIVE_PHRASES = {
    "balanced": "keeping each subject spread across the week",
    "front_load_special": "keeping labs and special sessions early in the week",
    "back_load_special": "keeping labs and special sessions late in the week",
    "refined": "staying close to the earlier version while following your feedback",
}

_TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "professor", "sir", "madam"}


def _faculty_key(name):
    """Canonical identity for a teacher: lowercase, titles and single-letter
    initials dropped, so 'Mrs. M. Aparna', 'Mrs.Aparna' and 'mrs aparna' are
    one person. Different first names stay distinct ('Vijay Kumar' vs
    'Kishore Kumar'). Biased conservative on purpose: over-merging only
    costs flexibility, under-merging double-books a real teacher."""
    tokens = [t for t in re.split(r"[\s.]+", (name or "").lower()) if t]
    kept = [t for t in tokens if t not in _TITLES and len(t) > 1]
    return " ".join(kept) if kept else (name or "").strip().lower()


def _venue_key(venue):
    return re.sub(r"\s+", "", (venue or "").lower())


def _build_periods(day_structure):
    seen = {}
    for t in day_structure.period_start_times:
        h, m = t.split(":")
        start = int(h) * 60 + int(m)
        seen[start] = start
    starts = sorted(seen.keys())
    periods = [(s, s + day_structure.period_length_minutes) for s in starts]

    lunch_idx = None
    max_gap = 0
    for i in range(len(periods) - 1):
        gap = periods[i + 1][0] - periods[i][1]
        if gap > max_gap:
            max_gap = gap
            lunch_idx = i
    if max_gap < 15:
        lunch_idx = None
    return periods, lunch_idx


def _valid_starts(span_k, n_periods, lunch_idx):
    starts = []
    for i in range(n_periods - span_k + 1):
        end = i + span_k - 1
        if lunch_idx is None or end <= lunch_idx or i > lunch_idx:
            starts.append(i)
    return starts


def _build_instances(spec):
    instances = []
    assumptions = []
    for req_idx, req in enumerate(spec.sessions):
        span_k = math.ceil(req.duration_minutes / spec.day_structure.period_length_minutes)
        if req.max_same_day_minutes is None and req.weekly_count > 1:
            label = f"{req.subject_name} ({req.section})" if req.section else req.subject_name
            assumptions.append(f"{label}: no daily cap stated, assumed max {req.duration_minutes * 2} min/day")
        for inst_idx in range(req.weekly_count):
            instances.append({
                "req_idx": req_idx, "inst_idx": inst_idx, "subject": req.subject_name,
                "kind": req.session_kind, "duration": req.duration_minutes, "span": span_k,
                "faculty": req.faculty, "section": req.section or "", "venue": req.venue or "",
            })
    return instances, assumptions


def _resource_capacity_check(spec, supply):
    period_len = spec.day_structure.period_length_minutes
    faculty_load, venue_load, venue_sections = {}, {}, {}
    for req in spec.sessions:
        total = math.ceil(req.duration_minutes / period_len) * req.weekly_count
        for key, name in {_faculty_key(f): f for f in req.faculty}.items():
            prev_name, prev_total = faculty_load.get(key, (name, 0))
            faculty_load[key] = (prev_name, prev_total + total)
        vkey = _venue_key(req.venue)
        if vkey:
            prev_name, prev_total = venue_load.get(vkey, (req.venue, 0))
            venue_load[vkey] = (prev_name, prev_total + total)
            venue_sections.setdefault(vkey, set()).add(req.section or "")

    problems = [f"{name} is needed for {total} periods" for name, total in faculty_load.values() if total > supply]
    problems += [
        f"{name} is needed for {total} periods"
        for vkey, (name, total) in venue_load.items()
        if len(venue_sections[vkey]) >= 2 and total > supply
    ]
    if problems:
        return {"feasible": False, "message": (
            f"A shared teacher or room is needed for more periods than exist in the week "
            f"({supply} total): {'; '.join(problems)}. Reduce the load across sections, or "
            f"assign another instructor or room."
        )}
    return {"feasible": True}


def capacity_check(spec: GenerationSpec):
    periods, lunch_idx = _build_periods(spec.day_structure)
    n_periods = len(periods)
    period_len = spec.day_structure.period_length_minutes
    if n_periods == 0 or period_len <= 0 or not spec.working_days:
        return {"feasible": False, "message": "I couldn't read the day structure - state the working days, when each period starts, and how long a period is."}
    if sum(max(0, s.weekly_count) for s in spec.sessions) == 0:
        return {"feasible": False, "message": "I couldn't find any sessions with weekly counts - state each subject with how many times a week it happens and how long it runs."}

    for s in spec.sessions:
        if s.duration_minutes <= 0:
            return {"feasible": False, "message": f"{s.subject_name} has no duration - state how long one session runs."}
        span_k = math.ceil(s.duration_minutes / period_len)
        if s.weekly_count > 0 and not _valid_starts(span_k, n_periods, lunch_idx):
            return {"feasible": False, "message": (
                f"{s.subject_name} needs {span_k} consecutive periods ({s.duration_minutes} min), but no "
                f"stretch that long exists in a day without crossing the lunch break."
            )}

    supply = len(spec.working_days) * n_periods
    demand_by_section = {}
    for s in spec.sessions:
        span_k = math.ceil(s.duration_minutes / period_len)
        key = s.section or ""
        demand_by_section[key] = demand_by_section.get(key, 0) + span_k * s.weekly_count
    for section, demand in demand_by_section.items():
        if demand > supply:
            label = f"Section {section}" if section else "This timetable"
            return {"feasible": False, "message": (
                f"{label} asks for {demand} periods of sessions across the week, but only "
                f"{supply} periods exist ({len(spec.working_days)} days x {n_periods} "
                f"periods) - {demand - supply} short. Reduce a subject's weekly count, shorten a "
                f"session, or add a working day or period."
            )}

    return _resource_capacity_check(spec, supply)


def _add_no_overlap(model, x, instances, starts_by_span, idx_list, n_days, n_periods):
    for d in range(n_days):
        for p in range(n_periods):
            occupying = [
                x[i, d, start] for i in idx_list
                for start in starts_by_span[instances[i]["span"]] if start <= p < start + instances[i]["span"]
            ]
            if len(occupying) > 1:
                model.add(sum(occupying) <= 1)


def _build_core(spec, instances, starts_by_span, n_periods, n_days, forbidden=None, relax=frozenset()):
    """The hard rules only - shared by generation, feedback refinement and
    infeasibility diagnosis, so none of them can drift. relax lets the
    diagnosis switch one rule family off to see which one is the blocker."""
    model = cp_model.CpModel()
    x = {}
    for i, inst in enumerate(instances):
        for d in range(n_days):
            for start in starts_by_span[inst["span"]]:
                x[i, d, start] = model.new_bool_var(f"x_{i}_{d}_{start}")

    for i, inst in enumerate(instances):
        model.add_exactly_one(x[i, d, start] for d in range(n_days) for start in starts_by_span[inst["span"]])

    by_section = {}
    for i, inst in enumerate(instances):
        by_section.setdefault(inst["section"], []).append(i)
    for idx_list in by_section.values():
        _add_no_overlap(model, x, instances, starts_by_span, idx_list, n_days, n_periods)

    if "faculty" not in relax:
        faculty_map = {}
        for i, inst in enumerate(instances):
            for key in {_faculty_key(f) for f in inst["faculty"]}:
                faculty_map.setdefault(key, []).append(i)
        for idx_list in faculty_map.values():
            if len(idx_list) > 1:
                _add_no_overlap(model, x, instances, starts_by_span, idx_list, n_days, n_periods)

    if "venue" not in relax:
        venue_map = {}
        for i, inst in enumerate(instances):
            vkey = _venue_key(inst["venue"])
            if vkey:
                venue_map.setdefault(vkey, []).append(i)
        for idx_list in venue_map.values():
            # A room used by only one section is already covered by that
            # section's own occupancy rule - only cross-section sharing matters.
            if len({instances[i]["section"] for i in idx_list}) >= 2:
                _add_no_overlap(model, x, instances, starts_by_span, idx_list, n_days, n_periods)

    by_req = {}
    for i, inst in enumerate(instances):
        by_req.setdefault(inst["req_idx"], []).append(i)
    if "daily_cap" not in relax:
        for req_idx, req in enumerate(spec.sessions):
            cap = req.max_same_day_minutes if req.max_same_day_minutes is not None else req.duration_minutes * 2
            idx_list = by_req.get(req_idx, [])
            if len(idx_list) < 2:
                continue
            for d in range(n_days):
                terms = [x[i, d, start] * instances[i]["duration"] for i in idx_list for start in starts_by_span[instances[i]["span"]]]
                if terms:
                    model.add(sum(terms) <= cap)

    if forbidden:
        for key in forbidden:
            if key in x:
                model.add(x[key] == 0)

    return model, x, by_req


def _balance_terms(model, x, instances, by_req, starts_by_span, n_days):
    terms = []
    for req_idx, idx_list in by_req.items():
        if len(idx_list) < 2:
            continue
        for d in range(n_days):
            count_terms = [x[i, d, start] for i in idx_list for start in starts_by_span[instances[i]["span"]]]
            if not count_terms:
                continue
            excess = model.new_int_var(0, len(idx_list), f"excess_{req_idx}_{d}")
            model.add(excess >= sum(count_terms) - 1)
            terms.append(excess)
    return terms


def _style_objective(model, x, instances, by_req, starts_by_span, n_days, objective_mode):
    balance = _balance_terms(model, x, instances, by_req, starts_by_span, n_days)
    if objective_mode == "balanced":
        return sum(balance) if balance else None

    sign = 1 if objective_mode == "front_load_special" else -1
    special = []
    for i, inst in enumerate(instances):
        if inst["kind"] not in ("special", "lab"):
            continue
        for d in range(n_days):
            for start in starts_by_span[inst["span"]]:
                special.append(x[i, d, start] * sign * d)
    parts = []
    if special:
        parts.append(10 * sum(special))   # where the labs go is this option's identity
    if balance:
        parts.append(sum(balance))        # light spread so the rest isn't piled up
    return sum(parts) if parts else None


def _to_reference_entries(spec, assignment):
    periods, _ = _build_periods(spec.day_structure)
    entries = []
    for a in assignment:
        start_min, _ = periods[a["start_period"]]
        end_min = periods[a["start_period"] + a["span"] - 1][1]
        metadata = []
        if a.get("section"):
            metadata.append(f"section: {a['section']}")
        if a["faculty"]:
            metadata.append(f"faculty: {', '.join(a['faculty'])}")
        entries.append({
            "title": a["subject"], "entry_type": "recurring_weekly",
            "weekday": spec.working_days[a["day"]],
            "start_time": f"{start_min // 60:02d}:{start_min % 60:02d}",
            "end_time": f"{end_min // 60:02d}:{end_min % 60:02d}",
            "venue": a.get("venue", ""),
            "metadata": metadata,
        })
    return entries


def _extract_assignment(spec, instances, starts_by_span, x, solver, n_days):
    assignment = []
    for i, inst in enumerate(instances):
        for d in range(n_days):
            for start in starts_by_span[inst["span"]]:
                if (i, d, start) in x and solver.value(x[i, d, start]):
                    assignment.append({**inst, "day": d, "start_period": start})
    return assignment


def solve_once(spec, objective_mode="balanced"):
    periods, lunch_idx = _build_periods(spec.day_structure)
    n_periods, n_days = len(periods), len(spec.working_days)
    instances, assumptions = _build_instances(spec)
    starts_by_span = {k: _valid_starts(k, n_periods, lunch_idx) for k in {inst["span"] for inst in instances}}

    model, x, by_req = _build_core(spec, instances, starts_by_span, n_periods, n_days)
    objective = _style_objective(model, x, instances, by_req, starts_by_span, n_days, objective_mode)
    if objective is not None:
        model.minimize(objective)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = DEFAULT_SOLVE_SECONDS
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, assumptions, status
    return _extract_assignment(spec, instances, starts_by_span, x, solver, n_days), assumptions, status


def diagnose_infeasibility(spec):
    """Re-solves with one rule family switched off at a time, using the SAME
    core model as real generation. Whichever relaxation makes it solvable
    is the real bottleneck - measured, not guessed."""
    periods, lunch_idx = _build_periods(spec.day_structure)
    n_periods, n_days = len(periods), len(spec.working_days)
    instances, _ = _build_instances(spec)
    starts_by_span = {k: _valid_starts(k, n_periods, lunch_idx) for k in {inst["span"] for inst in instances}}

    def solvable(relax):
        model, _, _ = _build_core(spec, instances, starts_by_span, n_periods, n_days, relax=frozenset(relax))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 8.0
        return solver.solve(model) in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    if solvable({"daily_cap"}):
        return "The same-subject daily limit is the blocker - a subject needs more time on some days than the limit allows. Raise its daily limit or reduce its weekly count."
    if solvable({"venue"}):
        return "A shared room or lab is the blocker - sections need the same room at the same time more often than it can serve. Give a section another room, or reduce how often it's used."
    if solvable({"faculty"}):
        return "A shared teacher is the blocker - the same person is needed in more places at once than the week has room for, even though the raw period count fits."
    if solvable({"daily_cap", "venue", "faculty"}):
        return "No single rule is the blocker - the daily limit, shared rooms and shared teachers together leave no room. Relaxing any one of them alone isn't enough."
    return "Even without daily limits, shared rooms or shared teachers, these sessions don't fit together - check the multi-period sessions (labs, research) against the lunch break and the periods per day."


def generate_options(spec: GenerationSpec):
    check = capacity_check(spec)
    if not check["feasible"]:
        return {"feasible": False, "message": check["message"], "options": [], "assumptions": []}

    options, assumptions, last_status = [], [], None
    for mode in OBJECTIVE_MODES:
        assignment, mode_assumptions, status = solve_once(spec, objective_mode=mode)
        assumptions, last_status = mode_assumptions, status
        if assignment is None:
            if status == cp_model.INFEASIBLE:
                break  # the hard rules are identical in every mode - none of the others can succeed either
            continue
        signature = frozenset((a["req_idx"], a["inst_idx"], a["day"], a["start_period"]) for a in assignment)
        if any(signature == o["_signature"] for o in options):
            continue
        options.append({"mode": mode, "reference_entries": _to_reference_entries(spec, assignment), "_signature": signature})
    for o in options:
        del o["_signature"]

    if not options:
        diagnosis = diagnose_infeasibility(spec)
        prefix = "No valid arrangement exists." if last_status == cp_model.INFEASIBLE else "Couldn't find an arrangement within the time limit."
        return {"feasible": False, "message": f"{prefix} {diagnosis}", "options": [], "assumptions": assumptions}
    return {"feasible": True, "message": "", "options": options, "assumptions": assumptions}


def _hhmm_to_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _parse_entry_metadata(entry):
    faculty, section = [], ""
    for m in entry.get("metadata", []):
        low = m.lower()
        if low.startswith("faculty:"):
            faculty = [f.strip() for f in m.split(":", 1)[1].split(",") if f.strip()]
        elif low.startswith("section:"):
            section = m.split(":", 1)[1].strip()
    return faculty, section


def _entry_section(entry):
    return _parse_entry_metadata(entry)[1]


def explain_placement(spec, reference_entries, target_entry, objective_mode="balanced"):
    """
    Replays the same hard rules against every EARLIER slot this session
    could have taken, given where everything else actually sits, and
    reports how many were blocked by what. Slots already used by this
    section's other sessions are the trivial case; the informative ones
    are free slots ruled out by a shared teacher, a shared room, or the
    daily limit. Anything still open is reported as an objective-driven
    choice, never as a made-up technical reason.
    """
    periods, lunch_idx = _build_periods(spec.day_structure)
    n_periods = len(periods)
    period_len = spec.day_structure.period_length_minutes
    day_to_idx = {name: i for i, name in enumerate(spec.working_days)}
    start_to_idx = {f"{s // 60:02d}:{s % 60:02d}": i for i, (s, _) in enumerate(periods)}

    def to_coords(entry):
        d = day_to_idx.get(entry.get("weekday"))
        s = start_to_idx.get(entry.get("start_time"))
        if d is None or s is None or not entry.get("end_time"):
            return None
        span_k = max(1, round((_hhmm_to_min(entry["end_time"]) - _hhmm_to_min(entry["start_time"])) / period_len))
        return d, s, span_k

    target = to_coords(target_entry)
    if not target:
        return "Couldn't locate this session's exact slot to explain it."
    t_day, t_start, t_span = target
    t_faculty, t_section = _parse_entry_metadata(target_entry)
    t_fac_by_key = {_faculty_key(f): f for f in t_faculty}
    t_venue_key = _venue_key(target_entry.get("venue", ""))
    t_subject = target_entry["title"]

    others = []
    for e in reference_entries:
        # The session being explained must not block its own earlier candidates.
        if (e.get("title") == t_subject and e.get("weekday") == target_entry.get("weekday")
                and e.get("start_time") == target_entry.get("start_time") and _entry_section(e) == t_section):
            continue
        c = to_coords(e)
        if not c:
            continue
        f, sec = _parse_entry_metadata(e)
        others.append({
            "title": e["title"], "day": c[0], "start": c[1], "span": c[2],
            "fac_keys": {_faculty_key(x) for x in f}, "section": sec,
            "venue_key": _venue_key(e.get("venue", "")), "venue_name": e.get("venue", ""),
        })

    req = next((r for r in spec.sessions if r.subject_name == t_subject and (r.section or "") == t_section), None)
    cap = None
    if req is not None:
        cap = req.max_same_day_minutes if req.max_same_day_minutes is not None else req.duration_minutes * 2

    valid_starts = sorted(_valid_starts(t_span, n_periods, lunch_idx))
    earlier = [(d, s) for d in range(len(spec.working_days)) for s in valid_starts if (d, s) < (t_day, t_start)]
    where = f"{spec.working_days[t_day]} at {target_entry['start_time']}"
    if not earlier:
        return f"{t_subject} is already at the earliest slot where a session this long can fit ({where}) - nothing earlier exists to consider."

    occupied = cap_blocked = free = 0
    fac_counts, ven_counts = {}, {}
    for d, s in earlier:
        overlapping = [o for o in others if o["day"] == d and not (s + t_span <= o["start"] or o["start"] + o["span"] <= s)]
        if any(o["section"] == t_section for o in overlapping):
            occupied += 1
            continue
        shared_fac = None
        for o in overlapping:
            common = o["fac_keys"] & set(t_fac_by_key)
            if common:
                shared_fac = t_fac_by_key[next(iter(common))]
                break
        if shared_fac:
            fac_counts[shared_fac] = fac_counts.get(shared_fac, 0) + 1
            continue
        shared_venue = next((o["venue_name"] for o in overlapping if t_venue_key and o["venue_key"] == t_venue_key), None)
        if shared_venue:
            ven_counts[shared_venue] = ven_counts.get(shared_venue, 0) + 1
            continue
        if cap is not None and req is not None:
            same_day = sum(1 for o in others if o["title"] == t_subject and o["section"] == t_section and o["day"] == d)
            if (same_day + 1) * req.duration_minutes > cap:
                cap_blocked += 1
                continue
        free += 1

    pieces = []
    if occupied:
        pieces.append(f"{occupied} were already used by this section's other sessions")
    for name, c in fac_counts.items():
        pieces.append(f"{c} were blocked because {name} already teaches another section's class then")
    for name, c in ven_counts.items():
        pieces.append(f"{c} were blocked because {name} was already in use by another section")
    if cap_blocked:
        pieces.append(f"{cap_blocked} would have put too much {t_subject} on one day")
    if free:
        goal = OBJECTIVE_PHRASES.get(objective_mode)
        if goal:
            pieces.append(f"{free} were still open - this option is aimed at {goal}, so the solver preferred a spot that serves that goal")
        else:
            pieces.append(f"{free} were still open - the solver's overall objective preferred this spot")
    return f"{t_subject} is on {where}. Of the {len(earlier)} earlier slots it could have taken: " + "; ".join(pieces) + "."


def _previous_slots_by_subject(spec, reference_entries, periods):
    start_to_idx = {f"{s // 60:02d}:{s % 60:02d}": i for i, (s, _) in enumerate(periods)}
    day_to_idx = {name: i for i, name in enumerate(spec.working_days)}
    result = {}
    for req_idx, req in enumerate(spec.sessions):
        slots = set()
        for entry in reference_entries:
            if entry.get("title") != req.subject_name:
                continue
            if (req.section or "") != _entry_section(entry):
                continue
            d, p = day_to_idx.get(entry.get("weekday")), start_to_idx.get(entry.get("start_time"))
            if d is not None and p is not None:
                slots.add((d, p))
        result[req_idx] = slots
    return result


def refine_with_feedback(spec, rejected_reference_entries, feedback_constraints, objective_mode="balanced"):
    periods, lunch_idx = _build_periods(spec.day_structure)
    n_periods, n_days = len(periods), len(spec.working_days)
    day_to_idx = {name: i for i, name in enumerate(spec.working_days)}
    previous_slots = _previous_slots_by_subject(spec, rejected_reference_entries, periods)

    instances, assumptions = _build_instances(spec)
    starts_by_span = {k: _valid_starts(k, n_periods, lunch_idx) for k in {inst["span"] for inst in instances}}
    midpoint = lunch_idx if lunch_idx is not None else max(0, n_periods // 2 - 1)

    def matches(inst, fc):
        return inst["subject"] in fc.subjects and (not fc.section or inst["section"] == fc.section)

    forbidden = set()
    soft_forbidden = []
    unresolved = []

    def mark(key, hard):
        if hard:
            forbidden.add(key)
        else:
            soft_forbidden.append(key)

    for fc in feedback_constraints:
        if fc.constraint_type in ("avoid_day", "require_day"):
            d = day_to_idx.get(fc.day) if fc.day else None
            if d is None:
                unresolved.append(f"Say which day for: {', '.join(fc.subjects) or 'that request'}")
                continue
            for i, inst in enumerate(instances):
                if not matches(inst, fc):
                    continue
                for start in starts_by_span[inst["span"]]:
                    if fc.constraint_type == "avoid_day":
                        mark((i, d, start), fc.is_hard)
                    else:
                        for other_d in range(n_days):
                            if other_d != d:
                                mark((i, other_d, start), fc.is_hard)

        elif fc.constraint_type == "faculty_unavailable":
            target_keys = {_faculty_key(f) for f in fc.faculty if f}
            if not target_keys:
                unresolved.append("Couldn't tell which teacher that applies to")
                continue
            if not fc.day and not fc.part_of_day:
                unresolved.append("Say which day or part of the day the teacher is unavailable")
                continue
            d_only = day_to_idx.get(fc.day) if fc.day else None
            if fc.day and d_only is None:
                unresolved.append(f"Didn't recognize day '{fc.day}'")
                continue
            days_hit = [d_only] if d_only is not None else list(range(n_days))
            hit_any = False
            for i, inst in enumerate(instances):
                if not ({_faculty_key(f) for f in inst["faculty"]} & target_keys):
                    continue
                hit_any = True
                for d in days_hit:
                    for start in starts_by_span[inst["span"]]:
                        end = start + inst["span"] - 1
                        if fc.part_of_day == "morning" and end > midpoint:
                            continue
                        if fc.part_of_day == "afternoon" and start <= midpoint:
                            continue
                        mark((i, d, start), fc.is_hard)
            if not hit_any:
                unresolved.append(f"No sessions are assigned to {', '.join(fc.faculty)} in this timetable")

    model, x, by_req = _build_core(spec, instances, starts_by_span, n_periods, n_days, forbidden)

    for fc in feedback_constraints:
        if fc.constraint_type != "separate_days" or len(fc.subjects) < 2:
            continue
        if not fc.is_hard:
            unresolved.append("Different-days as a mere preference isn't supported - say 'don't' or 'never' to make it firm")
            continue
        groups = [[i for i, inst in enumerate(instances) if inst["subject"] == s and (not fc.section or inst["section"] == fc.section)] for s in fc.subjects]
        groups = [g for g in groups if g]
        if len(groups) < 2:
            continue
        for d in range(n_days):
            any_vars = []
            for g_idx, group in enumerate(groups):
                vs = [x[i, d, start] for i in group for start in starts_by_span[instances[i]["span"]] if (i, d, start) in x]
                if not vs:
                    continue
                any_var = model.new_bool_var(f"sep_{d}_{g_idx}")
                for v in vs:
                    model.add(any_var >= v)
                model.add(any_var <= sum(vs))
                any_vars.append(any_var)
            if len(any_vars) >= 2:
                model.add(sum(any_vars) <= 1)

    disruption_terms = []
    for i, inst in enumerate(instances):
        prev = previous_slots.get(inst["req_idx"], set())
        for d in range(n_days):
            for start in starts_by_span[inst["span"]]:
                if (i, d, start) in x and (d, start) not in prev:
                    disruption_terms.append(x[i, d, start])

    preference_terms = []
    for fc in feedback_constraints:
        if fc.constraint_type == "time_preference" and fc.time_preference:
            weight = 1 if fc.time_preference == "morning" else -1
            for i, inst in enumerate(instances):
                if not matches(inst, fc):
                    continue
                for d in range(n_days):
                    for start in starts_by_span[inst["span"]]:
                        if (i, d, start) in x:
                            preference_terms.append(x[i, d, start] * weight * start)
        elif fc.constraint_type == "unsupported":
            unresolved.append(f"Couldn't apply: feedback about {', '.join(fc.subjects) or 'something unspecified'}")
    preference_terms += [x[key] * 5 for key in soft_forbidden if key in x]

    style_expr = _style_objective(model, x, instances, by_req, starts_by_span, n_days, objective_mode)
    objective = sum(disruption_terms) * 100 + sum(preference_terms) * 5
    if style_expr is not None:
        objective = objective + style_expr
    model.minimize(objective)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = DEFAULT_SOLVE_SECONDS
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"feasible": False, "message": "Couldn't satisfy that together with the existing requirements - try relaxing one part of it.", "reference_entries": [], "changed_subjects": [], "unresolved_feedback": unresolved, "assumptions": assumptions}

    assignment = _extract_assignment(spec, instances, starts_by_span, x, solver, n_days)
    changed = sorted({a["subject"] for a in assignment if (a["day"], a["start_period"]) not in previous_slots.get(a["req_idx"], set())})
    return {"feasible": True, "message": "", "reference_entries": _to_reference_entries(spec, assignment), "changed_subjects": changed, "unresolved_feedback": unresolved, "assumptions": assumptions}

def section_summary(spec):
    periods, _ = _build_periods(spec.day_structure)
    supply = len(spec.working_days) * len(periods)
    period_len = spec.day_structure.period_length_minutes or 50
    by_section = {}
    for s in spec.sessions:
        entry = by_section.setdefault(s.section or "", {"section": s.section or "", "subjects": 0, "sessions": 0, "periods": 0, "supply": supply})
        entry["subjects"] += 1
        entry["sessions"] += s.weekly_count
        entry["periods"] += math.ceil(s.duration_minutes / period_len) * s.weekly_count
    return sorted(by_section.values(), key=lambda e: e["section"])


def fill_notes(summary):
    notes = []
    if len(summary) < 2:
        return notes
    fullest = max(s["periods"] for s in summary)
    for s in summary:
        if s["supply"] and s["periods"] < 0.6 * s["supply"] and fullest >= 0.8 * s["supply"]:
            label = f"Section {s['section']}" if s["section"] else "One group"
            notes.append(f"{label} only fills {s['periods']} of {s['supply']} periods while another section fills "
                         f"{fullest} - if that isn't intended, something may be missing from your text for it.")
    return notes