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
                await context.grant_permissions(["clipboard-read"], origin=served.url)
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
                chain = agent.locator(".profile .fact.chain")
                await expect(chain).to_be_visible()
                assert await chain.evaluate("""chain => {
                    const facts = chain.closest('.facts').getBoundingClientRect();
                    const box = chain.getBoundingClientRect();
                    const models = [...chain.querySelectorAll('.hop b')];
                    return (
                        models.length === 2
                        && chain.scrollWidth <= chain.clientWidth
                        && box.left >= facts.left - 1
                        && box.right <= facts.right + 1
                        && models.every(model => {
                            const rect = model.getBoundingClientRect();
                            return (
                                rect.width > 0
                                && model.scrollWidth <= model.clientWidth
                                && rect.left >= box.left
                                && rect.right <= box.right
                            );
                        })
                    );
                }""")
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
                # token left blank; reached from the list and its tab, the page
                # changing in place
                await page.goto(f"{served.url}/agents")
                listing = next(
                    one for one in page.frames if one.url.endswith("/ui/agents/")
                )
                await listing.evaluate("window.kept = true")
                agent = page.frame_locator(
                    "iframe[src^='/ui/agents/']:not([src*='working'])"
                )
                # Menus are already in the list and work without a request.
                small = agent.locator(".tr.row", has_text="小满")
                menu = small.locator(".pop")
                await expect(menu.locator("a")).to_have_text("配置")
                await expect(
                    menu.get_by_role("button", name="分享", include_hidden=True)
                ).to_have_count(1)
                await expect(
                    menu.get_by_role("button", name="删除", include_hidden=True)
                ).to_have_count(1)
                await context.set_offline(True)
                await small.locator(".more").click()
                await expect(menu).to_be_visible()
                await expect(menu.get_by_role("link", name="配置")).to_be_visible()
                await small.locator(".more").press("Escape")
                await expect(menu).to_be_hidden()
                await small.locator(".more").click()
                await expect(menu).to_be_visible()
                await small.locator(".more").click()
                await expect(menu).to_be_hidden()
                await context.set_offline(False)
                await agent.locator("a.stretch", has_text="小满").click()
                await page.wait_for_url(re.compile(r"/agents/\d+$"))
                await agent.locator("a.tab", has_text="配置").click()
                await page.wait_for_url(re.compile(r"tab=config$"))
                assert await listing.evaluate("window.kept === true")
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
                # the rest come in as the end of the first page is scrolled to
                await agent.locator(
                    ".fr", has_text="2026-09-50.md"
                ).scroll_into_view_if_needed()
                await expect(
                    agent.locator(".fr", has_text="2026-09-60.md")
                ).to_have_count(1)
                await expect(agent.locator(".fr.more")).to_have_count(0)
                await agent.get_by_role("button", name=re.compile("^MEMORY.md")).click()
                await expect(agent.locator(".drawer .prose h1")).to_have_text(
                    "记忆索引"
                )
                # its path is copied, the page served over plain http to an
                # address on the network too, where the clipboard is not given
                detail = page.frame(url=re.compile(r"/ui/agents/\d+"))
                assert detail
                await detail.evaluate(
                    "Object.defineProperty(navigator, 'clipboard', {value: undefined})"
                )
                await agent.locator(".fpath .copy").click()
                assert (await page.evaluate("navigator.clipboard.readText()")).endswith(
                    "MEMORY.md"
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
                long_tool = agent.locator(
                    ".tools span",
                    has_text="codex_document_control.execute_document_command",
                )
                await expect(long_tool).to_be_visible()
                assert await long_tool.evaluate("""tool => {
                    const card = tool.closest('.card').getBoundingClientRect();
                    const row = tool.getBoundingClientRect();
                    return tool.scrollWidth <= tool.clientWidth && row.left >= card.left && row.right <= card.right;
                }""")
                await long_tool.scroll_into_view_if_needed()

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


def _render_runtime_chain_page() -> str:
    from importlib.resources import files

    from jinja2 import Environment, PackageLoader

    server_resources = Path(str(files("bazaar_contrib_server"))) / "resources"
    agent_resources = Path(str(files("bazaar_contrib_server_agent"))) / "resources"
    base_css = (server_resources / "static" / "base.css").read_text(encoding="utf-8")
    agents_css = (agent_resources / "static" / "agents.css").read_text(encoding="utf-8")
    detail_source = (
        agent_resources / "templates" / "agents" / "detail.html"
    ).read_text(encoding="utf-8")
    profile_source = (
        '{% import "agents/macros.html" as m with context %}\n'
        + detail_source[
            detail_source.index('<div class="profile">') : detail_source.index(
                "{# its own tabs"
            )
        ]
    )

    environment = Environment(
        loader=PackageLoader("bazaar_contrib_server_agent", "resources/templates"),
        autoescape=True,
    )
    environment.filters["tint"] = lambda _value: "h3"

    class _T:
        def text(self, key: str, *_args: object) -> str:
            return {
                "agents.idle": "空闲",
                "agents.standby": "备用",
                "agents.model.default": "默认模型",
                "agents.runs_on": "运行在",
                "agents.mode.dangerous_individual": "个人助理 · 完全访问",
            }.get(key, key)

    agent = {
        "id": "kana",
        "name": "有马佳奈",
        "mode": "dangerous_individual",
        "channel": [
            {"kind": "telegram", "connection": "telegram:0", "values": {}},
        ],
        "harness": [
            {
                "kind": "claudecode",
                "connection": "claudecode:0",
                "values": {"model": "opus", "effort": "medium"},
            },
            {
                "kind": "antigravity",
                "connection": "antigravity:0",
                "values": {"model": "gemini-4-neon", "effort": "xhigh"},
            },
            {
                "kind": "antigravity",
                "connection": "antigravity:1",
                "values": {"model": "claude-opus-5-5", "effort": "medium"},
            },
        ],
    }
    row = {
        "state": "idle",
        "kept": {"agent": agent},
        "computer": {"name": "Apple M1 Max"},
        "activity": {
            "channels": [
                {
                    "connection": "telegram:0",
                    "identity": {"id": "1", "name": "armslave_bot(有马加奈)"},
                }
            ]
        },
    }
    profile_html = environment.from_string(profile_source).render(
        agent=agent, row=row, t=_T()
    )
    return (
        f"<style>{base_css}\n{agents_css}</style></head>"
        f'<body><section class="content detail">{profile_html}</section></body></html>'
    )


async def test_runtime_chain_wraps_cleanly_on_narrow_screens():
    page_body = await asyncio.to_thread(_render_runtime_chain_page)

    async with async_playwright() as driver:
        browser = await driver.chromium.launch()
        try:
            for layout, width, height, expect_single_row in (
                ("wide", 1200, 400, True),
                ("narrow", 390, 844, False),
            ):
                page = await browser.new_page(
                    viewport={"width": width, "height": height}
                )
                await page.set_content(
                    f'<!doctype html><html data-layout="{layout}"><head><meta charset="utf-8">'
                    + page_body
                )
                metrics = await page.evaluate("""() => {
                    const facts = document.querySelector('.facts').getBoundingClientRect();
                    const chain = document.querySelector('.fact.chain');
                    const box = chain.getBoundingClientRect();
                    const hops = [...chain.querySelectorAll('.hop')].map(hop => {
                        const r = hop.getBoundingClientRect();
                        const b = hop.querySelector('b');
                        const br = b.getBoundingClientRect();
                        return {
                            top: Math.round(r.top),
                            inside: r.left >= box.left - 1 && r.right <= box.right + 1,
                            model: b.textContent,
                            modelWidth: br.width,
                            modelClipped: b.scrollWidth > b.clientWidth,
                            modelInside: br.left >= box.left - 1 && br.right <= box.right + 1,
                        };
                    });
                    return {
                        chainOverflow: chain.scrollWidth > chain.clientWidth,
                        docOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
                        boxInsideFacts: box.left >= facts.left - 1 && box.right <= facts.right + 1,
                        height: Math.round(box.height),
                        hops,
                    };
                }""")
                assert not metrics["chainOverflow"], metrics
                assert not metrics["docOverflow"], metrics
                assert metrics["boxInsideFacts"], metrics
                assert [hop["model"] for hop in metrics["hops"]] == [
                    "opus",
                    "gemini-4-neon",
                    "claude-opus-5-5",
                ]
                assert all(
                    hop["inside"]
                    and hop["modelInside"]
                    and hop["modelWidth"] > 0
                    and not hop["modelClipped"]
                    for hop in metrics["hops"]
                ), metrics
                distinct_rows = {hop["top"] for hop in metrics["hops"]}
                if expect_single_row:
                    assert len(distinct_rows) == 1, metrics
                    assert metrics["height"] == 26, metrics
                else:
                    assert len(distinct_rows) > 1, metrics
        finally:
            await browser.close()


def _render_agent_rows_page() -> str:
    from importlib.resources import files

    from jinja2 import Environment, PackageLoader

    server_resources = Path(str(files("bazaar_contrib_server"))) / "resources"
    agent_resources = Path(str(files("bazaar_contrib_server_agent"))) / "resources"
    base_css = (server_resources / "static" / "base.css").read_text(encoding="utf-8")
    agents_css = (agent_resources / "static" / "agents.css").read_text(encoding="utf-8")

    environment = Environment(
        loader=PackageLoader("bazaar_contrib_server_agent", "resources/templates"),
        autoescape=True,
    )
    for name in ("tint", "lasted", "tokens", "moment"):
        environment.filters[name] = lambda value, *_args: str(value)

    class _T:
        def text(self, key: str, *_args: object) -> str:
            return key

    def window(minutes: int, pct: float) -> dict[str, object]:
        return {
            "name": "Gemini Models",
            "duration_minutes": minutes,
            "remaining_pct": pct,
            "resets_at_ms": None,
        }

    def row(index: int, channels: int, runtimes: int) -> dict[str, object]:
        kinds = ("telegram", "lark", "wecom")
        harness = ("antigravity", "codex", "claudecode")
        return {
            "state": "idle",
            "online": True,
            "computer": {"name": "Apple M1 Max"},
            "kept": {
                "agent": {
                    "id": f"agent-{index}",
                    "name": f"智能体{index}",
                    "mode": "dangerous_individual",
                    "channel": [
                        {"kind": kinds[i % 3], "connection": f"c:{i}", "values": {}}
                        for i in range(channels)
                    ],
                    "harness": [
                        {
                            "kind": harness[i % 3],
                            "connection": f"h:{i}",
                            "values": {"model": f"model-{i}"},
                        }
                        for i in range(runtimes)
                    ],
                }
            },
            "activity": {
                "channels": [
                    {
                        "connection": f"c:{i}",
                        "identity": {"id": "1", "name": f"bot-{i}"},
                    }
                    for i in range(channels)
                ],
                "runtimes": [
                    {
                        "connection": f"h:{i}",
                        "quota": {"windows": [window(300, 80), window(10080, 60)]},
                    }
                    for i in range(runtimes)
                ],
                "usage": {"input": 0, "output": 0, "cost": None},
            },
        }

    rows = [row(0, 1, 1), row(1, 3, 3), row(2, 12, 12)]
    rows_html = environment.get_template("agents/rows.html").render(
        rows=rows, more=False, t=_T(), ref=str
    )
    return (
        f"<style>{base_css}\n{agents_css}</style></head>"
        f'<body><section class="content"><div class="card tbl">{rows_html}</div></section></body></html>'
    )


async def test_an_agent_row_keeps_its_height_however_many_channels_and_runtimes():
    page_body = await asyncio.to_thread(_render_agent_rows_page)

    async with async_playwright() as driver:
        browser = await driver.chromium.launch()
        try:
            for layout, width, height in (("wide", 1440, 900), ("narrow", 390, 844)):
                page = await browser.new_page(
                    viewport={"width": width, "height": height}
                )
                await page.set_content(
                    f'<!doctype html><html data-layout="{layout}"><head><meta charset="utf-8">'
                    + page_body
                )
                metrics = await page.evaluate("""() => ({
                    heights: [...document.querySelectorAll('.tr.row')].map(
                        row => Math.round(row.getBoundingClientRect().height)),
                    logos: [...document.querySelectorAll('.prov')].map(prov => {
                        const cell = prov.getBoundingClientRect();
                        const strip = prov.querySelector('.logos').getBoundingClientRect();
                        return strip.left >= cell.left - 1 && strip.right <= cell.right + 1;
                    }),
                    panels: [...document.querySelectorAll('.peek')].map(
                        peek => getComputedStyle(peek).display),
                    docOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
                })""")
                assert len(set(metrics["heights"])) == 1, (layout, metrics)
                assert all(metrics["logos"]), (layout, metrics)
                assert set(metrics["panels"]) == {"none"}, (layout, metrics)
                assert not metrics["docOverflow"], (layout, metrics)
        finally:
            await browser.close()
