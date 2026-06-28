#!/usr/bin/env python3
"""Apply approved cron monitor ON/OFF requests from GitHub issues.

Security model:
- Browser/GitHub Pages never stores secrets.
- Request is a GitHub issue in kedebuk/cron-monitor-9acc-vex.
- This local Mac executor uses existing gh auth and only honors issues authored by kedebuk.
- Action scope is restricted to labels present in data/cron.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
REPO = "kedebuk/cron-monitor-9acc-vex"
AUTHORIZED_AUTHORS = {"kedebuk"}
LAUNCH_DIR = Path("/Users/kantor/Library/LaunchAgents")
TITLE_RE = re.compile(r"^\[cron-control\]\s+(on|off)\s+([A-Za-z0-9_.-]+)\s*$", re.I)


def run(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError((proc.stdout + proc.stderr).strip() or f"command failed: {' '.join(cmd)}")
    return proc


def allowed_labels() -> set[str]:
    data = json.loads((REPO_DIR / "data" / "cron.json").read_text(encoding="utf-8"))
    return {str(job.get("label") or "") for job in data.get("jobs", []) if job.get("label")}


def issue_list() -> list[dict]:
    proc = run([
        "gh", "issue", "list", "--repo", REPO, "--state", "open", "--limit", "50",
        "--json", "number,title,author,body,createdAt",
    ], check=True)
    return json.loads(proc.stdout or "[]")


def current_state(label: str) -> dict[str, str | None]:
    proc = run(["launchctl", "list"])
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1] == label:
            return {"pid": None if parts[0] == "-" else parts[0], "status": parts[1]}
    return {}


def plist_for_label(label: str) -> Path | None:
    direct = LAUNCH_DIR / f"{label}.plist"
    if direct.exists():
        return direct
    candidates = sorted(LAUNCH_DIR.glob(f"{label}.plist.disabled-*")) + sorted(LAUNCH_DIR.glob(f"{label}.disabled-*"))
    return candidates[-1] if candidates else None


def enable_label(label: str, *, dry_run: bool) -> str:
    path = plist_for_label(label)
    if not path:
        raise RuntimeError(f"plist tidak ditemukan untuk {label}")
    target = LAUNCH_DIR / f"{label}.plist"
    actions = []
    if path != target:
        actions.append(f"rename {path.name} -> {target.name}")
        if not dry_run:
            if target.exists():
                raise RuntimeError(f"target plist sudah ada: {target}")
            path.rename(target)
    actions.append(f"bootstrap {label}")
    if not dry_run:
        run(["launchctl", "bootstrap", f"gui/{subprocess.check_output(['id','-u'], text=True).strip()}", str(target)])
        run(["launchctl", "kickstart", "-k", f"gui/{subprocess.check_output(['id','-u'], text=True).strip()}/{label}"])
    state = current_state(label) if not dry_run else {}
    return "; ".join(actions) + (f"; state={state}" if state else "")


def disable_label(label: str, *, dry_run: bool) -> str:
    path = plist_for_label(label)
    if not path:
        raise RuntimeError(f"plist tidak ditemukan untuk {label}")
    uid = subprocess.check_output(["id", "-u"], text=True).strip()
    actions = [f"bootout {label}"]
    if not dry_run:
        run(["launchctl", "bootout", f"gui/{uid}/{label}"])
    active = LAUNCH_DIR / f"{label}.plist"
    if active.exists():
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        disabled = LAUNCH_DIR / f"{label}.plist.disabled-dashboard-{stamp}"
        actions.append(f"rename {active.name} -> {disabled.name}")
        if not dry_run:
            active.rename(disabled)
    else:
        actions.append("already disabled/renamed")
    state = current_state(label) if not dry_run else {}
    return "; ".join(actions) + (f"; state={state}" if state else "")


def apply(action: str, label: str, *, dry_run: bool) -> str:
    if action == "on":
        return enable_label(label, dry_run=dry_run)
    if action == "off":
        return disable_label(label, dry_run=dry_run)
    raise RuntimeError(f"action tidak dikenal: {action}")


def comment_and_close(number: int, message: str, *, dry_run: bool) -> None:
    if dry_run:
        print(f"DRY issue #{number}: {message}")
        return
    run(["gh", "issue", "comment", str(number), "--repo", REPO, "--body", message], check=True)
    run(["gh", "issue", "close", str(number), "--repo", REPO, "--reason", "completed"], check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    labels = allowed_labels()
    processed = 0
    for issue in issue_list():
        title = str(issue.get("title") or "").strip()
        match = TITLE_RE.match(title)
        if not match:
            continue
        action = match.group(1).lower()
        label = match.group(2)
        number = int(issue["number"])
        author = str((issue.get("author") or {}).get("login") or "")
        if author not in AUTHORIZED_AUTHORS:
            comment_and_close(number, f"Ditolak: author `{author}` tidak authorized untuk control Vex cron.", dry_run=args.dry_run)
            processed += 1
            continue
        if label not in labels:
            comment_and_close(number, f"Ditolak: label `{label}` tidak ada di allowlist dashboard.", dry_run=args.dry_run)
            processed += 1
            continue
        try:
            result = apply(action, label, dry_run=args.dry_run)
            # Refresh dashboard data and push if this was a real change.
            if not args.dry_run:
                subprocess.run([str(REPO_DIR / "sync_status.py"), "--push"], cwd=REPO_DIR, text=True, timeout=180)
            comment_and_close(number, f"✅ `{action}` diterapkan untuk `{label}`.\n\n`{result}`", dry_run=args.dry_run)
        except Exception as exc:
            comment_and_close(number, f"❌ Gagal `{action}` untuk `{label}`: `{exc}`", dry_run=args.dry_run)
        processed += 1
    if processed:
        print(f"processed={processed}")


if __name__ == "__main__":
    main()
