#!/usr/bin/env python3
"""Build a first-day plan from a mailbox folder with an agent (Gemini via Antigravity CLI `agy`).

    python build.py <data_dir> [--out <dir>] [--today YYYY-MM-DD] [--owner "Имя"] [--model "Gemini 3.8 Flash (High)"]

<data_dir> must contain inbox.jsonl and chat.jsonl; sent.jsonl and notes.md are optional.
Writes <out>/plan.json (raw agent answer, validated) and <out>/plan.md (table with links).
Nothing about a particular mailbox lives in this file: today and owner are inferred from the data.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGY = os.environ.get("AGY", shutil.which("agy") or r"C:\Users\Administrator\AppData\Local\agy\bin\agy.exe")
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
BUCKETS = ["hot", "overdue", "wait", "resolved", "cancelled"]
TITLES = {"hot": "Горит сегодня", "overdue": "Просрочено – написать", "wait": "Может подождать",
          "resolved": "Решилось без вас", "cancelled": "Решение отменили или изменили"}


class InputError(Exception):
    pass


def read_jsonl(path: Path, required: set[str]) -> list[dict]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise InputError(f"{path.name}, строка {n}: не JSON ({e.msg})")
        missing = required - row.keys()
        if missing:
            raise InputError(f"{path.name}, строка {n}: нет полей {sorted(missing)}; ожидаются {sorted(required)}")
        rows.append(row)
    return rows


def load(data_dir: Path) -> dict:
    if not (data_dir / "inbox.jsonl").exists() or not (data_dir / "chat.jsonl").exists():
        raise InputError(f"в {data_dir} нужны inbox.jsonl и chat.jsonl (одна строка JSON на сообщение)")
    mail = {"id", "at", "from", "to", "subject", "body"}
    d = {"inbox": read_jsonl(data_dir / "inbox.jsonl", mail),
         "chat": read_jsonl(data_dir / "chat.jsonl", {"id", "at", "name", "text"}),
         "sent": read_jsonl(data_dir / "sent.jsonl", mail) if (data_dir / "sent.jsonl").exists() else []}
    d["ids"] = {r["id"] for k in ("inbox", "chat", "sent") for r in d[k]} | {"notes.md"}
    return d


def infer_today(d: dict) -> date:
    """First working day after the latest message."""
    last = max(datetime.fromisoformat(r["at"]).date() for k in ("inbox", "chat") for r in d[k])
    day = last + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def infer_owner(d: dict) -> str:
    if d["sent"]:
        return Counter(r["from"] for r in d["sent"]).most_common(1)[0][0]
    return Counter(r["to"] for r in d["inbox"]).most_common(1)[0][0]


def data_block(data_dir: Path) -> str:
    parts = []
    for name in ("inbox.jsonl", "chat.jsonl", "sent.jsonl", "notes.md"):
        p = data_dir / name
        if p.exists():
            parts.append(f"===== {name} =====\n{p.read_text(encoding='utf-8-sig').strip()}\n")
    return "\n".join(parts)


def ask_agent(prompt: str, model: str) -> str:
    # The prompt goes through stdin: it is too long for a Windows command line,
    # and the agent needs no tools, so headless permission prompts never fire.
    r = subprocess.run([AGY, "--model", model], input=prompt,
                       capture_output=True, text=True, encoding="utf-8", timeout=900)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"agy вернул {r.returncode}, пустой ответ или ошибка: {(r.stderr or r.stdout).strip()[:500]}")
    return r.stdout


def extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("в ответе агента нет JSON")
    return json.loads(text[start:end + 1])


def unknown_ids(plan: dict, ids: set[str]) -> list[str]:
    used = [s for it in plan.get("items", []) for s in it.get("sources", [])]
    used += [c.get("source") for c in plan.get("commitments", [])]
    used += [s for c in plan.get("commitments", []) for s in c.get("evidence", [])]
    used += [s for c in plan.get("contradictions", []) for s in c.get("sources", [])]
    return sorted({s for s in used if s and s not in ids})


def link(src: str, data_rel: str) -> str:
    if src == "notes.md":
        return f"[notes.md]({data_rel}/notes.md)"
    if src[0] == "C":
        return f"[{src}]({data_rel}/chat.md)"
    folder = "sent" if src[0] == "S" else "inbox"
    return f"[{src}]({data_rel}/{folder}/{src}.md)"


def cell(v) -> str:
    return "–" if v in (None, "", []) else str(v).replace("|", "/").replace("\n", " ")


def render(plan: dict, data_rel: str) -> str:
    out = [f"# План на {plan.get('today')} – {plan.get('owner')}", ""]
    for b in BUCKETS:
        items = [i for i in plan.get("items", []) if i.get("bucket") == b]
        if not items:
            continue
        out += [f"## {TITLES[b]} ({len(items)})", "", "| что | срок | кому | черновик | источники |", "|---|---|---|---|---|"]
        for i in items:
            due = cell(i.get("due")) + (" (догадка)" if i.get("due_guess") else "")
            out.append(f"| {cell(i.get('title'))} | {due} | {cell(i.get('write_to'))} | {cell(i.get('draft'))} | "
                       + ", ".join(link(s, data_rel) for s in i.get("sources", [])) + " |")
        out.append("")
    if plan.get("commitments"):
        out += ["## Мои обещания до отпуска", "", "| кому | что | срок | статус | черновик | источник |", "|---|---|---|---|---|---|"]
        for c in plan["commitments"]:
            due = cell(c.get("due")) + (f" (догадка: {cell(c.get('due_note'))})" if c.get("due_guess") else "")
            srcs = [c.get("source")] + list(c.get("evidence", []))
            out.append(f"| {cell(c.get('to'))} | {cell(c.get('what'))} | {due} | {cell(c.get('status'))} | "
                       f"{cell(c.get('draft'))} | " + ", ".join(link(s, data_rel) for s in srcs if s) + " |")
        out.append("")
    if plan.get("contradictions"):
        out += ["## Заметка расходится с фактами", ""]
        for c in plan["contradictions"]:
            out.append(f"- В заметке: «{cell(c.get('claim'))}». На деле: {cell(c.get('fact'))} – "
                       + ", ".join(link(s, data_rel) for s in [c.get("note", "notes.md")] + c.get("sources", [])))
        out.append("")
    ign = plan.get("ignored", [])
    if ign:
        out += [f"## Не требует действий ({len(ign)})", "",
                ", ".join(f"{link(i['source'], data_rel)} – {cell(i.get('why'))}" for i in ign if i.get("source")), ""]
    return "\n".join(out)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--today")
    ap.add_argument("--owner")
    ap.add_argument("--model", default=os.environ.get("PLAN_MODEL", "Gemini 3.8 Flash (High)"))
    ap.add_argument("--render-only", action="store_true", help="re-render plan.md from existing plan.json")
    a = ap.parse_args()
    data_dir = a.data_dir.resolve()
    out = (a.out or data_dir.parent / "plan").resolve()
    try:
        d = load(data_dir)
    except InputError as e:
        print(f"ошибка входа: {e}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    if a.render_only:
        plan = json.loads((out / "plan.json").read_text(encoding="utf-8-sig"))
    else:
        today = date.fromisoformat(a.today) if a.today else infer_today(d)
        owner = a.owner or infer_owner(d)
        prompt = (HERE / "prompt.md").read_text(encoding="utf-8-sig")
        for k, v in {"OWNER": owner, "TODAY": today.isoformat(), "WEEKDAY": WEEKDAYS[today.weekday()],
                     "DATA": data_block(data_dir)}.items():
            prompt = prompt.replace("{" + k + "}", v)
        raw = ask_agent(prompt, a.model)
        (out / "agent-raw.txt").write_text(raw, encoding="utf-8")
        plan = extract_json(raw)
        plan["_run"] = {"model": a.model, "today_inferred": not a.today, "owner_inferred": not a.owner,
                        "at": datetime.now().isoformat(timespec="seconds")}
        (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    bad = unknown_ids(plan, d["ids"])
    data_rel = os.path.relpath(data_dir, out).replace("\\", "/")
    (out / "plan.md").write_text(render(plan, data_rel), encoding="utf-8")
    n = Counter(i.get("bucket") for i in plan.get("items", []))
    print(f"план: {out / 'plan.md'}; пункты {dict(n)}, обещаний {len(plan.get('commitments', []))}, "
          f"расхождений {len(plan.get('contradictions', []))}, пропущено {len(plan.get('ignored', []))}")
    if bad:
        print(f"ВНИМАНИЕ: агент сослался на несуществующие id: {bad}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
