"""The sidebar's part of those at work, following a real turn on a node:
it shows an agent as the turn starts and lets it go as the turn ends; the
list is told what each turn used; and what the node says of its agents is
announced, the turn it runs named while it works."""

from __future__ import annotations

lazy import asyncio
lazy import json
lazy from pathlib import Path

from computer import computer
from served import admin, client, seen, serving, told, working

KANA = {
    "id": "kana",
    "name": "有马佳奈",
    "channel": [{"kind": "workshop-channel", "bot": "kana-bot"}],
    "harness": [{"kind": "workshop-runtime"}],
}


async def test_a_turn_puts_the_agent_among_those_at_work_until_it_ends(tmp_path: Path):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        home = tmp_path / "kana-vm"
        async with computer(served, boss, home, "kana-vm", [KANA]):
            table = ""
            async with asyncio.timeout(45):
                while "kana-bot" not in table:
                    table = (await boss.get(f"{served.url}/ui/agents/table")).text
                    await asyncio.sleep(1)
            workshop = home / "workshop" / "kana"
            await asyncio.to_thread(
                (workshop / "inbox").mkdir, parents=True, exist_ok=True
            )
            await asyncio.to_thread(
                (workshop / "inbox" / "first.json").write_text,
                json.dumps({"sender": "tester", "text": "hello"}),
            )
            await working(served, boss, "有马佳奈", True)
            assert any(said.agents["kana"].turns for said in served.said)
            await asyncio.to_thread((workshop / "release").write_text, "")
            await working(served, boss, "有马佳奈", False)
            assert not served.said[-1].agents["kana"].turns
            # a list drawn between two turns is told what the second used,
            # its agent idle again as it was
            listing = seen(
                (await boss.get(f"{served.url}/ui/agents/table")).text, "agents.list"
            )
            await asyncio.to_thread((workshop / "release").unlink)
            await asyncio.to_thread(
                (workshop / "inbox" / "second.json").write_text,
                json.dumps({"sender": "tester", "text": "again"}),
            )
            await working(served, boss, "有马佳奈", True)
            await asyncio.to_thread((workshop / "release").write_text, "")
            await working(served, boss, "有马佳奈", False)
            assert "list" in await told(
                served, boss, {"id": "list", "topic": "agents.list", "seen": listing}
            )
