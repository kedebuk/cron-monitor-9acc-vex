#!/usr/bin/env python3
"""Sync 9acc Vex LaunchAgent status into data/cron.json.

Read-only against LaunchAgents/log files. No Meta API/token access.
Use --push to commit/push changed dashboard data to GitHub Pages.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import plistlib
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent
DATA = REPO / "data" / "cron.json"
LAUNCH_DIR = Path("/Users/kantor/Library/LaunchAgents")
ACCOUNTS_JSON = Path("/Users/kantor/.hermes/legacy/openclaw/workspace/agents/meta-ads/the9acc.json")

JOBS = {
    "com.vex.9acc-ads-campaign-guard": ("9acc guard: pause campaign jika spend tinggi tanpa IC/purchase", "Guard / kill-switch"),
    "com.vex.9acc-meta-scale-watchdog": ("9acc scale watchdog / monitor perubahan scale Meta", "Guard / kill-switch"),
    "com.vex.activate-val-5am": ("Val 9acc auto-ON utama jam 05:00", "Val automation"),
    "com.vex.activate-val-0530-fallback": ("Val 9acc auto-ON fallback jam 05:30", "Val automation"),
    "com.vex.reset-val-budget": ("Val 9acc reset budget utama jam 23:00 (disabled file)", "Val automation"),
    "com.vex.reset-val-budget-backup": ("Val 9acc reset budget backup 00:10 (disabled file)", "Val automation"),
    "com.vex.ebook-perf-sheet": ("Update sheet Ads Performance V2 dari Meta 9acc", "Sheet / upload pipeline"),
    "com.vex.sheet-link-watcher": ("Scan sheet Link GDrive Test → manifest preview bulk upload", "Sheet / upload pipeline"),
    "com.kira.vex-roas-hourly": ("ROAS/report hourly all 9acc ke grup", "Reports / alerts"),
    "com.vex.monitor-saldo": ("Monitor spending limit/saldo 9acc", "Reports / alerts"),
    "com.vex.boncos-alert-morning": ("Boncos alert pagi", "Reports / alerts"),
    "com.vex.boncos-alert-afternoon": ("Boncos alert siang", "Reports / alerts"),
    "com.vex.boncos-alert-evening": ("Boncos alert sore", "Reports / alerts"),
    "com.vex.boncos-alert-20": ("Boncos alert malam", "Reports / alerts"),
    "com.vex.cashguard-7": ("Cashguard 07:15", "Reports / alerts"),
    "com.vex.cashguard-10": ("Cashguard 10:15", "Reports / alerts"),
    "com.vex.cashguard-13": ("Cashguard 13:15", "Reports / alerts"),
    "com.vex.cashguard-16": ("Cashguard 16:15", "Reports / alerts"),
    "com.vex.cashguard-19": ("Cashguard 19:15", "Reports / alerts"),
    "com.vex.pulse-check-8": ("Pulse check 08:05", "Reports / alerts"),
    "com.vex.pulse-check-10": ("Pulse check 10:05", "Reports / alerts"),
    "com.vex.pulse-check-12": ("Pulse check 12:05", "Reports / alerts"),
    "com.vex.pulse-check-14": ("Pulse check 14:05", "Reports / alerts"),
    "com.vex.pulse-check-16": ("Pulse check 16:05", "Reports / alerts"),
    "com.vex.pulse-check-18": ("Pulse check 18:05", "Reports / alerts"),
    "com.vex.pulse-check-20": ("Pulse check 20:05", "Reports / alerts"),
    "com.vex.duo-detector": ("Duo detector 9acc", "Detectors"),
    "com.vex.trio-detector": ("Trio detector 9acc", "Detectors"),
    "com.vex.nightly-val-check": ("Nightly Val 9acc health check", "Detectors"),
    "com.vex.morning-brief": ("Morning brief Vex", "Reports / alerts"),
    "com.vex.nightcap": ("Nightcap Vex", "Reports / alerts"),
}


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)


def launchctl_states() -> dict[str, dict[str, str | None]]:
    proc = run(["bash", "-lc", "launchctl list | grep -E 'com\\.(vex|kira\\.vex)' || true"])
    states: dict[str, dict[str, str | None]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            states[parts[-1]] = {"pid": None if parts[0] == "-" else parts[0], "last_status": parts[1]}
    return states


def schedule(start, interval) -> str:
    if interval:
        try:
            seconds = int(interval)
            return f"tiap {seconds // 60} menit" if seconds % 60 == 0 else f"tiap {seconds} detik"
        except Exception:
            return str(interval)
    if isinstance(start, dict):
        h = start.get("Hour")
        m = start.get("Minute", 0)
        wd = start.get("Weekday")
        text = f"{int(h):02d}:{int(m):02d} WIB" if h is not None else str(start)
        return text + (f" · weekday {wd}" if wd else "")
    if isinstance(start, list):
        vals = []
        for item in start:
            if isinstance(item, dict) and "Hour" in item:
                vals.append(f"{int(item['Hour']):02d}:{int(item.get('Minute', 0)):02d}")
        return ", ".join(vals) + " WIB" if vals else str(start)
    return "manual / tidak ada schedule"


def log_info(raw_path: str | None) -> dict | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.exists():
        return {"path": path.name, "exists": False}
    stat = path.stat()
    tail = ""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        tail = "\n".join(lines[-3:])[-500:]
    except Exception:
        pass
    return {
        "path": path.name,
        "exists": True,
        "size": stat.st_size,
        "mtime": dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S WIB"),
        "tail": tail,
    }


def load_plist(path: Path) -> dict | None:
    try:
        return plistlib.loads(path.read_bytes())
    except Exception:
        return None


def build_payload() -> dict:
    accounts = json.loads(ACCOUNTS_JSON.read_text(encoding="utf-8"))["accounts"]
    states = launchctl_states()
    rows = []
    paths = sorted(LAUNCH_DIR.glob("*.plist")) + sorted(LAUNCH_DIR.glob("*.disabled-*"))
    for path in paths:
        data = load_plist(path)
        if not data:
            continue
        label = data.get("Label") or path.stem
        if label not in JOBS:
            continue
        title, category = JOBS[label]
        prog = data.get("ProgramArguments") or []
        disabled = bool(data.get("Disabled", False)) or ".disabled-" in path.name
        state = states.get(label)
        status = "disabled" if disabled else ("running" if state and state.get("pid") else ("loaded" if state else "not-loaded"))
        if state and state.get("last_status") not in ("0", None) and not state.get("pid") and not disabled:
            status = "last-exit-nonzero"
        script = Path(prog[1]).name if len(prog) > 1 and isinstance(prog[1], str) else (Path(prog[0]).name if prog else "")
        rows.append({
            "label": label,
            "title": title,
            "category": category,
            "status": status,
            "launchctl": state,
            "schedule": schedule(data.get("StartCalendarInterval"), data.get("StartInterval")),
            "program": " ".join(Path(x).name if isinstance(x, str) and x.startswith("/Users/kantor") else str(x) for x in prog),
            "script": script,
            "stdout": log_info(data.get("StandardOutPath")),
            "stderr": log_info(data.get("StandardErrorPath")),
            "note": "Disabled/renamed; tidak akan jalan sampai di-enable ulang." if disabled else "",
        })
    summary: dict[str, int] = {}
    for row in rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    return {
        "title": "Cron Monitor — 9acc Vex",
        "snapshot": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S WIB"),
        "scope": "9acc Vex (10 accounts incl. EVERPRO-74), excluding EVPO-73 account-specific controllers",
        "accounts": accounts,
        "summary": summary,
        "jobs": rows,
        "source": {
            "host": "Mac Mini kantor",
            "launchagents": str(LAUNCH_DIR),
            "vex_workspace": "/Users/kantor/.hermes/profiles/vex/workspace",
            "token_policy": "No Meta token or credential is stored in this repo/dashboard.",
        },
    }


def sync(push: bool) -> None:
    DATA.parent.mkdir(parents=True, exist_ok=True)
    before = DATA.read_text(encoding="utf-8") if DATA.exists() else ""
    payload = build_payload()
    after = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    DATA.write_text(after, encoding="utf-8")
    if before == after:
        return
    if not push:
        print(f"updated {DATA}")
        return
    run(["git", "add", "data/cron.json"], cwd=REPO)
    msg = f"chore: sync 9acc vex cron status {payload['snapshot']}"
    commit = run(["git", "commit", "-m", msg], cwd=REPO)
    if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr).lower():
        raise SystemExit(commit.stdout + commit.stderr)
    push_proc = run(["git", "push", "origin", "main"], cwd=REPO)
    if push_proc.returncode != 0:
        raise SystemExit(push_proc.stdout + push_proc.stderr)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--push", action="store_true", help="commit and push changed data/cron.json")
    args = parser.parse_args()
    sync(push=args.push)

if __name__ == "__main__":
    main()
