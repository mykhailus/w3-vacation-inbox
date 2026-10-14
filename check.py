#!/usr/bin/env python3
"""Check a plan.json against an answer key. All expectations live in the key file.

    python check.py plan/plan.json key.json data

Key fields (all optional, a missing field is skipped):
  resolved_in_chat     [{email, chat}]        – email must not be in "hot"
  rescheduled_meeting  {final_source, date}   – an item cites final_source and due starts with date
  relative_deadline    {source, date}         – an item cites source and due starts with date
  noise                [id]                   – never cited anywhere in the plan
  commitments          [{source, due?, due_guess?, status?}]
  not_commitments      [id]                   – never listed as a commitment
  contradiction        {note, source}         – a contradiction cites both
Always: every item has at least one source, every cited id exists in data.
"""
import json
import sys
from pathlib import Path


def load_ids(data: Path) -> set[str]:
    ids = {"notes.md"}
    for name in ("inbox.jsonl", "chat.jsonl", "sent.jsonl"):
        p = data / name
        if p.exists():
            ids |= {json.loads(l)["id"] for l in p.read_text(encoding="utf-8-sig").splitlines() if l.strip()}
    return ids


def cited(plan: dict) -> list[str]:
    out = [s for it in plan.get("items", []) for s in it.get("sources", [])]
    out += [c.get("source") for c in plan.get("commitments", [])]
    out += [s for c in plan.get("commitments", []) for s in c.get("evidence", [])]
    out += [s for c in plan.get("contradictions", []) for s in c.get("sources", [])]
    return [s for s in out if s]


def run(plan: dict, key: dict, ids: set[str]) -> list[tuple[str, bool, str]]:
    items = plan.get("items", [])
    res = []

    def add(name, ok, detail):
        res.append((name, bool(ok), detail))

    for r in key.get("resolved_in_chat", []):
        hot = [i["title"] for i in items if i.get("bucket") == "hot" and r["email"] in i.get("sources", [])]
        add(f"1. {r['email']} (решено в {r['chat']}) не в «горит»", not hot, f"в hot: {hot}" if hot else "ок")

    m = key.get("rescheduled_meeting")
    if m:
        hit = [i for i in items if m["final_source"] in i.get("sources", [])]
        ok = any(str(i.get("due") or "").startswith(m["date"]) for i in hit)
        add(f"2. встреча стоит на {m['date']} со ссылкой на {m['final_source']}", ok,
            "; ".join(f"{i.get('title')} → {i.get('due')}" for i in hit) or "нет пункта с этим источником")

    rd = key.get("relative_deadline")
    if rd:
        hit = [i for i in items if rd["source"] in i.get("sources", [])]
        ok = any(str(i.get("due") or "").startswith(rd["date"]) for i in hit)
        add(f"3. «{rd.get('phrase', 'срок')}» из {rd['source']} → {rd['date']}", ok,
            "; ".join(f"{i.get('title')} → {i.get('due')}" for i in hit) or "нет пункта с этим источником")

    if "noise" in key:
        leak = sorted(set(cited(plan)) & set(key["noise"]))
        add("4. рассылки и автоуведомления не в плане", not leak, f"попали: {leak}" if leak else "ок")

    nosrc = [i.get("title") for i in items if not i.get("sources")]
    nosrc += [c.get("what") for c in plan.get("commitments", []) if not c.get("source")]
    bad = sorted(set(cited(plan)) - ids)
    add("5. у каждого пункта есть источник, все id существуют", not nosrc and not bad,
        f"без источника: {nosrc}; несуществующие id: {bad}" if nosrc or bad else "ок")

    got = {c.get("source"): c for c in plan.get("commitments", [])}
    for k in key.get("commitments", []):
        c = got.get(k["source"])
        if k.get("status") == "done":
            ok = c is None or c.get("status") == "done"
            add(f"6. {k['source']} выполнено командой – не долг", ok, f"статус {c.get('status')}" if c else "не в списке")
            continue
        if c is None:
            add(f"6. обещание {k['source']} найдено", False, "нет в commitments")
            continue
        probs = []
        if "due" in k and not str(c.get("due") or "").startswith(k["due"]):
            probs.append(f"срок {c.get('due')} вместо {k['due']}")
        if "due_guess" in k and bool(c.get("due_guess")) != k["due_guess"]:
            probs.append(f"due_guess {c.get('due_guess')} вместо {k['due_guess']}")
        add(f"6. обещание {k['source']}: {c.get('to')} – {c.get('due')}", not probs, "; ".join(probs) or "ок")

    extra = sorted(set(got) & set(key.get("not_commitments", [])))
    if "not_commitments" in key:
        add("7. письма без обещаний не считаются обещаниями", not extra, f"лишние: {extra}" if extra else "ок")

    ct = key.get("contradiction")
    if ct:
        ok = any(c.get("note", "notes.md") == ct["note"] and ct["source"] in c.get("sources", [])
                 for c in plan.get("contradictions", []))
        add(f"8. расхождение {ct['note']} ↔ {ct['source']} замечено", ok,
            "; ".join(c.get("claim", "") for c in plan.get("contradictions", [])) or "расхождений нет")
    return res


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    key = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8-sig"))
    res = run(plan, key, load_ids(Path(sys.argv[3])))
    for name, ok, detail in res:
        print(f"{'OK  ' if ok else 'FAIL'} {name} – {detail}")
    fails = sum(not ok for _, ok, _ in res)
    print(f"\nитого: {len(res) - fails} из {len(res)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
