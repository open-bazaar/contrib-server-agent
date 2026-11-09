"""AGENTS as one service: the tabs and activity sections other modules put
on an agent's page, and the agents an account may see, as their computers
last gave them."""

from __future__ import annotations

from contextlib import asynccontextmanager
lazy from collections.abc import AsyncIterator, Sequence

lazy from bazaar_contrib_server import Account
lazy from bazaar_contrib_server_access import Access
lazy from bazaar_contrib_server_computer import Computers, ComputerSummary

lazy from .contracts import Agent, AgentSection, AgentTab
lazy from .fleet import Fleet
lazy from .pages import TABS, VIEW
lazy from .store import Kept, Store


class Service:
    def __init__(
        self, access: Access, computers: Computers, store: Store, fleet: Fleet
    ) -> None:
        self._access = access
        self._computers = computers
        self._store = store
        self._fleet = fleet
        # what other modules put on an agent's page, as they registered it
        self.tabs: list[AgentTab] = []
        self.sections: list[AgentSection] = []

    @asynccontextmanager
    async def tab(self, tab: AgentTab) -> AsyncIterator[None]:
        if tab.key in TABS or any(
            registered.key == tab.key for registered in self.tabs
        ):
            raise ValueError(f"agent tab {tab.key!r} is already there")
        self.tabs.append(tab)
        try:
            yield
        finally:
            self.tabs.remove(tab)

    @asynccontextmanager
    async def section(self, section: AgentSection) -> AsyncIterator[None]:
        if any(registered.key == section.key for registered in self.sections):
            raise ValueError(f"activity section {section.key!r} is already there")
        self.sections.append(section)
        try:
            yield
        finally:
            self.sections.remove(section)

    async def agents(self, account: Account) -> Sequence[Agent]:
        computers = await self._fleet.looked()
        return self._made(
            await self._store.every(await self._access.targets(account, "agent", VIEW)),
            computers,
        )

    async def agent(self, account: Account, agent_id: str) -> Agent | None:
        if not await self._access.can(account, "agent", agent_id, VIEW):
            return None
        kept = await self._store.get(agent_id)
        if kept is None:
            return None
        computers = await self._computers.computers([kept.computer_id])
        for computer in computers:
            self._fleet.look(computer.id, computer.online)
        (agent,) = self._made([kept], computers)
        return agent

    def _made(
        self, kept: Sequence[Kept], computers: Sequence[ComputerSummary]
    ) -> list[Agent]:
        """The agents as their computers last gave them, and whether those
        are here."""
        online = {computer.id for computer in computers if computer.online}
        return [
            Agent(
                id=item.agent.id,
                computer_id=item.computer_id,
                name=item.agent.name,
                online=item.computer_id in online,
                config=item.agent,
                activity=self._fleet.activity(item.computer_id).get(item.agent.id),
            )
            for item in kept
        ]
