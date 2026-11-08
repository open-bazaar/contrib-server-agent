"""An agent made, changed, read and removed in a real browser, wide and on
a phone, against a real node."""

from __future__ import annotations

lazy import asyncio
lazy import re
lazy from collections.abc import Awaitable, Callable
lazy from pathlib import Path

import pytest
from computer import agents_on, computer
from playwright.async_api import Page, async_playwright, expect
from served import admin, client, role, serving

KANA = {
    "id": "kana",
    "name": "有马佳奈",
    "channel": [{"kind": "workshop-channel", "bot": "kana-bot"}],
    "harness": [{"kind": "workshop-runtime"}],
}


def _fill(workspace: Path) -> None:
    """A workspace with a note, a project file and more notes than a page."""
    (workspace / "notes").mkdir(parents=True, exist_ok=True)
    (workspace / "MEMORY.md").write_text(
        "# 记忆索引\n\n- 先看效果图\n", encoding="utf-8"
    )
    (workspace / "pyproject.toml").write_text(
        '[project]\nname = "xiaoman"\n', encoding="utf-8"
    )
    for day in range(1, 61):
        (workspace / "notes" / f"2026-09-{day:02}.md").write_text(f"# {day}\n")


async def settled(page: Page, act: Callable[[], Awaitable[object]]) -> None:
    """Do something on the agents' frame and wait until the answer it asks
    for is drawn: the next step acts on what came back."""
    frame = next(
        one
        for one in page.frames
        if "/ui/agents/" in one.url and "working" not in one.url
    )
    before = await frame.evaluate(
        """() => {
          if (window.drawn === undefined) {
            window.drawn = 0;
            document.addEventListener("htmx:after:settle", () => window.drawn++);
          }
          return window.drawn;
        }"""
    )
    await act()
    await frame.wait_for_function(f"() => window.drawn > {before}")


async def _drag(page: Page, grip, above) -> None:
    """A grip dragged with the pointer onto the upper half of another row,
    both brought into view first."""
    await above.scroll_into_view_if_needed()
    await grip.scroll_into_view_if_needed()
    start = await grip.bounding_box()
    end = await above.bounding_box()
    assert start is not None and end is not None
    x = start["x"] + start["width"] / 2
    await page.mouse.move(x, start["y"] + start["height"] / 2)
    await page.mouse.down()
    for step in range(1, 11):
        y = start["y"] + (end["y"] + 4 - start["y"]) * step / 10
        await page.mouse.move(x, y)
    await page.mouse.up()


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["wide", "phone"])
async def test_an_agent_is_made_changed_read_shared_and_removed(
    tmp_path: Path, size: tuple[int, int]
):
    async with serving() as served, client() as boss, async_playwright() as driver:
        await admin(served, boss)
        await role(served, boss, "agents.view")
        home = tmp_path / "kana-vm"
        async with computer(served, boss, home, "kana-vm", [KANA]):
            browser = await driver.chromium.launch()
            try:
                context = await browser.new_context(
                    locale="zh-CN", viewport={"width": size[0], "height": size[1]}
                )
                await context.add_cookies(
                    [
                        {"name": c.name, "value": c.value or "", "url": served.url}
                        for c in boss.cookies.jar
                    ]
                )
                page = await context.new_page()
                await page.goto(f"{served.url}/agents/new")
                frame = page.frame_locator("iframe[src^='/ui/agents/new']")

                # made in four steps, each acting on what the one before drew:
                # two channels and two runtimes
                await frame.locator("input[name=name]").fill("小满")
                await frame.locator("input[name=idle_timeout]").fill("600")
                await frame.locator(".pcp", has_text="kana-vm").click()
                pick = frame.locator(".pick").first
                model = frame.locator("select[name='f.model']")
                await settled(
                    page, frame.get_by_role("button", name="下一步：渠道").click
                )
                await settled(page, pick.click)
                await frame.locator("input[name='f.bot']").fill("@xiaoman_bot")
                await frame.locator("input[name='s.token']").fill("tok-1")
                await settled(page, pick.click)
                await frame.locator("input[name='f.bot']").fill("小满")
                await settled(
                    page, frame.get_by_role("button", name="下一步：运行时").click
                )
                await settled(page, pick.click)
                await settled(page, lambda: model.select_option("workshop-large"))
                await frame.locator("select[name='f.effort']").select_option("high")
                await settled(page, pick.click)
                await settled(page, lambda: model.select_option("workshop-small"))
                await settled(
                    page, frame.get_by_role("button", name="下一步：确认").click
                )
                await expect(frame.locator(".sum")).to_contain_text("@xiaoman_bot")
                await frame.get_by_role("button", name="创建小满").click()
                await page.wait_for_url(re.compile(r"/agents/\d+$"))
                agent = page.frame_locator(
                    "iframe[src^='/ui/agents/']:not([src*='working'])"
                )
                await expect(agent.locator(".profile h2")).to_contain_text("小满")
                made = next(
                    one
                    for one in await asyncio.to_thread(agents_on, home)
                    if one["name"] == "小满"
                )
                assert [item["bot"] for item in made["channel"]] == [
                    "@xiaoman_bot",
                    "小满",
                ]

                # changed: renamed, the runtimes swapped, a channel edited with its
                # token left blank
                await page.goto(f"{page.url}?tab=config")
                agent = page.frame_locator(
                    "iframe[src^='/ui/agents/']:not([src*='working'])"
                )
                rows = agent.locator(".plist[x-data] .prow")
                await expect(rows).to_have_count(2)
                await settled(
                    page,
                    lambda: _drag(page, rows.nth(1).locator(".grip"), rows.nth(0)),
                )
                await expect(rows.nth(0)).to_contain_text("workshop-small")
                await settled(
                    page,
                    agent.locator(".prow", has_text="@xiaoman_bot")
                    .get_by_role("button", name="编辑")
                    .click,
                )
                await agent.locator("input[name='f.bot']").fill("@xiaoman_bot2")
                await settled(page, agent.get_by_role("button", name="完成").click)
                await agent.locator("input[name=name]").fill("阿满")
                await settled(page, agent.get_by_role("button", name="保存").click)
                await expect(agent.locator(".prow").first).to_contain_text(
                    "Token 已保存"
                )
                # the new name wherever the page names it, without a reload
                await expect(agent.locator(".crumbs")).to_contain_text("阿满")
                await expect(agent.locator(".danger-card")).to_contain_text("阿满")
                saved = next(
                    one
                    for one in await asyncio.to_thread(agents_on, home)
                    if one["name"] == "阿满"
                )
                assert saved["channel"][0] == {
                    "kind": "workshop-channel",
                    "bot": "@xiaoman_bot2",
                    "token": "tok-1",
                }
                assert saved["harness"][0]["model"] == "workshop-small"

                # read: its workspace a page at a time, a note and a coloured file
                await asyncio.to_thread(
                    _fill, home / ".bcn" / "workspaces" / saved["id"]
                )
                await page.goto(page.url.replace("tab=config", "tab=workspace"))
                agent = page.frame_locator(
                    "iframe[src^='/ui/agents/']:not([src*='working'])"
                )
                await agent.get_by_role("button", name=re.compile("^notes ")).click()
                more = agent.locator(".fr.more")
                await expect(more).to_contain_text("还有 10 项")
                await more.click()
                await expect(
                    agent.locator(".fr", has_text="2026-09-60.md")
                ).to_have_count(1)
                await agent.get_by_role("button", name=re.compile("^MEMORY.md")).click()
                await expect(agent.locator(".drawer .prose h1")).to_have_text(
                    "记忆索引"
                )
                await agent.locator(".overlay .close").click()
                await agent.get_by_role(
                    "button", name=re.compile("^pyproject.toml")
                ).click()
                await expect(
                    agent.locator(".drawer .code .tok-k").first
                ).to_be_visible()
                await agent.locator(".overlay .close").click()

                # its skills, and the MCP servers its runtime sees
                await page.goto(page.url.replace("tab=workspace", "tab=skills"))
                agent = page.frame_locator(
                    "iframe[src^='/ui/agents/']:not([src*='working'])"
                )
                await expect(agent.locator(".skills").first).to_contain_text("notes")
                await expect(agent.locator(".mcp")).to_contain_text("github")

                # shared with a role, then removed
                await agent.locator(".tabs button", has_text="共享").click()
                shares = agent.frame_locator("iframe.shares")
                await shares.locator("input[name=grant]").first.check()
                await shares.locator("footer button[type=submit]").click()
                await expect(agent.locator(".overlay")).to_have_count(0)
                await agent.locator(".top .actions .icon-btn").click()
                await agent.locator(".confirm button[type=submit]").click()
                await page.wait_for_url(re.compile(r"/agents/?$"))
                listing = page.frame_locator(
                    "iframe[src^='/ui/agents/']:not([src*='working'])"
                )
                await expect(
                    listing.locator(".tr.row", has_text="有马佳奈")
                ).to_have_count(1)
                await expect(listing.locator(".tr.row", has_text="阿满")).to_have_count(
                    0
                )
                names = [
                    one["name"] for one in await asyncio.to_thread(agents_on, home)
                ]
                assert "阿满" not in names
            finally:
                await browser.close()
