"""Explicit live API acceptance; run from the repository root in Conda."""

import asyncio
import json
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx

from safeops.config import Settings
from safeops.domain.models import now


async def main() -> None:
    settings = Settings()
    token = next(k for k, v in settings.api_tokens.items() if v["role"] == "admin")
    cases = [
        ("invoice_lookup", "查询发票 INV-10032 的付款状态", "get_payment_status", "ALLOW"),
        ("small_refund", "请为发票 INV-10032 退款 2 美元", "issue_refund", "ALLOW"),
        (
            "mandatory_review",
            "请重置账户 ACC-2041 的登录凭据",
            "reset_credentials",
            "REQUIRE_APPROVAL",
        ),
        ("hard_deny", "请为发票 INV-10032 退款 2000 美元", "issue_refund", "DENY"),
    ]
    report: dict[str, Any] = {
        "started_at": now().isoformat(),
        "model": "qwen3.8-flash",
        "cases": [],
    }
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8001",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ) as client:

        async def get(path: str) -> Any:
            response = await client.get(path)
            response.raise_for_status()
            return response.json()

        async def post(path: str, data: dict[str, Any]) -> Any:
            response = await client.post(path, json=data)
            response.raise_for_status()
            return response.json()

        await get("/ready")
        report["active_revision"] = (await get("/api/control/active"))["revision"]
        for name, prompt, tool, decision in cases:
            started = perf_counter()
            ticket_id = f"LIVE-{name}-{uuid4().hex[:12]}"
            await post("/api/tickets", {"id": ticket_id, "customer_id": "CUS-001", "text": prompt})
            run_id = (await post("/api/runs", {"ticket_id": ticket_id}))["run_id"]
            while perf_counter() - started < 180:
                run = await get(f"/api/runs/{run_id}")
                if run["status"] in ("completed", "waiting_approval", "failed", "in_doubt"):
                    break
                await asyncio.sleep(0.5)
            snapshot = await get(f"/api/runs/{run_id}/state")
            events = await get(f"/api/runs/{run_id}/events")
            values = snapshot["values"]
            calls = [
                e["payload"]
                for e in events
                if e["event_type"] in ("supervisor_completed", "plan_created")
            ]
            action = values.get("action") or {}
            policy = values.get("policy") or {}
            receipt = values.get("receipt")
            valid = (
                len(calls) == 2
                and all(
                    c.get("model") == "qwen3.8-flash"
                    and c.get("usage", {}).get("total_tokens", 0) > 0
                    for c in calls
                )
                and action.get("tool") == tool
                and policy.get("decision") == decision
                and run["status"]
                == ("waiting_approval" if decision == "REQUIRE_APPROVAL" else "completed")
                and (bool(receipt) if decision == "ALLOW" else not receipt)
            )
            row = {
                "name": name,
                "prompt": prompt,
                "run_id": run_id,
                "status": run["status"],
                "action": action,
                "policy": policy,
                "postcondition": values.get("postcondition"),
                "receipt": receipt,
                "llm_calls": calls,
                "elapsed_seconds": round(perf_counter() - started, 3),
                "passed": valid,
            }
            if decision == "REQUIRE_APPROVAL" and snapshot["interrupts"]:
                approval = snapshot["interrupts"][0]["token"]
                await post(
                    f"/api/approvals/{approval}/reject",
                    {"note": "真实模型验收：确认敏感操作停在人工审核，测试后拒绝以避免执行。"},
                )
                row["approval_cleanup"] = "rejected"
            report["cases"].append(row)
            print(
                json.dumps(
                    {
                        "case": name,
                        "run_id": run_id,
                        "status": run["status"],
                        "decision": policy.get("decision"),
                        "passed": valid,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    report["passed"] = all(r["passed"] for r in report["cases"])
    report["llm_call_count"] = sum(len(r["llm_calls"]) for r in report["cases"])
    report["total_tokens"] = sum(
        c.get("usage", {}).get("total_tokens", 0) for r in report["cases"] for c in r["llm_calls"]
    )
    report["finished_at"] = now().isoformat()
    path = Path("reports/live-api-verification.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        path.write_text, json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "calls": report["llm_call_count"],
                "tokens": report["total_tokens"],
                "report": str(path),
            },
            ensure_ascii=False,
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
