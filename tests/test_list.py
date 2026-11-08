"""The list of agents, as a real node runs them."""

from __future__ import annotations

lazy import asyncio
lazy from pathlib import Path

from computer import computer
from served import admin, client, serving

KANA = {
    "id": "kana",
    "name": "有马佳奈",
    "channel": [{"kind": "workshop-channel", "bot": "kana-bot"}],
    "harness": [
        {"kind": "workshop-runtime", "model": "workshop-large"},
        {"kind": "workshop-runtime"},
    ],
}


async def test_the_list_shows_a_computers_agents_as_its_node_runs_them(tmp_path: Path):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        async with computer(served, boss, tmp_path / "kana-vm", "kana-vm", [KANA]):
            table = None
            try:
                async with asyncio.timeout(45):
                    while True:
                        table = await boss.get(f"{served.url}/ui/agents/table")
                        if "kana-bot" in table.text:
                            break
                        await asyncio.sleep(1)
            except TimeoutError:
                raise AssertionError(table.text if table else "no table") from None
            assert "有马佳奈" in table.text
            assert "workshop-large" in table.text
            assert "Standby" in table.text
            assert "kana-vm" in table.text
