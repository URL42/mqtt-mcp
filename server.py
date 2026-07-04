"""MQTT to MCP bridge.

A background task subscribes to the broker and caches the last message per
topic. MCP tools expose that cache, plus a guarded publish, over streamable
HTTP so clients like n8n's MCP Client Tool or Claude Code can use it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import aiomqtt
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("mqtt-bridge")

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD")
CMD_PREFIX = os.environ.get("CMD_PREFIX", "cmd/")
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))
RECONNECT_SECONDS = 5
PAYLOAD_PREVIEW_CHARS = 200


@dataclass
class TopicRecord:
    payload: str
    received_at: float
    count: int


cache: dict[str, TopicRecord] = {}
broker_status: dict[str, object] = {"connected": False, "last_error": None}


def _mqtt_client(identifier: str) -> aiomqtt.Client:
    return aiomqtt.Client(
        hostname=MQTT_HOST,
        port=MQTT_PORT,
        username=MQTT_USERNAME,
        password=MQTT_PASSWORD,
        identifier=identifier,
    )


def _decode(payload: object) -> str:
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload).decode("utf-8", errors="replace")
    return str(payload)


async def listen() -> None:
    """Subscribe to everything and keep the last-value cache current.

    Reconnects forever; a broker outage degrades the bridge (stale cache,
    disconnected status) rather than killing it.
    """
    while True:
        try:
            log.info("connecting to broker %s:%s", MQTT_HOST, MQTT_PORT)
            async with _mqtt_client("mqtt-mcp-listener") as client:
                await client.subscribe("#")
                broker_status["connected"] = True
                broker_status["last_error"] = None
                log.info("subscribed to '#', waiting for messages")
                async for message in client.messages:
                    topic = str(message.topic)
                    previous = cache.get(topic)
                    cache[topic] = TopicRecord(
                        payload=_decode(message.payload),
                        received_at=time.time(),
                        count=previous.count + 1 if previous else 1,
                    )
                    log.info("cached %s (%d topics total)", topic, len(cache))
            log.warning("message stream ended cleanly; reconnecting")
        except aiomqtt.MqttError as exc:
            broker_status["connected"] = False
            broker_status["last_error"] = str(exc)
            log.warning("broker error: %s; reconnecting in %ss", exc, RECONNECT_SECONDS)
            await asyncio.sleep(RECONNECT_SECONDS)
        except Exception:
            broker_status["connected"] = False
            log.exception("unexpected error in listener; reconnecting in %ss", RECONNECT_SECONDS)
            await asyncio.sleep(RECONNECT_SECONDS)


mcp = FastMCP("mqtt-bridge", host=MCP_HOST, port=MCP_PORT)


@mcp.tool()
def list_topics(prefix: str = "") -> str:
    """List MQTT topics seen since the bridge started, with freshness info.

    Call this to discover what devices and sensors exist, or to check which
    devices are still alive (age_seconds shows time since the last message).
    Optionally filter with a topic prefix like "sensors/aircube/".
    """
    now = time.time()
    topics = [
        {
            "topic": topic,
            "last_payload": record.payload[:PAYLOAD_PREVIEW_CHARS],
            "age_seconds": round(now - record.received_at, 1),
            "message_count": record.count,
        }
        for topic, record in sorted(cache.items())
        if topic.startswith(prefix)
    ]
    return json.dumps({"broker": broker_status, "topics": topics}, indent=2)


@mcp.tool()
def read_topic(topic: str) -> str:
    """Read the last message published to an exact MQTT topic.

    Call this when you need the current value of a specific sensor, e.g.
    "sensors/aircube/co2". Use list_topics first if you are unsure of the
    exact topic name. age_seconds tells you how stale the reading is.
    """
    record = cache.get(topic)
    if record is None:
        return json.dumps(
            {
                "error": f"No message seen on topic {topic!r} since the bridge started.",
                "hint": "Use list_topics to see available topics.",
            }
        )
    return json.dumps(
        {
            "topic": topic,
            "payload": record.payload,
            "age_seconds": round(time.time() - record.received_at, 1),
            "message_count": record.count,
        },
        indent=2,
    )


@mcp.tool()
async def publish(topic: str, payload: str) -> str:
    """Publish a command message to an MQTT topic.

    Call this to send a command to a device, e.g. topic "cmd/aircube/led"
    with payload "on". Only topics under the command prefix are allowed;
    sensor topics cannot be written to.
    """
    if not topic.startswith(CMD_PREFIX):
        return json.dumps(
            {"error": f"Publishing is restricted to topics under {CMD_PREFIX!r}."}
        )
    try:
        async with _mqtt_client("mqtt-mcp-publisher") as client:
            await client.publish(topic, payload)
    except aiomqtt.MqttError as exc:
        return json.dumps({"error": f"Publish failed: {exc}"})
    return json.dumps({"published": {"topic": topic, "payload": payload}})


@mcp.tool()
def bridge_status() -> str:
    """Report the bridge's connection to the MQTT broker.

    Call this first when other tools return empty or stale data, to tell a
    broker outage apart from devices simply not publishing.
    """
    return json.dumps(
        {
            "broker_host": MQTT_HOST,
            "broker_port": MQTT_PORT,
            "connected": broker_status["connected"],
            "last_error": broker_status["last_error"],
            "topics_cached": len(cache),
        },
        indent=2,
    )


def build_app():
    """Build the ASGI app and start the subscriber at the process-level lifespan.

    FastMCP's own `lifespan` runs per client session under streamable HTTP, so a
    task started there dies when the client disconnects. The Starlette app's
    lifespan runs once at process startup, which is where a persistent
    background subscriber belongs.
    """
    app = mcp.streamable_http_app()
    base_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app) -> AsyncIterator[None]:
        task = asyncio.create_task(listen())
        try:
            async with base_lifespan(app):
                yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app.router.lifespan_context = lifespan
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(build_app(), host=MCP_HOST, port=MCP_PORT)
