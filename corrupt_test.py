#!/usr/bin/env python3
"""Corrupt a good plan in known ways and make sure check.py turns red each time.

    python corrupt_test.py plan/plan.json key.json data
"""
import copy
import json
import sys
from pathlib import Path

from check import load_ids, run


def first(items, src):
    return next(i for i in items if src in i.get("sources", []))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    key = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8-sig"))
    ids = load_ids(Path(sys.argv[3]))
    meet, rd = key["rescheduled_meeting"], key["relative_deadline"]
    nc = key["commitments"]
    explicit = next(k for k in nc if k.get("due_guess") is False)

    def c(src):
        return lambda p: next(x for x in p["commitments"] if x["source"] == src)

    cases = {
        "решённое в чате письмо в «горит»": lambda p: first(p["items"], key["resolved_in_chat"][0]["email"]).update(bucket="hot"),
        "встреча на дате первого переноса": lambda p: first(p["items"], meet["final_source"]).update(due="2026-10-14 15:00"),
        "относительный срок не переведён": lambda p: first(p["items"], rd["source"]).update(due=None),
        "рассылка попала в план": lambda p: p["items"].append({"bucket": "wait", "title": "дайджест", "sources": [key["noise"][0]]}),
        "пункт без источника": lambda p: p["items"][0].update(sources=[]),
        "выдуманный id": lambda p: p["items"][0]["sources"].append("E99"),
        "обещание потеряно": lambda p: p["commitments"].remove(c(explicit["source"])(p)),
        "явный срок помечен догадкой": lambda p: c(explicit["source"])(p).update(due_guess=True),
        "отказ посчитан обещанием": lambda p: p["commitments"].append({"source": key["not_commitments"][0], "status": "open"}),
        "расхождение с заметкой не замечено": lambda p: p.update(contradictions=[]),
    }
    caught = 0
    for name, mutate in cases.items():
        p = copy.deepcopy(plan)
        mutate(p)
        red = [n for n, ok, _ in run(p, key, ids) if not ok]
        base = [n for n, ok, _ in run(plan, key, ids) if not ok]
        new = [n for n in red if n not in base]
        caught += bool(new)
        print(f"{'поймано ' if new else 'ПРОПУЩЕНО'} {name}" + (f" → {new[0]}" if new else ""))
    print(f"\nпоймано {caught} из {len(cases)}")
    return 0 if caught == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
