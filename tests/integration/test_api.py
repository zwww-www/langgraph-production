import httpx
import pytest

from safeops.api.app import create_app

pytestmark = pytest.mark.integration


async def test_api_sse_approval_and_inspection(settings):
    app = create_app(settings, start_worker=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/ready")).status_code == 200
            assert (await client.get("/api/runs")).status_code == 401
            client.headers["Authorization"] = "Bearer demo-admin"
            ticket = {
                "id": "api-ticket",
                "customer_id": "CUS-001",
                "text": "Refund $45 on INV-10032",
            }
            assert (await client.post("/api/tickets", json=ticket)).status_code == 201
            run = await client.post("/api/runs", json={"ticket_id": ticket["id"]})
            assert run.status_code == 202
            run_id = run.json()["run_id"]
            await app.state.runtime.process(run_id)
            approvals = (await client.get("/api/approvals")).json()
            token = approvals[0]["token"]
            client.headers["Authorization"] = "Bearer demo-operator"
            assert (
                await client.post(f"/api/approvals/{token}/approve", json={"note": "checked"})
            ).status_code == 403
            client.headers["Authorization"] = "Bearer demo-admin"
            assert (
                await client.post(f"/api/approvals/{token}/approve", json={"note": "checked"})
            ).status_code == 200
            assert (
                await client.post(f"/api/approvals/{token}/approve", json={"note": "checked"})
            ).status_code == 200
            assert (
                await client.post(f"/api/approvals/{token}/reject", json={"note": "checked"})
            ).status_code == 409
            await app.state.runtime.process(run_id)
            for suffix in ("", "/events", "/state", "/checkpoints"):
                assert (await client.get(f"/api/runs/{run_id}{suffix}")).status_code == 200
            response = await client.get(f"/api/runs/{run_id}/stream")
            assert "text/event-stream" in response.headers["content-type"]
            assert "tool_completed" in response.text
            assert "run_completed" in response.text
            events = (await client.get(f"/api/runs/{run_id}/events")).json()
            response = await client.get(
                f"/api/runs/{run_id}/stream", headers={"Last-Event-ID": str(events[-1]["id"])}
            )
            assert "event: agent_event" not in response.text
            assert (await client.get("/api/customers/CUS-001/memory")).status_code == 200
            assert (await client.get("/api/runs/missing/state")).status_code == 404
            assert (
                await client.post("/api/runs", json={"ticket_id": "missing"})
            ).status_code == 404
            assert (
                await client.post("/api/tickets", json=ticket | {"approved": True})
            ).status_code == 422


async def test_worker_processes_durable_queue(settings):
    import asyncio

    from safeops.domain.models import Ticket
    from safeops.graph.runtime import Runtime
    from safeops.graph.worker import worker

    async with Runtime(settings) as runtime:
        await runtime.ticket(Ticket(id="worker", customer_id="CUS-001", text="INV-10032"))
        run_id = await runtime.enqueue("worker")
        stop = asyncio.Event()
        task = asyncio.create_task(worker(runtime, stop))
        try:
            async with asyncio.timeout(10):
                while (await runtime.get_run(run_id))["status"] != "completed":  # noqa: ASYNC110 - polls durable database state, not a local event
                    await asyncio.sleep(0.05)
        finally:
            stop.set()
            await task
