#!/usr/bin/env python3
"""Submit and poll a real SEO/QA audit through the running Night Walker API.

Example:
  uv run python scripts/live_audit_smoke.py --url https://weylandts.co.za --workflow seo
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TERMINAL = {"completed", "blocked", "failed", "stopped"}


def request_json(url: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Public website URL to audit")
    parser.add_argument("--workflow", choices=("seo", "qa", "both"), default="seo")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Night Walker API base URL")
    parser.add_argument("--timeout", type=int, default=1800, help="Maximum task wait in seconds")
    parser.add_argument("--poll-interval", type=float, default=3.0)
    parser.add_argument("--output", help="Optional path to write the final task JSON")
    args = parser.parse_args()

    objective = {
        "seo": f"Perform a comprehensive SEO audit of {args.url}",
        "qa": f"Run a read-only website QA audit of {args.url}",
        "both": f"Perform an SEO audit and QA testing of {args.url}",
    }[args.workflow]
    base = args.base_url.rstrip("/")

    try:
        submitted = request_json(
            base + "/tasks", method="POST", payload={"objective": objective}
        )
        task_id = submitted.get("task_id")
        if not task_id:
            print("API did not return a task_id:", json.dumps(submitted, indent=2))
            return 2
        print(f"Submitted {args.workflow} audit: {task_id}")

        deadline = time.monotonic() + max(30, args.timeout)
        snapshot = submitted
        while time.monotonic() < deadline:
            snapshot = request_json(base + "/tasks/" + str(task_id))
            status = str(snapshot.get("status") or "")
            current = snapshot.get("current_step") or {}
            print(
                f"status={status} round={snapshot.get('round', 0)} "
                f"step={current.get('objective') or '-'}"
            )
            if status in TERMINAL:
                break
            time.sleep(max(0.5, args.poll_interval))
        else:
            print(f"Timed out waiting for task {task_id}; it remains available in the UI/API.")
            return 3

        report = snapshot.get("result") or {}
        summary = (report.get("structured_data") or {}).get("audit_report_summary", {})
        print("\nFinal status:", snapshot.get("status"))
        print("Visited URLs:", len(report.get("visited_urls") or []))
        print("Finding count:", len(report.get("findings") or []))
        if summary:
            print("Audit summary:", json.dumps(summary, indent=2))
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, indent=2, ensure_ascii=False)
            print("Saved:", args.output)
        return 0 if snapshot.get("status") == "completed" else 2
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"Night Walker API returned HTTP {exc.code}: {detail}", file=sys.stderr)
        return 2
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"Could not complete live audit smoke test: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
