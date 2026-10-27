"""Owner-selected sources shared by the MCP page, Claude and Codex."""
from __future__ import annotations

import json
import re
import time
import uuid

from . import mcp_servers
from .agent_reach import AgentReach, handles_of
from .source_mcp import CredentialStorage, SourceMcp

PRESETS = {
    "n8n": {"title": "n8n", "url": "", "auth": "header", "help": "Paste the MCP server URL and MCP access token from Settings > Instance-level MCP."},
    "nexlev": {"title": "Nexlev", "url": "https://prod.dashboard.nexlev.io/api/claude-mcp", "auth": "oauth", "help": "Sign in with your Nexlev account to read channel analytics, transcripts and research."},
    "neon": {"title": "Neon", "url": "https://mcp.neon.tech/mcp", "auth": "header", "help": "Connect with a Neon API key or browser sign-in. Tool access follows your account permissions."},
}
DISCORD_WORKFLOW = "HGTU978gZLJns4Nw"


def unpack(result):
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    for content in result.get("content", []):
        if content.get("type") == "text":
            try:
                return json.loads(content["text"])
            except ValueError:
                continue
    return result


def discord_messages(data):
    """Accept n8n's execution envelope and the reader's JSON chat output, keeping source records."""
    messages, seen = [], set()
    remaining = [20000]
    def walk(value, depth=0):
        remaining[0] -= 1
        if depth > 20 or remaining[0] < 0 or len(messages) >= 100:
            return
        if isinstance(value, dict):
            if value.get('message_id') and str(value.get('source_url', '')).startswith('https://discord.com/channels/'):
                mid = str(value['message_id'])
                if mid not in seen:
                    seen.add(mid)
                    messages.append({key: value.get(key, '' if key not in ('embeds', 'attachments') else [])
                        for key in ('message_id', 'channel_id', 'timestamp', 'author', 'content', 'embeds', 'attachments', 'source_url')})
            else:
                for item in value.values():
                    walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)
        elif isinstance(value, str) and value.startswith('{') and len(value) < 1_000_000:
            try:
                walk(json.loads(value), depth + 1)
            except ValueError:
                pass
    walk(data)
    return sorted(messages, key=lambda m: m.get('timestamp') or '')


class Sources:
    def __init__(self, settings, save_setting, storage, store, client_factory=None, reach_runner=None):
        self.settings, self.save_setting, self.storage, self.store = settings, save_setting, storage, store
        self.mcp = SourceMcp(settings, client_factory)
        self.reach = AgentReach(settings, reach_runner)
        self.executions = {}

    def state(self):
        servers = mcp_servers.check(self.settings.mcp_servers or {})
        items = {}
        for name, preset in PRESETS.items():
            cfg = servers.get(name)
            items[name] = {**preset, "configured": cfg is not None, "enabled": cfg.get("enabled", True) if cfg else False,
                "url": cfg.get("url", "") if cfg else preset["url"], "auth": cfg.get("auth", "header") if cfg else preset["auth"],
                "has_token": bool(mcp_servers.headers_of(name, cfg).get("Authorization")) if cfg else False,
                "signed_in": bool(CredentialStorage(name, cfg["url"]).read(":tokens")) if cfg and cfg.get("auth") == "oauth" else False}
        discord = {"server": "n8n", "workflow_id": DISCORD_WORKFLOW, "enabled": False,
                   **((self.settings.source_connections or {}).get("discord") or {})}
        return {"connectors": items, "discord": discord, "agent_reach": self.reach.status(),
                "drive": self.storage.status()["drive"], "servers": list(servers), "agents": ["Claude", "Codex"]}

    def prune(self, old, new):
        for name, cfg in old.items():
            other = new.get(name, {})
            if not other or cfg.get("url") != other.get("url") or cfg.get("auth") != other.get("auth"):
                self.mcp.forget(name, cfg)
            elif not other.get('enabled', True):
                self.mcp.cancel(name)

    def save_connector(self, name, data):
        if name not in PRESETS:
            raise ValueError("Choose n8n, Nexlev or Neon.")
        url = str(data.get("url") or PRESETS[name]["url"]).strip()
        # Private connections and credentials must never be sent over plaintext remote HTTP.
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")):
            raise ValueError("Use an HTTPS MCP URL, or localhost for a local instance.")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Put credentials in the token field, not in the URL.")
        auth = data.get("auth", PRESETS[name]["auth"])
        if auth not in ("header", "oauth"):
            raise ValueError("Choose API token or browser sign-in.")
        old = mcp_servers.check(self.settings.mcp_servers or {})
        entry = {"type": "http", "url": url, "preset": name}
        if not data.get("enabled", True):
            entry["enabled"] = False
        if auth == "oauth":
            entry["auth"] = "oauth"
        else:
            token = str(data.get("token") or "").strip()
            if token and not token.lower().startswith("bearer "):
                token = "Bearer " + token
            if not token and (old.get(name, {}).get("url") != url or old.get(name, {}).get("auth") == "oauth"):
                raise ValueError("Add an API token for this server URL, or choose browser sign-in.")
            if not token and not mcp_servers.headers_of(name, old.get(name, {})).get("Authorization"):
                raise ValueError("Add an API token, or choose browser sign-in.")
            entry["headers"] = {"Authorization": token}
        secrets = {}
        new = mcp_servers.check({**old, name: entry}, secrets)
        self.prune(old, new)
        mcp_servers.save_secrets(old, new, secrets)
        self.settings.mcp_servers = new
        self.save_setting("mcp_servers", new)
        return self.state()

    def remove_connector(self, name):
        if name not in PRESETS:
            raise ValueError("Unknown source.")
        old = mcp_servers.check(self.settings.mcp_servers or {})
        new = {n: cfg for n, cfg in old.items() if n != name}
        self.prune(old, new)
        mcp_servers.save_secrets(old, new, {})
        self.settings.mcp_servers = new
        self.save_setting("mcp_servers", new)
        return self.state()

    def save_preferences(self, name, data):
        if name == "discord":
            server, workflow = str(data.get("server") or "n8n"), str(data.get("workflow_id") or DISCORD_WORKFLOW).strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", workflow) or not mcp_servers.NAME.fullmatch(server):
                raise ValueError("Choose a valid MCP server name and workflow ID.")
            value = {"server": server, "workflow_id": workflow, "enabled": bool(data.get("enabled", True))}
        elif name == "agent_reach":
            command = str(data.get("command") or "").strip().strip('"')
            value = {"command": command, "handles": handles_of(data.get("handles", [])), "enabled": bool(data.get("enabled", True))}
        else:
            raise ValueError("Unknown source preferences.")
        self.settings.source_connections = {**(self.settings.source_connections or {}), name: value}
        self.save_setting("source_connections", self.settings.source_connections)
        return self.state()

    def discord(self):
        cfg = self.state()["discord"]
        if not cfg["enabled"]:
            raise ValueError("Enable Discord in MCP first.")
        self.mcp.config(cfg["server"])
        return cfg

    async def test_discord(self):
        cfg = self.discord()
        result = unpack(await self.mcp.call(cfg["server"], "get_workflow_details", {"workflowId": cfg["workflow_id"], "detailLevel": "full"}))
        workflow = result.get("workflow") or {}
        if workflow.get("id") != cfg["workflow_id"]:
            raise ValueError("The selected Discord workflow could not be verified.")
        # Verify the actual graph before executing; an arbitrary workflow ID is not a read-only guarantee.
        nodes = workflow.get("nodes") or []
        allowed = {"n8n-nodes-base.manualTrigger", "@n8n/n8n-nodes-langchain.chatTrigger", "n8n-nodes-base.discord", "n8n-nodes-base.code"}
        if not nodes or not any(n.get('type') == 'n8n-nodes-base.discord' for n in nodes) or any(n.get("type") not in allowed for n in nodes):
            raise ValueError("This workflow is not the supported Discord reader. Use the adapted read-only workflow.")
        for node in nodes:
            if node.get("type") == "n8n-nodes-base.discord":
                params = node.get("parameters", {})
                if params.get("resource") != "message" or params.get("operation") != "getAll":
                    raise ValueError("Discord workflow contains an operation other than reading messages.")
        return {"ok": True, "workflow": {"id": workflow["id"], "name": workflow.get("name")},
                "message": "Workflow found. Testing this connection did not run it or read messages."}

    async def read_discord(self):
        cfg = self.discord()
        await self.test_discord()
        result = unpack(await self.mcp.call(cfg["server"], "execute_workflow", {"workflowId": cfg["workflow_id"],
            "executionMode": "manual", "inputs": {"type": "chat", "chatInput": "Read the latest final scripts for One Production."}}))
        execution = result.get("executionId")
        if not execution:
            raise ValueError(str(result.get("error") or "n8n returned no execution ID."))
        self.executions[str(execution)] = {**cfg, "url_snapshot": self.mcp.config(cfg['server']).get('url'), "started": time.time()}
        return result

    async def discord_result(self, execution):
        cfg = self.executions.get(execution)
        if not cfg or time.time() - cfg["started"] > 3600:
            raise ValueError("Start a new Discord read in One Production before checking its results.")
        current = self.discord()
        if any(current[k] != cfg[k] for k in ("server", "workflow_id")) or self.mcp.config(current['server']).get('url') != cfg['url_snapshot']:
            raise ValueError("The Discord connection changed. Start a new read.")
        result = unpack(await self.mcp.call(cfg["server"], "get_workflow_execution", {"workflowId": cfg["workflow_id"],
            "executionId": execution, "includeData": True, "nodeNames": ["Normalize and Order Script Posts"], "truncateData": 100}))
        return {'execution': result.get('execution'), 'error': result.get('error'), 'messages': discord_messages(result.get('data'))}

    def save_result(self, pid, kind, result):
        # Structured source data and links are retained; agents read these files as reference data.
        if kind not in ("discord", "x"):
            raise ValueError("Unknown source type.")
        folder = self.store.files_dir(pid) / "sources" / kind
        folder.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if len(text) > 2_000_000:
            raise ValueError("Source result is too large to import.")
        (folder / f"{name}.json").write_text(text, encoding="utf-8")
        return {"file": f"sources/{kind}/{name}.json"}

    async def close(self):
        await self.mcp.close()
        await self.reach.close()
