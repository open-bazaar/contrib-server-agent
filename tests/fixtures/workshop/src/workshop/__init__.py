"""A channel and a runtime enough for an agent to run on a real node while
the pages manage it: the channel connects and takes the messages a test
leaves in its inbox, and the runtime inspects and holds each turn until the
test releases it."""
# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import time_ns
from typing import Any, ClassVar
from uuid import NAMESPACE_URL, uuid5

from bazaar_contrib_audit import AUDIT, AuditContext
from bazaar_contrib_channel import (
    CHANNEL,
    CHANNEL_BUILDERS,
    CHANNEL_INPUT,
    ChannelBuilderConfig,
    ChannelConnected,
    ChannelIdentity,
    ChannelInput,
    ChannelTarget,
    InboundMessage,
    SenderIdentity,
    channel_builder_key,
)
from bazaar_contrib_harness import (
    HARNESS,
    HARNESS_BUILDERS,
    HARNESS_INPUT,
    HarnessBuilderConfig,
    HarnessInspection,
    HarnessMcpServer,
    HarnessMcpTool,
    HarnessModel,
    HarnessOutput,
    HarnessSession,
    HarnessSessionReconciliation,
    HarnessSkill,
    HarnessTurn,
    McpAuth,
    McpFailed,
    ProviderCallResult,
    ProviderCallStatus,
    TokenUsage,
    TurnCompleted,
    TurnStarted,
    UsageUpdated,
    harness_builder_key,
)
from bazaar_core import Context, Contrib
from pydantic import BaseModel, ConfigDict, Field


class ChannelConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "x-locales": {
                "en": {
                    "bot.title": "Bot",
                    "bot.description": "The bot's name.",
                    "token.title": "Token",
                    "token.description": "The bot's token.",
                    "token.empty": "No token",
                },
                "zh-CN": {
                    "bot.title": "机器人",
                    "bot.description": "机器人的名字。",
                    "token.title": "Token",
                    "token.description": "机器人的 Token。",
                    "token.empty": "不用 Token",
                },
            }
        },
    )
    bot: str = Field(min_length=1)
    token: str | None = Field(default=None, json_schema_extra={"writeOnly": True})


def workshop(agent_id: str) -> Path:
    """Where a test talks to an agent's workshop channel and runtime: an
    inbox of messages, one a JSON file of a sender and a text, and the file
    whose presence ends a turn."""
    return Path(os.environ["BCN_HOME"]) / "workshop" / agent_id


class WorkshopChannel:
    def __init__(self, input: ChannelInput, bot: str) -> None:
        self.input = input
        self.bot = bot
        self.stopped = asyncio.Event()

    def get_identity(self) -> ChannelIdentity:
        return ChannelIdentity(id=f"{self.input.agent_id}-{self.bot}", name=self.bot)

    def backfill_provider_thread_id(
        self, provider_thread_id: str, *, channel: str, channel_identity: str
    ) -> str:
        return provider_thread_id

    def dm_address(self, request: Any) -> None:
        return None

    async def receive(self) -> AsyncIterator[InboundMessage]:
        inbox = workshop(self.input.agent_id) / "inbox"
        while not self.stopped.is_set():
            for path in sorted(await asyncio.to_thread(inbox.glob, "*.json")):
                message = json.loads(await asyncio.to_thread(path.read_text))
                await asyncio.to_thread(path.unlink)
                identity = f"workshop:{self.input.agent_id}:{message['sender']}"
                yield InboundMessage(
                    id=str(uuid5(NAMESPACE_URL, f"{identity}:{path.stem}")),
                    provider_message_id=path.stem,
                    channel_session_id=str(uuid5(NAMESPACE_URL, identity + ":channel")),
                    thread_id=str(uuid5(NAMESPACE_URL, identity + ":thread")),
                    target=ChannelTarget(kind="dm", thread_id=message["sender"]),
                    sender=SenderIdentity(id=message["sender"], name=message["sender"]),
                    body=message["text"],
                    occurred_at_ms=time_ns() // 1_000_000,
                    # a test's sender is known: its messages need no review
                    metadata={"review": "approved"},
                )
            try:
                async with asyncio.timeout(0.2):
                    await self.stopped.wait()
            except TimeoutError:
                pass

    async def prepare_send(self, request: Any) -> tuple[Any, ...]:
        raise NotImplementedError("the workshop channel sends nothing")

    async def request_approval(self, request: Any, *, timeout: float) -> Any:
        raise NotImplementedError("the workshop channel asks nothing")


@asynccontextmanager
async def channel_lifespan(ctx: Context, config: ChannelConfig):
    channel = WorkshopChannel(ctx.accept(CHANNEL_INPUT), config.bot)
    ctx.offer(CHANNEL, channel)
    await ctx.accept(AUDIT).publish(
        ChannelConnected.event_name,
        context=AuditContext(
            agent_id=channel.input.agent_id, channel="workshop-channel"
        ),
        payload=ChannelConnected(identity=channel.get_identity()).model_dump(
            mode="json"
        ),
    )
    try:
        yield
    finally:
        channel.stopped.set()


channel = Contrib(
    config=ChannelConfig,
    accepts=[CHANNEL_INPUT, AUDIT],
    offers=[CHANNEL],
    lifespan=channel_lifespan,
)


class ChannelBuilderParameters(ChannelBuilderConfig):
    kind: ClassVar[str] = "workshop-channel"
    entity_model: ClassVar[type[BaseModel]] = ChannelConfig
    entry: ClassVar[Contrib[Any]] = channel


@asynccontextmanager
async def channel_builder_lifespan(ctx: Context, config: ChannelBuilderParameters):
    with ctx.accept(CHANNEL_BUILDERS).register(config.builder()) as builder:
        ctx.offer(channel_builder_key(builder.kind), builder)
        yield


channel_builder = Contrib(
    config=ChannelBuilderParameters,
    accepts=[CHANNEL_BUILDERS],
    offers=[channel_builder_key("workshop-channel")],
    lifespan=channel_builder_lifespan,
)


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "x-locales": {
                "en": {
                    "model.title": "Model",
                    "model.description": "The model sessions start with.",
                    "model.empty": "The default model",
                    "effort.title": "Effort",
                    "effort.description": "How hard the model works.",
                    "effort.empty": "The model's default",
                    "api_key.title": "API key",
                    "api_key.description": "A key for this runtime.",
                    "api_key.empty": "Use the account signed in on the computer",
                    "fast.title": "Fast mode",
                    "fast.description": "Answer sooner, at a higher cost.",
                },
                "zh-CN": {
                    "model.title": "模型",
                    "model.description": "开会话时用的模型。",
                    "model.empty": "默认模型",
                    "effort.title": "强度",
                    "effort.description": "模型用多大力气。",
                    "effort.empty": "模型的默认强度",
                    "api_key.title": "API Key",
                    "api_key.description": "这个运行时用的 Key。",
                    "api_key.empty": "用电脑上已登录的账号",
                    "fast.title": "快速模式",
                    "fast.description": "回得更快，花费更高。",
                },
            }
        },
    )
    model: str | None = Field(default=None, json_schema_extra={"x-inspect": "models"})
    effort: str | None = Field(default=None, json_schema_extra={"x-inspect": "efforts"})
    api_key: str | None = Field(default=None, json_schema_extra={"writeOnly": True})
    fast: bool = False


class WorkshopTurn:
    """A turn that starts at once, says what its session has used so far,
    and completes when its release appears."""

    def __init__(self, turn: HarnessTurn, release: Path, turns: int) -> None:
        self.turn = turn
        self.release = release
        self.turns = turns
        self.said: list[str] = []

    def __aiter__(self) -> WorkshopTurn:
        return self

    async def __anext__(self) -> HarnessOutput:
        if not self.said:
            self.said.append("started")
            payload: TurnStarted | UsageUpdated | TurnCompleted = TurnStarted(
                event_name="workshop.started"
            )
        elif self.said == ["started"]:
            self.said.append("used")
            payload = UsageUpdated(
                total=TokenUsage(
                    input_tokens=1200 * self.turns, output_tokens=300 * self.turns
                ),
                cost_usd=0.01 * self.turns,
            )
        elif self.said == ["started", "used"]:
            # the release is a file another process writes: looked for again
            while True:
                if await asyncio.to_thread(self.release.exists):
                    break
                await asyncio.sleep(0.2)
            self.said.append("completed")
            payload = TurnCompleted(event_name="workshop.completed")
        else:
            raise StopAsyncIteration
        return HarnessOutput(
            session_id=self.turn.session_id,
            turn_id=self.turn.id,
            payload=payload,
            provider="workshop",
        )

    async def aclose(self) -> None:
        self.said.append("closed")


class WorkshopRuntime:
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.events: asyncio.Queue[Any] = asyncio.Queue()
        self.turns = 0

    async def receive_event(self) -> Any:
        return await self.events.get()

    async def start_session(
        self, request: Any, *, timeout: float
    ) -> ProviderCallResult[HarnessSession]:
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=HarnessSession(id=request.id, provider_thread_id=request.id),
        )

    async def reconcile_session(
        self, session: HarnessSession, *args: Any, **kwargs: Any
    ) -> ProviderCallResult[HarnessSessionReconciliation]:
        return ProviderCallResult(
            status=ProviderCallStatus.CONFIRMED,
            value=HarnessSessionReconciliation(session),
        )

    async def start_turn(
        self, session: HarnessSession, turn: HarnessTurn, *args: Any, **kwargs: Any
    ) -> WorkshopTurn:
        self.turns += 1
        return WorkshopTurn(turn, workshop(self.agent_id) / "release", self.turns)

    async def steer_turn(self, *args: Any, **kwargs: Any) -> bool:
        return False

    async def has_background_job(self, *args: Any, **kwargs: Any) -> bool:
        return False

    async def stop_session(
        self, session: HarnessSession, *, timeout: float
    ) -> ProviderCallResult[HarnessSession]:
        return ProviderCallResult(status=ProviderCallStatus.CONFIRMED, value=session)


@asynccontextmanager
async def runtime_lifespan(ctx: Context, config: RuntimeConfig):
    del config
    ctx.offer(HARNESS, WorkshopRuntime(ctx.accept(HARNESS_INPUT).agent_id))
    yield


runtime = Contrib(
    config=RuntimeConfig,
    accepts=[HARNESS_INPUT],
    offers=[HARNESS],
    lifespan=runtime_lifespan,
)


class RuntimeBuilderParameters(HarnessBuilderConfig):
    kind: ClassVar[str] = "workshop-runtime"
    entity_model: ClassVar[type[BaseModel]] = RuntimeConfig
    entry: ClassVar[Contrib[Any]] = runtime

    @classmethod
    async def inspect(cls, workspace: Path, *, timeout: float) -> HarnessInspection:
        return HarnessInspection(
            available=True,
            version="1.2.0",
            models=(
                HarnessModel(
                    id="workshop-large",
                    name="Workshop Large",
                    efforts=("low", "high"),
                    default_effort="low",
                    default=True,
                ),
                HarnessModel(
                    id="workshop-small", name="Workshop Small", efforts=("low",)
                ),
            ),
            skills=(
                HarnessSkill(
                    name="notes",
                    description=f"Keep notes in {workspace}",
                    source="workspace",
                ),
                HarnessSkill(
                    name="search", description="Look things up", source="personal"
                ),
                HarnessSkill(
                    name="settings",
                    description="Change the runtime's own settings",
                    source="system",
                ),
            ),
            mcp_servers=(
                HarnessMcpServer(
                    name="notes",
                    auth=McpAuth.NONE,
                    tools=tuple(
                        HarnessMcpTool(name=name, input_schema={"type": "object"})
                        for name in (
                            "save_note",
                            "list_notes",
                            "codex_document_control.execute_document_command",
                        )
                    ),
                ),
                HarnessMcpServer(
                    name="github",
                    auth=McpAuth.SIGNED_OUT,
                    tools=tuple(
                        HarnessMcpTool(
                            name=f"tool_{n}", input_schema={"type": "object"}
                        )
                        for n in range(12)
                    ),
                ),
                HarnessMcpServer(
                    name="search",
                    connection=McpFailed(reason="Connection closed"),
                ),
            ),
        )


@asynccontextmanager
async def runtime_builder_lifespan(ctx: Context, config: RuntimeBuilderParameters):
    with ctx.accept(HARNESS_BUILDERS).register(config.builder()) as builder:
        ctx.offer(harness_builder_key(builder.kind), builder)
        yield


runtime_builder = Contrib(
    config=RuntimeBuilderParameters,
    accepts=[HARNESS_BUILDERS],
    offers=[harness_builder_key("workshop-runtime")],
    lifespan=runtime_builder_lifespan,
)
