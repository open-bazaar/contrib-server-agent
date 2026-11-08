"""The agents each computer gave last: kept as they came, paged in the order
of their computer and their id, and dropped with their owners and shares
once no computer has them any more."""

from __future__ import annotations

from dataclasses import dataclass
lazy from collections.abc import Sequence
lazy from typing import cast

lazy from bazaar_contrib_server_access import Access
lazy from bazaar_contrib_storage import (
    All,
    AnyOf,
    Call,
    Column,
    Compare,
    Conflict,
    Contains,
    Database,
    Delete,
    In,
    Insert,
    Predicate,
    Record,
    Scalar,
    Select,
    Session,
    Sort,
    Value,
)

lazy from .models import AgentView

# what one page of the list holds
PAGE = 50


@dataclass(frozen=True, slots=True)
class Kept:
    """An agent as its computer last gave it, and when."""

    computer_id: str
    agent: AgentView
    fetched_ms: int


def _kept(row: Record) -> Kept:
    return Kept(
        computer_id=cast(str, row["computer_id"]),
        agent=AgentView.model_validate_json(cast(str, row["config"])),
        fetched_ms=cast(int, row["fetched_at_ms"]),
    )


def _equals(column: str, value: str) -> Compare:
    return Compare(left=Column(name=column), operator="=", right=Value(value=value))


def _row(computer_id: str, agent: AgentView, fetched_ms: int) -> dict[str, Scalar]:
    return {
        "computer_id": computer_id,
        "agent_id": agent.id,
        "name": agent.name,
        "config": agent.model_dump_json(),
        "fetched_at_ms": fetched_ms,
    }


class Store:
    def __init__(self, database: Database, access: Access) -> None:
        self._database = database
        self._access = access

    async def _gone(self, session: Session, agent_ids: Sequence[str]) -> None:
        """Forget who owns and shares the agents no computer has any more."""
        if not agent_ids:
            return
        still = await session.select(
            Select(
                table="agents",
                columns={"agent_id": Column(name="agent_id")},
                where=In(expression=Column(name="agent_id"), values=tuple(agent_ids)),
            )
        )
        held = {cast(str, row["agent_id"]) for row in still}
        for agent_id in agent_ids:
            if agent_id not in held:
                await self._access.forget(session, "agent", agent_id)

    async def replace(
        self,
        computer_id: str,
        agents: Sequence[AgentView],
        asked_ms: int,
        fetched_ms: int,
    ) -> None:
        """The computer's agents as it gave them when asked, in place of those
        it gave before; one stored since it was asked, made or changed
        meanwhile, is newer than the answer and stays as it is."""

        async def replace(session: Session) -> None:
            before = await session.select(
                Select(
                    table="agents",
                    columns={
                        "agent_id": Column(name="agent_id"),
                        "fetched_at_ms": Column(name="fetched_at_ms"),
                    },
                    where=_equals("computer_id", computer_id),
                )
            )
            newer = {
                cast(str, row["agent_id"])
                for row in before
                if cast(int, row["fetched_at_ms"]) >= asked_ms
            }
            named = {agent.id for agent in agents}
            dropped = [
                agent_id
                for row in before
                if (agent_id := cast(str, row["agent_id"])) not in named
                and agent_id not in newer
            ]
            given = [agent for agent in agents if agent.id not in newer]
            if dropped:
                await session.delete(
                    Delete(
                        table="agents",
                        where=All(
                            conditions=(
                                _equals("computer_id", computer_id),
                                In(
                                    expression=Column(name="agent_id"),
                                    values=tuple(dropped),
                                ),
                            )
                        ),
                    )
                )
            if given:
                await session.insert(
                    Insert(
                        table="agents",
                        rows=tuple(
                            _row(computer_id, agent, fetched_ms) for agent in given
                        ),
                        conflict=Conflict(
                            columns=("computer_id", "agent_id"),
                            update=("name", "config", "fetched_at_ms"),
                        ),
                    )
                )
            await self._gone(session, dropped)

        await self._database.transaction(replace)

    async def add(
        self, computer_id: str, agent: AgentView, fetched_ms: int, owner_id: str
    ) -> None:
        """An agent made from a page, owned by whoever made it."""

        async def add(session: Session) -> None:
            await session.insert(
                Insert(
                    table="agents",
                    rows=(_row(computer_id, agent, fetched_ms),),
                    conflict=Conflict(
                        columns=("computer_id", "agent_id"),
                        update=("name", "config", "fetched_at_ms"),
                    ),
                )
            )
            await self._access.own(session, owner_id, "agent", agent.id)

        await self._database.transaction(add)

    async def remove(self, computer_id: str, agent_id: str) -> None:
        async def remove(session: Session) -> None:
            await session.delete(
                Delete(
                    table="agents",
                    where=All(
                        conditions=(
                            _equals("computer_id", computer_id),
                            _equals("agent_id", agent_id),
                        )
                    ),
                )
            )
            await self._gone(session, [agent_id])

        await self._database.transaction(remove)

    async def get(self, agent_id: str) -> Kept | None:
        rows = await self._database.read(
            lambda session: session.select(
                Select(
                    table="agents",
                    where=_equals("agent_id", agent_id),
                    order_by=(
                        Sort(expression=Column(name="fetched_at_ms"), descending=True),
                    ),
                    limit=1,
                )
            )
        )
        return _kept(rows[0]) if rows else None

    async def many(self, agent_ids: Sequence[str]) -> dict[str, Kept]:
        """The agents given, as their computers last gave them."""
        if not agent_ids:
            return {}
        rows = await self._database.read(
            lambda session: session.select(
                Select(
                    table="agents",
                    where=In(
                        expression=Column(name="agent_id"), values=tuple(agent_ids)
                    ),
                )
            )
        )
        return {cast(str, row["agent_id"]): _kept(row) for row in rows}

    async def page(
        self,
        visible: frozenset[str] | None,
        *,
        computer_id: str | None = None,
        name: str | None = None,
        after: tuple[str, str] | None = None,
    ) -> tuple[list[Kept], bool]:
        """The agents the account may see, of one computer or all, whose names
        hold the text: a page past the one named, and whether more follow."""
        if visible is not None and not visible:
            return [], False
        conditions: list[Predicate] = []
        if visible is not None:
            conditions.append(
                In(expression=Column(name="agent_id"), values=tuple(visible))
            )
        if computer_id is not None:
            conditions.append(_equals("computer_id", computer_id))
        if name:
            conditions.append(
                Contains(
                    expression=Call(name="lower", arguments=(Column(name="name"),)),
                    text=name.lower(),
                )
            )
        if after is not None:
            conditions.append(
                AnyOf(
                    conditions=(
                        Compare(
                            left=Column(name="computer_id"),
                            operator=">",
                            right=Value(value=after[0]),
                        ),
                        All(
                            conditions=(
                                _equals("computer_id", after[0]),
                                Compare(
                                    left=Column(name="agent_id"),
                                    operator=">",
                                    right=Value(value=after[1]),
                                ),
                            )
                        ),
                    )
                )
            )
        rows = await self._database.read(
            lambda session: session.select(
                Select(
                    table="agents",
                    where=All(conditions=tuple(conditions)) if conditions else None,
                    order_by=(
                        Sort(expression=Column(name="computer_id")),
                        Sort(expression=Column(name="agent_id")),
                    ),
                    limit=PAGE + 1,
                )
            )
        )
        return [_kept(row) for row in rows[:PAGE]], len(rows) > PAGE

    async def count(self, visible: frozenset[str] | None) -> int:
        if visible is not None and not visible:
            return 0
        rows = await self._database.read(
            lambda session: session.select(
                Select(
                    table="agents",
                    columns={
                        "n": Call(name="count", arguments=(Column(name="agent_id"),))
                    },
                    where=None
                    if visible is None
                    else In(expression=Column(name="agent_id"), values=tuple(visible)),
                )
            )
        )
        return cast(int, rows[0]["n"])

    async def computers(self, visible: frozenset[str] | None) -> list[str]:
        """The computers holding the agents the account may see."""
        if visible is not None and not visible:
            return []
        rows = await self._database.read(
            lambda session: session.select(
                Select(
                    table="agents",
                    columns={"computer_id": Column(name="computer_id")},
                    where=None
                    if visible is None
                    else In(expression=Column(name="agent_id"), values=tuple(visible)),
                    distinct=True,
                )
            )
        )
        return [cast(str, row["computer_id"]) for row in rows]
