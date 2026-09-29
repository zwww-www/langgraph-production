import argparse
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any

from safeops.config import Settings
from safeops.domain.models import Forbidden, Principal, Ticket
from safeops.graph.runtime import Runtime
from safeops.persistence.database import Database
from safeops.policy.control import CompileInput, ControlPlane, ReleaseInput
from safeops.risk.models import BusinessSLO
from safeops.tools.local import seed_demo


def register(commands: Any) -> None:
    for name in (
        "generate-demo-history",
        "compile-policy",
        "run-policy-backtest",
        "risk-demo",
        "release-policy",
    ):
        parser = commands.add_parser(name)
        parser.add_argument(
            "--token-env",
            default="SAFEOPS_ADMIN_TOKEN",
            help="environment variable containing an admin bearer token",
        )
        parser.add_argument("--count", type=int, default=100000)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--dataset")
        parser.add_argument("--candidate")
        parser.add_argument("--slo", type=Path, help="optional BusinessSLO JSON snapshot")
        parser.add_argument("--output", type=Path, default=Path("reports/risk-backtest.json"))
        parser.add_argument(
            "--release",
            action="store_true",
            help="explicit admin authorization to release the computed recommendation",
        )


async def run(settings: Settings, args: argparse.Namespace) -> None:
    identity = settings.api_tokens.get(os.environ.get(args.token_env, ""))
    if not identity or identity.get("role") != "admin":
        raise Forbidden("configure an admin token in the selected environment variable")
    principal = Principal.model_validate(identity)
    db = Database(settings)
    started = perf_counter()
    report: dict[str, Any] = {}
    try:
        control = ControlPlane(db)
        if args.slo:
            await control.put_slo(
                BusinessSLO.model_validate_json(args.slo.read_text(encoding="utf-8")), principal
            )
        if args.command in ("generate-demo-history", "risk-demo"):
            dataset = await control.generate(args.count, args.seed, principal)
            report["dataset"] = dataset
            args.dataset = dataset["id"]
        if args.command in ("compile-policy", "risk-demo"):
            compilation = await control.compile(
                CompileInput(
                    source="synthetic" if args.dataset else "runtime", dataset_id=args.dataset
                ),
                principal,
            )
            report["compilation"] = compilation
            args.candidate = compilation["recommended"]
        if args.candidate:
            candidate = await control.candidate(args.candidate)
            report["candidate"] = candidate
            if args.release or args.command == "release-policy":
                report["release"] = await control.release(
                    args.candidate,
                    ReleaseInput(
                        note="Explicit authenticated CLI release after deterministic backtest",
                        diff_digest=candidate["diff_digest"],
                        expected_revision=candidate["base_revision"],
                    ),
                    principal,
                )
        elif args.command in ("run-policy-backtest", "release-policy"):
            raise ValueError("--candidate required")
        report["elapsed_seconds"] = round(perf_counter() - started, 3)
        report["active_revision"] = (await control.registry.active()).revision
        if args.command == "risk-demo" and "release" in report:
            # Offline planning keeps this control-plane benchmark free from LLM/network variability.
            config = settings.model_copy(update={"llm_provider": "offline"})
            async with Runtime(config) as runtime:
                await seed_demo(runtime.db)
                ticket = Ticket(
                    id=f"RISK-DEMO-{args.candidate[:12]}",
                    customer_id="CUS-001",
                    text="Refund $2 INV-10032",
                )
                await runtime.ticket(ticket)
                run_id = await runtime.enqueue(ticket.id)
                state = await runtime.process(run_id)
                report["runtime"] = {
                    "run_id": run_id,
                    "policy": state["values"].get("policy"),
                    "postcondition": state["values"].get("postcondition"),
                    "interrupts": state["interrupts"],
                }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        summary = {k: v for k, v in report.items() if k != "candidate"}
        if "compilation" in summary:
            summary["compilation"] = {
                k: v for k, v in summary["compilation"].items() if k != "candidate_ids"
            }
        if "candidate" in report:
            summary["backtest"] = report["candidate"]["result"]["replay"]
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    finally:
        await db.close()
