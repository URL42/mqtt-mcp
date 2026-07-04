"""Publish fake aircube-style readings so the bridge can be tested end to end.

Usage: uv run scripts/fake_sensor.py
Honors the same MQTT_* environment variables as server.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time

import aiomqtt

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD")
INTERVAL_SECONDS = 5


async def main() -> None:
    temperature = 21.5
    co2 = 600.0
    humidity = 45.0
    async with aiomqtt.Client(
        hostname=MQTT_HOST,
        port=MQTT_PORT,
        username=MQTT_USERNAME,
        password=MQTT_PASSWORD,
        identifier="fake-aircube",
    ) as client:
        print(f"Publishing to {MQTT_HOST}:{MQTT_PORT} every {INTERVAL_SECONDS}s (ctrl-c to stop)")
        while True:
            temperature += random.uniform(-0.3, 0.3)
            co2 = max(400.0, co2 + random.uniform(-25, 25))
            humidity = min(90.0, max(20.0, humidity + random.uniform(-1, 1)))
            readings = {
                "sensors/aircube/temperature": round(temperature, 1),
                "sensors/aircube/co2": round(co2),
                "sensors/aircube/humidity": round(humidity, 1),
            }
            for topic, value in readings.items():
                await client.publish(topic, json.dumps({"value": value, "ts": int(time.time())}))
            print(f"published {readings}")
            await asyncio.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
