"""A tiny fake Home Assistant: websocket auth + get_states + state_changed events, REST services/history."""
import asyncio
import json
import threading

import websockets
from websockets.http11 import Response
from websockets.datastructures import Headers

TOKEN = "test-token"


class FakeHA:
    def __init__(self, states):
        self.states = {s["entity_id"]: s for s in states}
        self.history = {}
        self.clients = set()
        self.loop = None
        self.port = None
        self._ready = threading.Event()

    # --- test controls --------------------------------------------------------------------
    def push(self, entity_id, state, **attrs):
        new = {"entity_id": entity_id, "state": state, "attributes": attrs, "last_changed": "2026-11-16T12:00:00+00:00"}
        self.states[entity_id] = new
        self._call(self._broadcast({"type": "event", "event": {"event_type": "state_changed", "data": {"entity_id": entity_id, "new_state": new}}}))

    def drop_clients(self):
        self._call(self._drop())

    def start(self):
        threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True).start()
        self._ready.wait(5)
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self._stop.set)

    # --- server ---------------------------------------------------------------------------
    def _call(self, coro):
        asyncio.run_coroutine_threadsafe(coro, self.loop).result(3)

    async def _broadcast(self, msg):
        for ws in list(self.clients):
            await ws.send(json.dumps({"id": 1, **msg}))

    async def _drop(self):
        for ws in list(self.clients):
            ws.transport.abort()          # abrupt drop, like a network failure or HA restart

    async def _handler(self, ws):
        await ws.send(json.dumps({"type": "auth_required"}))
        auth = json.loads(await ws.recv())
        if auth.get("access_token") != TOKEN:
            await ws.send(json.dumps({"type": "auth_invalid"}))
            return
        await ws.send(json.dumps({"type": "auth_ok"}))
        self.clients.add(ws)
        try:
            async for raw in ws:
                m = json.loads(raw)
                if m["type"] == "get_states":
                    await ws.send(json.dumps({"id": m["id"], "type": "result", "success": True, "result": list(self.states.values())}))
                elif m["type"] == "subscribe_events":
                    await ws.send(json.dumps({"id": m["id"], "type": "result", "success": True, "result": None}))
        finally:
            self.clients.discard(ws)

    def _http(self, connection, request):
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return Response(401, "Unauthorized", Headers(), b"")
        path = request.path
        body = b""
        if path.startswith("/api/history/period/"):
            eid = path.split("filter_entity_id=")[1].split("&")[0]
            body = json.dumps([[{"state": str(v)} for v in self.history.get(eid, [])]]).encode()
        return Response(200, "OK", Headers({"Content-Type": "application/json"}), body)

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        async with websockets.serve(self._handler, "127.0.0.1", 0, process_request=self._http) as srv:
            self.port = srv.sockets[0].getsockname()[1]
            self._ready.set()
            await self._stop.wait()
