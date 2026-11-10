"""Tabs and activity sections another module puts on an agent's page, in a
real browser, wide and on a phone, against a real node."""

from __future__ import annotations

lazy import asyncio
lazy import re
lazy from pathlib import Path

from bazaar_core import Composition, ContribEntry
from computer import computer
from ledger import ledger
from playwright.async_api import FrameLocator, Page, async_playwright, expect
from served import admin, client, serving
from test_sharing import listed

KANA = {
    "id": "kana",
    "name": "有马佳奈",
    "channel": [{"kind": "workshop-channel", "bot": "kana-bot"}],
    "harness": [{"kind": "workshop-runtime"}],
}


async def opened(page: Page, address: str) -> FrameLocator:
    """The agent's page in the shell, at the address."""
    await page.goto(address)
    agent = page.frame_locator("iframe.frame")
    await expect(agent.locator(".profile h2")).to_contain_text("有马佳奈")
    return agent


async def titles(agent: FrameLocator, column: str) -> list[str]:
    frames = agent.locator(f".sections .{column} iframe")
    return [await frame.get_attribute("title") or "" for frame in await frames.all()]


async def test_tabs_and_sections_another_module_registers_come_and_go(tmp_path: Path):
    async with serving() as served, client() as boss, async_playwright() as driver:
        await admin(served, boss)
        async with computer(served, boss, tmp_path / "kana-vm", "kana-vm", [KANA]):
            table = await listed(served, boss, "kana-bot")
            kana = re.search(r'href="/ui/agents/(\d+)"><b>有马佳奈</b>', table)
            assert kana, table[:3000]
            address = f"{served.url}/agents/{kana.group(1)}"
            browser = await driver.chromium.launch()
            try:
                context = await browser.new_context(
                    locale="zh-CN", viewport={"width": 1440, "height": 900}
                )
                await context.add_cookies(
                    [
                        {"name": c.name, "value": c.value or "", "url": served.url}
                        for c in boss.cookies.jar
                    ]
                )
                page = await context.new_page()
                async with served.hub.enable(
                    Composition(
                        entries=[ContribEntry(name="ledger", definition=ledger)]
                    )
                ):
                    agent = await opened(page, address)
                    # a count the update bus gives for the agent
                    await expect(
                        agent.locator(".tabs .tab", has_text="会话").locator(".tally")
                    ).to_have_text("12")
                    # the registered tabs among the page's own, by their order;
                    # the one under the activity is not in the bar
                    names = await agent.locator(".tabs .tab").all_inner_texts()
                    expected = [
                        "动态",
                        "会话",
                        "工作区",
                        "技能",
                        "账本",
                        "配置",
                        "共享",
                    ]
                    assert [
                        tab
                        for tab, name in zip(expected, names, strict=True)
                        if name.strip().startswith(tab)
                    ] == expected
                    assert not any("完整活动记录" in name for name in names)
                    # the activity is the sections, by column and order, each
                    # given the agent's ref
                    assert await titles(agent, "main") == ["live", "turns"]
                    assert await titles(agent, "side") == ["status", "usage", "notes"]
                    await expect(
                        agent.frame_locator("iframe[title=turns]").locator("#ref")
                    ).to_have_text(kana.group(1))
                    main = await agent.locator(".sections .main").bounding_box()
                    side = await agent.locator(".sections .side").bounding_box()
                    assert main and side and main["x"] + main["width"] <= side["x"]
                    # a section showing nothing takes no room: the one after it
                    # starts its column, level with the other column's first
                    async with asyncio.timeout(10):
                        while True:
                            turns = await agent.locator(
                                "iframe[title=turns]"
                            ).bounding_box()
                            status = await agent.locator(
                                "iframe[title=status]"
                            ).bounding_box()
                            assert turns and status
                            if turns["y"] == status["y"]:
                                break
                            await asyncio.sleep(0.2)
                    # a tab under another lights it while open; what else the
                    # address asks goes on to the tab's page
                    agent = await opened(
                        page, f"{address}?tab=log&turn=turn-1&kind=workshop-runtime"
                    )
                    await expect(agent.locator(".tabs .tab.on")).to_have_text("动态")
                    pane = agent.frame_locator("iframe.pane")
                    await expect(pane.locator("#name")).to_have_text("log")
                    await expect(pane.locator("#ref")).to_have_text(kana.group(1))
                    await expect(pane.locator("#query")).to_have_text("turn=turn-1")

                    # a phone: one column, the main first, without the sections
                    # for wide screens alone
                    await page.set_viewport_size({"width": 390, "height": 844})
                    agent = await opened(page, address)
                    for title in ("status", "notes"):
                        await expect(
                            agent.locator(f"iframe[title={title}]")
                        ).to_be_hidden()
                    turns = agent.locator("iframe[title=turns]")
                    usage = agent.locator("iframe[title=usage]")
                    await expect(usage).to_be_visible()
                    above = await turns.bounding_box()
                    below = await usage.bounding_box()
                    assert above and below
                    assert above["x"] == below["x"]
                    assert above["y"] + above["height"] <= below["y"]

                # the module gone, so is what it put there
                await page.set_viewport_size({"width": 1440, "height": 900})
                agent = await opened(page, address)
                await expect(
                    agent.locator(".tabs .tab", has_text="会话")
                ).to_have_count(0)
                assert await titles(agent, "main") == []
                assert await titles(agent, "side") == ["status"]
            finally:
                await browser.close()
