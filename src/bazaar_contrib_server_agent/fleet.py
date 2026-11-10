"""What each computer's node says about its agents: the list from
agent-configuration, kept in the store, and their state from
agent-activity; asked while a page shows the computer or a module reads
the agents, once what was said is a minute old, the state again as soon
as the computer reports a turn or a connection, once more when it
reports one while an answer is on its way, and what a runtime reports
kept a minute for each agent and kind."""

from __future__ import annotations

from dataclasses import dataclass
lazy import asyncio
lazy import logging
lazy from time import time_ns

from pydantic import TypeAdapter, ValidationError
lazy from bazaar_contrib_server_computer import Computers, ComputerSummary
lazy from bazaar_contrib_sidecar import RemoteError

from .models import Activity, Inspection, Listing, Providers
lazy from .store import Store

# what was said is shown for a minute before the node is asked again
FRESH_MS = 60_000
# what a computer reports that changes the state of one of its agents
CHANGES = frozenset(
    {
        "harness.started",
        "harness.completed",
        "harness.failed",
        "harness.cancelled",
        "channel.connection.connected",
        "channel.connection.failed",
    }
)

_ACTIVITY = TypeAdapter(dict[str, Activity])


def _now() -> int:
    return time_ns() // 1_000_000


@dataclass(frozen=True, slots=True)
class Inspected:
    """What a runtime reported, and when it was asked."""

    inspection: Inspection
    at_ms: int


class Fleet:
    def __init__(
        self, computers: Computers, store: Store, tasks: asyncio.TaskGroup
    ) -> None:
        self._computers = computers
        self._store = store
        self._tasks = tasks
        self._listed: dict[str, tuple[int, str | None]] = {}
        self._activity: dict[str, tuple[int, dict[str, Activity]]] = {}
        self._inspected: dict[tuple[str, str | None, str], Inspected] = {}
        self._providers: dict[str, tuple[int, Providers]] = {}
        self._asking: dict[tuple[str, str], asyncio.Task[None]] = {}
        # computers that reported a change while their state was being asked
        self._again: set[str] = set()

    async def looked(self) -> list[ComputerSummary]:
        """Every computer, those online asked again about what their agents
        are once that is a minute old: a computer just connected has none
        kept yet."""
        everyone = list(await self._computers.computers(None))
        for computer in everyone:
            self.look(computer.id, computer.online)
        return everyone

    def look(self, computer_id: str, online: bool) -> None:
        """A page shows the computer's agents: one online is asked again
        about what is a minute old."""
        if not online:
            return
        now = _now()
        listed = self._listed.get(computer_id)
        if listed is None or now - listed[0] >= FRESH_MS:
            self._ask(computer_id, "list")
        known = self._activity.get(computer_id)
        if known is None or now - known[0] >= FRESH_MS:
            self._ask(computer_id, "activity")

    def reported(self, computer_id: str, event_name: str) -> None:
        """A change the computer reports: its state is asked again, and asked
        once more after an answer on its way, which may have been taken
        before the change."""
        if event_name not in CHANGES:
            return
        if (computer_id, "activity") in self._asking:
            self._again.add(computer_id)
        else:
            self._ask(computer_id, "activity")

    def revision(self, computer_id: str) -> str | None:
        """The revision of the configuration the computer last gave its list
        from, which changes as any of its agents is changed."""
        listed = self._listed.get(computer_id)
        return None if listed is None else listed[1]

    def activity(self, computer_id: str) -> dict[str, Activity]:
        """The state of the computer's agents as it last said, when it did."""
        known = self._activity.get(computer_id)
        return {} if known is None else known[1]

    def working(self) -> list[tuple[str, str, Activity]]:
        """The agents at work as their computers last said: their computer,
        their id and how they are."""
        return [
            (computer_id, agent_id, activity)
            for computer_id, (_, agents) in self._activity.items()
            for agent_id, activity in agents.items()
            if activity.working is not None
        ]

    def said(self) -> dict[str, str]:
        """How each agent is as its computer last said, today's usage with
        it, put shortly enough to tell when it changes."""
        return {
            agent_id: "/".join(
                (
                    activity.state,
                    "" if activity.working is None else str(activity.working.since_ms),
                    *(channel.state for channel in activity.channels),
                    *(runtime.state for runtime in activity.runtimes),
                    activity.usage.model_dump_json(),
                )
            )
            for _, agents in self._activity.values()
            for agent_id, activity in agents.items()
        }

    def _ask(self, computer_id: str, what: str) -> None:
        if (computer_id, what) not in self._asking:
            self._asking[computer_id, what] = self._tasks.create_task(
                self._answer(computer_id, what)
            )

    async def _answer(self, computer_id: str, what: str) -> None:
        try:
            if what == "list":
                await self.listing(computer_id)
            else:
                again = True
                while again:
                    self._again.discard(computer_id)
                    value = await self._computers.call(
                        computer_id, "agent-activity", "agents"
                    )
                    self._activity[computer_id] = (
                        _now(),
                        _ACTIVITY.validate_python(value),
                    )
                    again = computer_id in self._again
        except (RemoteError, TimeoutError, ValidationError) as error:
            # a node that does not answer, or answers otherwise, keeps what
            # it said before
            logging.getLogger(__name__).info("agents.unanswered: %r", error)
        finally:
            del self._asking[computer_id, what]

    async def listing(self, computer_id: str) -> Listing:
        """The computer's agents as it gives them now, kept for when it is away."""
        asked = _now()
        listing = Listing.model_validate(
            await self._computers.call(computer_id, "agent-configuration", "list")
        )
        now = _now()
        await self._store.replace(computer_id, listing.agents, asked, now)
        self._listed[computer_id] = (now, listing.revision)
        return listing

    async def inspect(
        self, computer_id: str, kind: str, agent_id: str | None = None
    ) -> Inspected:
        """What the runtime of the kind reports in the agent's workspace, or
        in an empty one, asked again once a minute old."""
        key = (computer_id, agent_id, kind)
        known = self._inspected.get(key)
        if known is not None and _now() - known.at_ms < FRESH_MS:
            return known
        inspected = Inspected(
            inspection=Inspection.model_validate(
                await self._computers.call(
                    computer_id, "agent-configuration", "inspect", kind, agent_id
                )
            ),
            at_ms=_now(),
        )
        self._inspected[key] = inspected
        return inspected

    async def providers(self, computer_id: str) -> Providers:
        """The channels and runtimes the computer can run, with the schemas
        their forms are drawn from, asked again once a minute old."""
        known = self._providers.get(computer_id)
        if known is not None and _now() - known[0] < FRESH_MS:
            return known[1]
        providers = Providers.model_validate(
            await self._computers.call(computer_id, "agent-configuration", "providers")
        )
        self._providers[computer_id] = (_now(), providers)
        return providers

    async def removed(self, computer_id: str) -> None:
        """The computer is gone, and its agents with it."""
        now = _now()
        await self._store.replace(computer_id, (), now, now)
        self.forget(computer_id)

    def forget(self, computer_id: str) -> None:
        self._listed.pop(computer_id, None)
        self._activity.pop(computer_id, None)
        self._providers.pop(computer_id, None)
        self._again.discard(computer_id)
        for key in [key for key in self._inspected if key[0] == computer_id]:
            del self._inspected[key]

    def close(self) -> None:
        """Stop asking: the server is going down."""
        for task in self._asking.values():
            task.cancel()
