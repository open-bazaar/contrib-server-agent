"""The agents of the Bazaar Compute server: every agent on its computers,
their pages, who may see and change them, and AGENTS for the modules that
put tabs and activity sections on an agent's page or read the agents."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy import asyncio
lazy from collections.abc import AsyncIterator
lazy from importlib.resources import files
lazy from pathlib import Path

from bazaar_contrib_server import (
    SERVER,
    PermissionItem,
    SidebarItem,
    SidebarPanel,
    Topic,
)
from bazaar_contrib_server_access import ACCESS, Shared
from bazaar_contrib_server_computer import (
    COMPUTER_REMOVED,
    COMPUTERS,
    REPORTED,
    ComputerRemoved,
    Reported,
)
from bazaar_contrib_server_database import SERVER_DB, SERVER_SCHEMA
from bazaar_core import Context, Contrib, ServiceKey

from .contracts import AGENTS, Agent, Agents, AgentSection, AgentTab
from .models import Activity, AgentView
lazy from .fleet import Fleet
lazy from .pages import CREATE, DELETE, UPDATE, VIEW, Views
lazy from .schema import MODULE
lazy from .service import Service
lazy from .store import Store

__all__ = [
    "AGENTS",
    "Activity",
    "Agent",
    "AgentSection",
    "AgentTab",
    "AgentView",
    "Agents",
    "agent",
]

# the computers' agents, for what the computers report and remove
_FLEET = ServiceKey["Fleet"]("server.agents.fleet")


@asynccontextmanager
async def _agent(ctx: Context) -> AsyncIterator[None]:
    server = ctx.accept(SERVER)
    access = ctx.accept(ACCESS)
    computers = ctx.accept(COMPUTERS)
    resources = Path(str(files(__name__))) / "resources"
    icon = await asyncio.to_thread(
        (resources / "templates" / "agents" / "icons" / "bot.svg").read_text,
        encoding="utf-8",
    )
    async with (
        ctx.accept(SERVER_SCHEMA).register(MODULE),
        asyncio.TaskGroup() as tasks,
    ):
        store = Store(ctx.accept(SERVER_DB), access)
        fleet = Fleet(computers, store, tasks)
        service = Service(access, computers, store, fleet)
        pages = await server.pages("agents", __name__)
        views = Views(server, pages, access, computers, store, fleet, service)
        async with (
            server.module(
                "agents",
                ui=pages.application(views.routes),
                static=resources / "static",
                locales=resources / "locales",
            ),
            server.permission(
                PermissionItem(point=VIEW, description="agents.permission.view")
            ),
            server.permission(
                PermissionItem(point=CREATE, description="agents.permission.create")
            ),
            server.permission(
                PermissionItem(point=UPDATE, description="agents.permission.update")
            ),
            server.permission(
                PermissionItem(point=DELETE, description="agents.permission.delete")
            ),
            server.sidebar(
                SidebarItem(
                    text="agents.title",
                    icon=icon,
                    path="/agents",
                    group="server.sidebar.workbench",
                    permission=VIEW,
                    order=20,
                    badge="agents.count",
                )
            ),
            server.panel(
                SidebarPanel(
                    key="agents-working",
                    src="/ui/agents/working",
                    group="server.sidebar.workbench",
                    permission=VIEW,
                    order=21,
                )
            ),
            server.topic(
                Topic(name="agents.count", fetch=views.counted, permission=VIEW)
            ),
            server.topic(
                Topic(name="agents.list", fetch=views.listed, permission=VIEW)
            ),
            server.topic(
                Topic(name="agents.working", fetch=views.worked, permission=VIEW)
            ),
            server.topic(
                Topic(
                    name="agents.agent",
                    fetch=views.followed,
                    fields=("agent",),
                    permission=VIEW,
                )
            ),
            access.shared(
                Shared(
                    kind="agent",
                    permission=VIEW,
                    points=(VIEW, UPDATE, DELETE),
                    name=views.name,
                )
            ),
            # who may create agents on a computer is shared with it, as 0.2 did
            access.points("computer", (CREATE,)),
            # how each channel is connected, beside the activity on a wide screen
            service.section(
                AgentSection(
                    key="status",
                    src="/ui/agents/status",
                    column="side",
                    permission=VIEW,
                    order=10,
                    narrow=False,
                )
            ),
        ):
            ctx.offer(_FLEET, fleet)
            ctx.offer(AGENTS, service)
            try:
                yield
            finally:
                fleet.close()


def _reported(ctx: Context, value: Reported) -> None:
    ctx.accept(_FLEET).reported(value.computer_id, value.event.event_name)


async def _removed(ctx: Context, value: ComputerRemoved) -> None:
    await ctx.accept(_FLEET).removed(value.computer_id)


agent = Contrib(
    accepts=[SERVER, ACCESS, COMPUTERS, SERVER_DB, SERVER_SCHEMA],
    offers=[_FLEET, AGENTS],
    lifespan=_agent,
    listen={REPORTED: _reported, COMPUTER_REMOVED: _removed},
)
