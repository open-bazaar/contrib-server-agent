"""What a computer's node answers about its agents, as agent-configuration
and agent-activity give it."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue


class _Answer(BaseModel):
    model_config = ConfigDict(frozen=True)


class Provider(_Answer):
    """A channel or runtime of an agent: its values, and the names of the
    credentials it has, never their values."""

    kind: str
    connection: str
    values: dict[str, JsonValue]
    secrets: tuple[str, ...]


class Sandbox(_Answer):
    policy: Literal["workspace-write", "danger-full-access"] = "workspace-write"


class SessionConfig(_Answer):
    sandbox: Sandbox = Sandbox()


class Session(_Answer):
    config: SessionConfig = SessionConfig()


class AgentView(_Answer):
    id: str
    name: str
    mode: Literal["session", "dangerous_individual"]
    session: Session | None
    idle_timeout: float
    channel: tuple[Provider, ...]
    harness: tuple[Provider, ...]


class Listing(_Answer):
    revision: str | None
    agents: tuple[AgentView, ...]


class Schema(_Answer):
    kind: str
    json_schema: dict[str, JsonValue]


class Providers(_Answer):
    channel: tuple[Schema, ...]
    harness: tuple[Schema, ...]


class Usage(_Answer):
    input: int = 0
    output: int = 0
    cached: int = 0
    cost: float | None = None


class Working(_Answer):
    since_ms: int
    conversation: str | None


class Identity(_Answer):
    id: str
    name: str | None = None


class ChannelStatus(_Answer):
    kind: str
    connection: str
    state: str
    identity: Identity | None
    connected_at_ms: int | None
    received_at_ms: int | None
    failures: int
    error: str | None


class RuntimeStatus(_Answer):
    kind: str
    connection: str
    state: str
    in_use: bool
    banned_until_ms: int | None


class Activity(_Answer):
    state: str
    reason: str | None
    usage: Usage
    event: str | None
    event_at_ms: int | None
    started_at_ms: int | None
    working: Working | None
    channels: tuple[ChannelStatus, ...]
    runtimes: tuple[RuntimeStatus, ...]


class Model(_Answer):
    id: str
    name: str
    efforts: tuple[str, ...] = ()
    default_effort: str | None = None
    default: bool = False


class Skill(_Answer):
    name: str
    description: str
    source: str


class Tool(_Answer):
    name: str
    description: str | None = None


class McpFailed(_Answer):
    """A runtime could not connect to the server, and why."""

    reason: str


class McpServer(_Answer):
    """An MCP server as a runtime sees it, in the common states; what the
    runtime does not say is left out."""

    name: str
    connection: Literal["connected", "connecting", "disabled"] | McpFailed | None = None
    auth: Literal["none", "signed_in", "signed_out"] | None = None
    tools: tuple[Tool, ...] = ()


class Inspection(_Answer):
    available: bool
    version: str | None = None
    models: tuple[Model, ...] = ()
    skills: tuple[Skill, ...] = ()
    mcp_servers: tuple[McpServer, ...] = ()
    error: str | None = None


class Entry(_Answer):
    name: str
    directory: bool
    size: int
    modified_at_ms: int


class Directory(_Answer):
    root: str
    path: str
    total: int
    entries: tuple[Entry, ...]


class File(_Answer):
    path: str
    size: int
    modified_at_ms: int
    content: str | None
