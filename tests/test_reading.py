"""What AGENTS gives other modules, and how each of an agent's channels is
connected, over real HTTP with a real node."""

from __future__ import annotations

lazy import asyncio
lazy import re
lazy from pathlib import Path
lazy from typing import Any

import httpx
from bazaar_core import Composition, ContribEntry
from computer import computer
from ledger import ledger
from served import Served, admin, client, role, serving, through
from test_sharing import listed

KANA = {
    "id": "kana",
    "name": "有马佳奈",
    "channel": [
        {"kind": "workshop-channel", "bot": "kana-bot"},
        {"kind": "workshop-channel", "bot": "kana-desk"},
    ],
    "harness": [{"kind": "workshop-runtime"}],
}
AQUA = {
    "id": "aqua",
    "name": "啊夸",
    "channel": [{"kind": "workshop-channel", "bot": "aqua-bot"}],
    "harness": [{"kind": "workshop-runtime"}],
}


async def shown(served: Served, session: httpx.AsyncClient, ref: str) -> str:
    """The agent's status section once its computer said how its channels
    are."""
    page = ""
    async with asyncio.timeout(45):
        while "kana-desk" not in page:
            page = (await session.get(f"{served.url}/ui/agents/status/{ref}")).text
            await asyncio.sleep(0.5)
    return page


async def given(served: Served, session: httpx.AsyncClient) -> list[dict[str, Any]]:
    """What AGENTS gives once the computer said what its two agents are and
    how they are, read as a module would with no page asking."""
    agents: list[dict[str, Any]] = []
    async with asyncio.timeout(45):
        while len(agents) < 2 or any(agent["state"] is None for agent in agents):
            await asyncio.sleep(0.5)
            agents = (await session.get(f"{served.url}/ui/ledger/agents")).json()
    return agents


async def test_agents_are_read_as_the_account_sees_them(tmp_path: Path):
    async with serving() as served, client() as boss, client() as bob:
        await admin(served, boss)
        viewers = await role(served, boss, "agents.view")
        await boss.post(f"{served.url}{viewers}/default")
        await through(served, bob, "bob")
        role_id = viewers.rsplit("/", 1)[1]
        async with (
            computer(served, boss, tmp_path / "kana-vm", "kana-vm", [KANA, AQUA]),
            served.hub.enable(
                Composition(entries=[ContribEntry(name="ledger", definition=ledger)])
            ),
        ):
            # the computer just connected has none kept: reading asks it
            everyone = await given(served, boss)
            assert {agent["name"] for agent in everyone} == {"有马佳奈", "啊夸"}
            kept = next(agent for agent in everyone if agent["id"] == "kana")
            assert kept["online"]
            assert kept["channels"] == 2
            table = await listed(served, boss, "aqua-bot")
            kana = re.search(r'href="/ui/agents/(\d+)"><b>有马佳奈</b>', table)
            assert kana, table[:3000]
            await boss.post(
                f"{served.url}/ui/access/shares/agent/{kana.group(1)}",
                data={"roles": [role_id], "grant": [f"{role_id} agents.view"]},
            )
            # shared one of the two, bob reads that one alone
            seen = (await bob.get(f"{served.url}/ui/ledger/agents")).json()
            assert [agent["name"] for agent in seen] == ["有马佳奈"]
            assert (await bob.get(f"{served.url}/ui/ledger/agents/aqua")).json() is None
            assert (await boss.get(f"{served.url}/ui/ledger/agents/aqua")).json() == {
                "name": "啊夸"
            }


async def test_the_status_section_has_a_group_a_channel(tmp_path: Path):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with computer(served, boss, tmp_path / "kana-vm", "kana-vm", [KANA]):
            table = await listed(served, boss, "kana-bot")
            kana = re.search(r'href="/ui/agents/(\d+)"><b>有马佳奈</b>', table)
            assert kana, table[:3000]
            section = await shown(served, boss, kana.group(1))
            groups = section.split('<div class="chan">')[1:]
            assert len(groups) == 2, section[:3000]
            assert "kana-bot" in groups[0]
            assert "kana-desk" in groups[1]
            # each connected, and since when on the viewer's day
            for group in groups:
                assert 'class="state ok"' in group
                assert "Connected today" in group
