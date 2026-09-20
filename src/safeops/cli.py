import argparse
import asyncio
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from safeops.asyncio_support import configure_loop
from safeops.config import Settings
from safeops.domain.models import Principal, Ticket
from safeops.effects.reconcile import Resolution, reconcile
from safeops.faults import CRASH_POINTS, Faults, SimulatedCrash
from safeops.graph.runtime import Runtime
from safeops.tools.local import seed_demo


async def checkpoint_setup(settings: Settings) -> None:
    async with AsyncPostgresSaver.from_conn_string(settings.database_url) as saver:
        await saver.setup()


async def demo(settings: Settings, args: argparse.Namespace) -> None:
    async with Runtime(settings, faults=Faults(args.crash_at)) as runtime:
        await seed_demo(runtime.db)
        if args.seed_only:
            print("Seeded CUS-001 / INV-10032 / ACC-2041 and CUS-002 records")
            return
        ticket = Ticket(id=args.ticket_id, customer_id="CUS-001", text=args.text)
        await runtime.ticket(ticket)
        run_id = await runtime.enqueue(ticket.id)
        print(f"run_id={run_id}")
        try:
            result = await runtime.process(run_id)
            if result["interrupts"] and args.approve:
                token = result["interrupts"][0]["token"]
                await runtime.deps.approvals.decide(
                    token,
                    Principal(name="cli.demo", role="admin"),
                    "approve",
                    "explicit CLI demo approval",
                )
                result = await runtime.process(run_id)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        except SimulatedCrash as exc:
            print(f"Injected crash at {exc}; closing all runtime connections")
    if args.crash_at:
        async with Runtime(settings) as fresh:
            print(
                json.dumps(await fresh.process(run_id), ensure_ascii=False, indent=2, default=str)
            )


def main() -> None:
    configure_loop()
    parser = argparse.ArgumentParser(prog="safeops")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("migrate")
    demo_parser = commands.add_parser("demo")
    demo_parser.add_argument("--seed-only", action="store_true")
    demo_parser.add_argument("--text", default="查一下 INV-10032")
    demo_parser.add_argument("--ticket-id", default="DEMO-LOOKUP")
    demo_parser.add_argument("--approve", action="store_true")
    demo_parser.add_argument("--crash-at", choices=CRASH_POINTS)
    evaluation = commands.add_parser("eval")
    evaluation.add_argument("--json", type=Path, default=Path("reports/evaluation.json"))
    evaluation.add_argument(
        "--database",
        action="store_true",
        help="include PostgreSQL graph/recovery/safety evaluation",
    )
    reconcile_parser = commands.add_parser("reconcile")
    reconcile_parser.add_argument("--effect", required=True)
    reconcile_parser.add_argument(
        "--outcome", choices=["performed", "not_performed"], required=True
    )
    reconcile_parser.add_argument("--evidence", required=True)
    reconcile_parser.add_argument("--result", default="null")
    reconcile_parser.add_argument("--token", required=True, help="configured admin bearer token")
    args = parser.parse_args()
    settings = Settings()
    if args.command == "serve":
        import uvicorn

        from safeops.api.app import create_app

        server = uvicorn.Server(
            uvicorn.Config(create_app(settings), host="0.0.0.0", port=args.port)
        )
        # Own the loop so uvicorn cannot replace Windows' psycopg-compatible selector.
        asyncio.run(server.serve())
    elif args.command == "migrate":
        command.upgrade(Config("alembic.ini"), "head")
        asyncio.run(checkpoint_setup(settings))
    elif args.command == "demo":
        asyncio.run(demo(settings, args))
    elif args.command == "eval":
        from safeops.evaluation.runner import evaluate

        report = asyncio.run(evaluate(settings, database=args.database))
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report["passed"] else 1)
    else:

        async def resolve() -> None:
            identity = settings.api_tokens.get(args.token)
            if identity is None:
                raise ValueError("invalid admin token")
            async with Runtime(settings) as runtime:
                run_id = await reconcile(
                    runtime.db,
                    args.effect,
                    Resolution(
                        outcome=args.outcome, evidence=args.evidence, result=json.loads(args.result)
                    ),
                    Principal.model_validate(identity),
                )
                await runtime.queue_resume(run_id)
                print(run_id)

        asyncio.run(resolve())


if __name__ == "__main__":
    main()
