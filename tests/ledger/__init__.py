"""A module putting tabs and activity sections on an agent's page, the way
the activity and conversation modules do, and reading the agents an
account may see through AGENTS."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy from collections.abc import AsyncIterator, Mapping
lazy from importlib.resources import files
lazy from pathlib import Path

from bazaar_contrib_server import SERVER, Account, Topic
from bazaar_core import Context, Contrib
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from bazaar_contrib_server_agent import AGENTS, AgentSection, AgentTab

ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="12" r="9"/></svg>'


@asynccontextmanager
async def _ledger(ctx: Context) -> AsyncIterator[None]:
    server = ctx.accept(SERVER)
    agents = ctx.accept(AGENTS)
    pages = await server.pages("ledger", "ledger")
    locales = (
        (await asyncio.to_thread(lambda: Path(str(files("ledger")))))
        / "resources"
        / "locales"
    )

    async def page(request: Request) -> Response:
        # a tab's or a section's page, for the agent its address names
        return await pages.render(
            request,
            "ledger/page.html",
            name=request.path_params["name"],
            ref=request.path_params["ref"],
        )

    async def listed(request: Request) -> Response:
        return JSONResponse(
            [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "computer": agent.computer_id,
                    "online": agent.online,
                    "channels": len(agent.config.channel),
                    "state": None if agent.activity is None else agent.activity.state,
                }
                for agent in await agents.agents(request.state.account)
            ]
        )

    async def one(request: Request) -> Response:
        agent = await agents.agent(
            request.state.account, request.path_params["agent_id"]
        )
        return JSONResponse(None if agent is None else {"name": agent.name})

    async def counted(
        account: Account, scope: Mapping[str, str]
    ) -> Mapping[str, object] | None:
        del account, scope
        return {"text": "12", "kind": "count"}

    async with (
        server.module(
            "ledger",
            ui=pages.application(
                [
                    Route("/agents", listed),
                    Route("/agents/{agent_id}", one),
                    Route("/{name}/{ref}", page),
                ]
            ),
            locales=locales,
        ),
        server.topic(Topic(name="ledger.count", fetch=counted, fields=("agent",))),
        agents.tab(
            AgentTab(
                key="chats",
                text="ledger.chats",
                icon=ICON,
                src="/ui/ledger/chats",
                permission="agents.view",
                order=20,
                count="ledger.count",
            )
        ),
        agents.tab(
            AgentTab(
                key="ledger",
                text="ledger.ledger",
                icon=ICON,
                src="/ui/ledger/ledger",
                order=45,
            )
        ),
        # the whole record, opened from the activity and under it
        agents.tab(
            AgentTab(
                key="log",
                text="ledger.log",
                icon=ICON,
                src="/ui/ledger/log",
                order=60,
                parent="activity",
            )
        ),
        agents.section(
            AgentSection(key="turns", src="/ui/ledger/turns", column="main", order=20)
        ),
        agents.section(
            AgentSection(key="live", src="/ui/ledger/live", column="main", order=10)
        ),
        agents.section(
            AgentSection(key="usage", src="/ui/ledger/usage", column="side", order=20)
        ),
        agents.section(
            AgentSection(
                key="notes",
                src="/ui/ledger/notes",
                column="side",
                permission="agents.view",
                order=30,
                narrow=False,
            )
        ),
    ):
        yield


ledger = Contrib(accepts=[SERVER, AGENTS], lifespan=_ledger)
