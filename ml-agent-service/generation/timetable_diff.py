from collections import defaultdict

_DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _section(entry):
    for m in entry.get("metadata", []) or []:
        if m.lower().startswith("section:"):
            return m.split(":", 1)[1].strip()
    return ""


def _to_min(hhmm):
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


def _day_idx(name):
    return _DAY_ORDER.index(name) if name in _DAY_ORDER else 99


def diff_entries(old_entries, new_entries):
    """
    old_entries: database-shaped (_id, weekday, startTime, endTime, title, metadata).
    new_entries: solver-shaped (weekday, start_time, end_time, title, metadata).
    Sessions of the same subject and section are interchangeable, so unchanged
    slots are matched first and only the leftovers are paired (nearest day, then
    nearest time) into moves - the smallest honest description of what changed.
    """
    old_by, new_by = defaultdict(list), defaultdict(list)
    for e in old_entries:
        if e.get("weekday") and e.get("startTime") and e.get("endTime"):
            old_by[(e["title"], _section(e))].append(e)
    for e in new_entries:
        new_by[(e["title"], _section(e))].append(e)

    moves = []
    for key, olds in old_by.items():
        news = list(new_by.get(key, []))
        leftovers = []
        for o in olds:
            idx = next((i for i, n in enumerate(news) if n["weekday"] == o["weekday"] and n["start_time"] == o["startTime"]), None)
            if idx is None:
                leftovers.append(o)
            else:
                news.pop(idx)
        for o in sorted(leftovers, key=lambda e: (_day_idx(e["weekday"]), _to_min(e["startTime"]))):
            if not news:
                break
            best = min(range(len(news)), key=lambda i: (
                abs(_day_idx(news[i]["weekday"]) - _day_idx(o["weekday"])),
                abs(_to_min(news[i]["start_time"]) - _to_min(o["startTime"])),
            ))
            n = news.pop(best)
            moves.append({
                "entry_id": str(o["_id"]), "title": o["title"], "section": key[1],
                "old": {"weekday": o["weekday"], "start": o["startTime"], "end": o["endTime"]},
                "new": {"weekday": n["weekday"], "start": n["start_time"], "end": n["end_time"]},
            })
    return moves