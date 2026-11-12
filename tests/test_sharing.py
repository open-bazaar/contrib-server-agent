"""Who may see, change, make and share agents, over real HTTP with a real
node: the points a role holds and what is shared with it."""

from __future__ import annotations

lazy import asyncio
lazy import json
lazy import re
lazy from pathlib import Path

import httpx
from computer import agents_on, computer
from served import Served, admin, client, role, seen, serving, through, told

KANA = {
    "id": "kana",
    "name": "有马佳奈",
    "channel": [{"kind": "workshop-channel", "bot": "kana-bot"}],
    "harness": [{"kind": "workshop-runtime"}],
}


async def listed(served: Served, session: httpx.AsyncClient, text: str) -> str:
    """The list once it shows the text; its rows as they are then."""
    table = None
    try:
        async with asyncio.timeout(45):
            while True:
                table = await session.get(f"{served.url}/ui/agents/table")
                if text in table.text:
                    return table.text
                await asyncio.sleep(1)
    except TimeoutError:
        raise AssertionError(table.text if table else "no table") from None


def hidden(page: str, name: str) -> str:
    """A hidden field of the page's form, as the browser would send it."""
    found = re.search(rf"name=\"{name}\" value='([^']*)'", page) or re.search(
        rf'name="{name}" value="([^"]*)"', page
    )
    assert found, page[:3000]
    return found.group(1)


async def test_the_agents_a_computer_brings_are_its_owners(tmp_path: Path):
    async with serving() as served, client() as boss, client() as carol:
        await admin(served, boss)
        keepers = await role(
            served, boss, "agents.view", "computers.view", "computers.create"
        )
        await boss.post(f"{served.url}{keepers}/default")
        await through(served, carol, "carol")
        async with computer(served, carol, tmp_path / "kana-vm", "kana-vm", [KANA]):
            await listed(served, carol, "有马佳奈")


async def test_a_role_shared_viewing_sees_an_agent_and_shared_changing_saves_it(
    tmp_path: Path,
):
    async with serving() as served, client() as boss, client() as bob:
        await admin(served, boss)
        changers = await role(served, boss, "agents.view", "agents.update")
        await boss.post(f"{served.url}{changers}/default")
        await through(served, bob, "bob")
        given = changers.rsplit("/", 1)[1]
        home = tmp_path / "kana-vm"
        async with computer(served, boss, home, "kana-vm", [KANA]):
            table = await listed(served, boss, "kana-bot")
            found = re.search(r'href="/ui/agents/([^"?/]+)"', table)
            assert found, table
            kana = found.group(1)
            sharing = f"{served.url}/ui/access/shares/agent/{kana}"
            await boss.post(
                sharing, data={"roles": [given], "grant": [f"{given} agents.view"]}
            )
            viewer_list = await listed(served, bob, "有马佳奈")
            assert f"/ui/agents/{kana}/share" not in viewer_list
            assert f"/ui/agents/{kana}/remove" not in viewer_list
            assert ">Open</a>" in viewer_list
            # shared for viewing alone, its configuration is not bob's to save
            refused = await bob.post(
                f"{served.url}/ui/agents/{kana}/config", data={"action": "save"}
            )
            assert refused.status_code == 404
            await boss.post(
                sharing,
                data={
                    "roles": [given],
                    "grant": [f"{given} agents.view", f"{given} agents.update"],
                },
            )
            tab = (await bob.get(f"{served.url}/ui/agents/{kana}?tab=config")).text
            # its runtime in the drawer, drawn again for another model, keeps a
            # box left unticked, which the form does not send
            draft = json.loads(hidden(tab, "draft"))
            draft["harness"][0]["values"]["fast"] = True
            item = {
                "draft": json.dumps(draft),
                "label": "harness",
                "place": "0",
                "kind": "workshop-runtime",
            }
            ticked = re.compile(r'name="f\.fast" checked')
            opened = await bob.post(f"{served.url}/ui/agents/{kana}/item", data=item)
            assert ticked.search(opened.text), opened.text[:3000]
            redrawn = await bob.post(
                f"{served.url}/ui/agents/{kana}/item",
                data=item | {"redraw": "1", "f.model": "workshop-large"},
            )
            assert 'name="f.fast"' in redrawn.text, redrawn.text[:3000]
            assert not ticked.search(redrawn.text), redrawn.text[:3000]
            listing = seen(await listed(served, boss, "有马佳奈"), "agents.list")
            saved = await bob.post(
                f"{served.url}/ui/agents/{kana}/config",
                data={
                    "draft": hidden(tab, "draft"),
                    "revision": hidden(tab, "revision"),
                    "name": "有马佳奈 二号",
                    "idle_timeout": "0",
                    "mode": "session",
                    "sandbox": "workspace-write",
                    "action": "save",
                },
            )
            assert saved.status_code == 200, saved.text[:3000]
            names = [
                agent["name"] for agent in await asyncio.to_thread(agents_on, home)
            ]
            assert "有马佳奈 二号" in names
            # a list open elsewhere is told of the change
            assert "list" in await told(
                served, boss, {"id": "list", "topic": "agents.list", "seen": listing}
            )


async def test_the_computers_shared_for_making_are_offered_and_the_maker_owns_the_agent(
    tmp_path: Path,
):
    async with serving() as served, client() as boss, client() as bob:
        await admin(served, boss)
        makers = await role(
            served, boss, "agents.view", "agents.create", "computers.view"
        )
        await boss.post(f"{served.url}{makers}/default")
        await through(served, bob, "bob")
        given = makers.rsplit("/", 1)[1]
        home = tmp_path / "kana-vm"
        async with computer(served, boss, home, "kana-vm", [KANA]) as kana_vm:
            await listed(served, boss, "kana-bot")
            # a computer's share page offers making agents on it
            sharing = f"{served.url}/ui/access/shares/computer/{kana_vm.ref}"
            assert f"{given} agents.create" in (await boss.get(sharing)).text
            page = (await bob.get(f"{served.url}/ui/agents/new")).text
            assert "kana-vm" not in page
            # nor is a draft sent back naming it made there
            forged = json.loads(hidden(page, "draft")) | {
                "name": "影子",
                "computer": kana_vm.id,
                "channel": [
                    {
                        "kind": "workshop-channel",
                        "connection": None,
                        "values": {"bot": "shadow-bot"},
                        "secrets": [],
                        "new": {},
                    }
                ],
                "harness": [
                    {
                        "kind": "workshop-runtime",
                        "connection": None,
                        "values": {},
                        "secrets": [],
                        "new": {},
                    }
                ],
            }
            refused = await bob.post(
                f"{served.url}/ui/agents/new",
                data={"draft": json.dumps(forged), "step": "4", "action": "create"},
            )
            assert refused.status_code == 404
            names = [
                agent["name"] for agent in await asyncio.to_thread(agents_on, home)
            ]
            assert "影子" not in names
            await boss.post(
                sharing,
                data={
                    "roles": [given],
                    "grant": [f"{given} computers.view", f"{given} agents.create"],
                },
            )
            page = (await bob.get(f"{served.url}/ui/agents/new")).text
            assert "kana-vm" in page
            computer_ref = re.search(r'name="computer" value="(\d+)"', page)
            assert computer_ref, page[:3000]

            async def step(page: str, **fields: str) -> str:
                answer = await bob.post(
                    f"{served.url}/ui/agents/new",
                    data={
                        "draft": hidden(page, "draft"),
                        "step": hidden(page, "step"),
                        "opened": hidden(page, "opened"),
                        **fields,
                    },
                )
                assert answer.status_code in (200, 204), answer.text[:3000]
                return (
                    answer.text
                    if answer.status_code == 200
                    else answer.headers["hx-redirect"]
                )

            page = await step(
                page,
                name="小满",
                idle_timeout="600",
                mode="session",
                sandbox="workspace-write",
                computer=computer_ref.group(1),
                action="go:2",
            )
            page = await step(page, action="add:channel:workshop-channel")
            page = await step(page, **{"f.bot": "xiaoman-bot", "action": "go:3"})
            page = await step(page, action="add:harness:workshop-runtime")
            page = await step(page, action="go:4")
            made = await step(page, action="create")
            assert made.startswith("/ui/agents/")
            # the maker owns it, and so may share it
            assert (await bob.get(f"{served.url}{made}/share")).status_code == 200
            agents = await asyncio.to_thread(agents_on, home)
            xiaoman = next(agent for agent in agents if agent["name"] == "小满")
            assert xiaoman["channel"][0]["bot"] == "xiaoman-bot"
            assert xiaoman["harness"][0]["kind"] == "workshop-runtime"
