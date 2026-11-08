"""An agent's computer going away mid-turn: listed offline from what it
last gave, no longer among those at work, and gone with its shares once the
computer is removed."""

from __future__ import annotations

lazy import asyncio
lazy import json
lazy import re
lazy from pathlib import Path

from computer import computer
from served import admin, client, role, seen, serving, told, working

KANA = {
    "id": "kana",
    "name": "有马佳奈",
    "channel": [{"kind": "workshop-channel", "bot": "kana-bot"}],
    "harness": [{"kind": "workshop-runtime"}],
}
# a computer is offline a minute after it was last heard from
OFFLINE_SECONDS = 90


async def test_an_offline_computers_agents_stay_listed_until_it_is_removed(
    tmp_path: Path,
):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        viewers = await role(served, boss, "agents.view")
        given = viewers.rsplit("/", 1)[1]
        home = tmp_path / "kana-vm"
        async with computer(served, boss, home, "kana-vm", [KANA]) as kana_vm:
            table = ""
            async with asyncio.timeout(45):
                while "kana-bot" not in table:
                    table = (await boss.get(f"{served.url}/ui/agents/table")).text
                    await asyncio.sleep(1)
            found = re.search(r'href="/ui/agents/([^"?/]+)"', table)
            assert found, table
            kana = found.group(1)
            sharing = f"{served.url}/ui/access/shares/agent/{kana}"
            await boss.post(
                sharing, data={"roles": [given], "grant": [f"{given} agents.view"]}
            )
            # at work as its computer goes, without a word of the turn ending
            inbox = home / "workshop" / "kana" / "inbox"
            await asyncio.to_thread(inbox.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(
                (inbox / "first.json").write_text,
                json.dumps({"sender": "tester", "text": "hello"}),
            )
            panel = await working(served, boss, "有马佳奈", True)
            busy = seen(panel, "agents.working")
            kana_vm.node.kill()
            await kana_vm.node.wait()
        # its node gone, the agent stays listed from what it last gave and is
        # no longer among those at work, and the pages drawn while its
        # computer still seemed there are told once it is not
        table = (await boss.get(f"{served.url}/ui/agents/table")).text
        listing = seen(table, "agents.list")
        page = (await boss.get(f"{served.url}/ui/agents/{kana}")).text
        following = seen(page, "agents.agent")
        async with asyncio.timeout(OFFLINE_SECONDS):
            while "row away" not in table:
                await asyncio.sleep(2)
                table = (await boss.get(f"{served.url}/ui/agents/table")).text
        assert "有马佳奈" in table
        panel = (await boss.get(f"{served.url}/ui/agents/working")).text
        assert "有马佳奈" not in panel
        assert await told(
            served,
            boss,
            {"id": "list", "topic": "agents.list", "seen": listing},
            {
                "id": "agent",
                "topic": "agents.agent",
                "scope": {"agent": kana},
                "seen": following,
            },
            {"id": "working", "topic": "agents.working", "seen": busy},
        ) >= {"list", "agent", "working"}
        page = await boss.get(f"{served.url}/ui/agents/{kana}")
        assert page.status_code == 200
        # removed, the computer takes its agents and their shares with it
        removed = await boss.post(f"{served.url}/ui/computers/{kana_vm.ref}/remove")
        assert removed.status_code == 204, removed.text[:2000]
        async with asyncio.timeout(10):
            while "有马佳奈" in table:
                await asyncio.sleep(0.5)
                table = (await boss.get(f"{served.url}/ui/agents/table")).text
        assert (await boss.get(sharing)).status_code == 404
