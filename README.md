# mqtt-mcp

An MQTT to MCP bridge. Devices publish sensor readings to a Mosquitto broker;
this server subscribes in the background, caches the last message per topic,
and exposes that cache as MCP tools over streamable HTTP. Any MCP client
(n8n's MCP Client Tool, Claude Code, the MCP Inspector) can then ask questions
like "what is the CO2 in the office?" or send commands back to devices.

```
ESP32s --publish--> Mosquitto <--subscribe/publish-- mqtt-mcp (this server)
                                                          ^ streamable HTTP :8000/mcp
                                             n8n AI Agent / Claude Code
```

## Tools

| Tool | What it does |
|---|---|
| `list_topics(prefix="")` | Topics seen since start, with last payload, age, and count |
| `read_topic(topic)` | Last message on an exact topic |
| `publish(topic, payload)` | Send a command; restricted to topics under `cmd/` |
| `bridge_status()` | Broker connection state, for telling outages apart from silent devices |

The cache is in memory only. After a restart it refills as devices publish
(retained messages arrive immediately on reconnect).

## Topic conventions

- `sensors/aircube/<metric>` for the aircube readings (temperature, co2, humidity)
- `sensors/espcam/<metric>` reserved for the FireBeetle + ESP32-CAM vision project
- `cmd/<device>/<action>` for commands; only this prefix is writable through MCP

## Server setup (Docker host)

1. Clone, then create broker credentials (writes `mosquitto/config/passwd`):

   ```sh
   docker compose run --rm mosquitto mosquitto_passwd -c -b /mosquitto/config/passwd mqtt <password>
   ```

2. Copy `.env.example` to `.env` and fill in the same username and password.

3. `docker compose up -d --build`

Mosquitto listens on 1883 (devices connect there), the MCP endpoint is
`http://<host>:8000/mcp`. Neither has TLS and the MCP endpoint has no auth,
so keep both LAN-only.

## Local development (Mac)

```sh
MQTT_HOST=<broker-ip> MQTT_USERNAME=mqtt MQTT_PASSWORD=<password> uv run server.py
```

In a second terminal, generate traffic:

```sh
MQTT_HOST=<broker-ip> MQTT_USERNAME=mqtt MQTT_PASSWORD=<password> uv run scripts/fake_sensor.py
```

Then poke at the tools with the MCP Inspector:

```sh
npx @modelcontextprotocol/inspector
# transport: Streamable HTTP, URL: http://localhost:8000/mcp
```

## Connecting from n8n

Add an AI Agent node, attach an **MCP Client Tool**, and set the endpoint to
`http://<host>:8000/mcp` with transport "HTTP Streamable" (n8n 1.94 or newer;
older versions only speak SSE, in which case run the server with
`transport="sse"` in `server.py` and point n8n at `http://<host>:8000/sse`).

## Configuration

All via environment variables, read in `server.py`:

| Variable | Default | Purpose |
|---|---|---|
| `MQTT_HOST` | `localhost` | Broker hostname |
| `MQTT_PORT` | `1883` | Broker port |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | unset | Broker credentials |
| `CMD_PREFIX` | `cmd/` | Only prefix the publish tool may write to |
| `MCP_HOST` | `0.0.0.0` | MCP listen address |
| `MCP_PORT` | `8000` | MCP listen port |
