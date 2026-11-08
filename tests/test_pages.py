"""The agent pages over real HTTP."""

from __future__ import annotations

from served import admin, client, serving


async def test_the_list_has_no_agents_yet():
    async with serving() as served, client() as boss:
        await admin(served, boss)
        page = await boss.get(f"{served.url}/ui/agents/")
        assert page.status_code == 200, page.text[:2000]
        assert "No agents yet." in page.text
        panel = await boss.get(f"{served.url}/ui/agents/working")
        assert panel.status_code == 200, panel.text[:2000]
