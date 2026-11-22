"""The agent pages over real HTTP."""

from __future__ import annotations

lazy import re
lazy from importlib.resources import files
lazy from pathlib import Path

from served import admin, client, serving
lazy from jinja2 import Environment, PackageLoader


async def test_the_list_has_no_agents_yet():
    async with serving() as served, client() as boss:
        await admin(served, boss)
        page = await boss.get(f"{served.url}/ui/agents/")
        assert page.status_code == 200, page.text[:2000]
        assert "No agents yet." in page.text
        stylesheet = re.search(r'href="([^"]*agents\.css[^"]*)"', page.text)
        assert stylesheet is not None, page.text[:2000]
        css = await boss.get(f"{served.url}{stylesheet.group(1)}")
        assert css.status_code == 200
        assert ".g.antigravity {" in css.text
        panel = await boss.get(f"{served.url}/ui/agents/working")
        assert panel.status_code == 200, panel.text[:2000]


def test_plugin_logos_and_brand_styles_stay_in_sync():
    resources = Path(str(files("bazaar_contrib_server_agent"))) / "resources"
    logos_dir = resources / "templates" / "agents" / "logos"
    puzzle = (resources / "templates" / "agents" / "icons" / "puzzle.svg").read_text(
        encoding="utf-8"
    )
    css = (resources / "static" / "agents.css").read_text(encoding="utf-8")

    kinds = sorted(path.stem for path in logos_dir.glob("*.svg"))
    assert "antigravity" in kinds

    environment = Environment(
        loader=PackageLoader("bazaar_contrib_server_agent", "resources/templates"),
        autoescape=True,
    )
    environment.filters |= dict.fromkeys(
        ("tint", "lasted", "moment", "tokens", "word"), lambda value, *_: value
    )
    template = environment.from_string(
        '{% import "agents/macros.html" as m %}{{ m.logo(kind) }}'
    )

    fallback_rule = re.search(r"\.g:not\(([^)]+)\)", css)
    assert fallback_rule is not None
    excluded = {part.strip() for part in fallback_rule.group(1).split(",")}

    for kind in kinds:
        svg = (logos_dir / f"{kind}.svg").read_text(encoding="utf-8").strip()
        rendered = template.render(kind=kind)
        assert f'<span class="g {kind}">' in rendered
        assert svg in rendered
        assert puzzle.strip() not in rendered
        assert f".g.{kind} {{" in css
        assert f".{kind}" in excluded

    unknown = template.render(kind="unknown-runtime")
    assert '<span class="g unknown-runtime">' in unknown
    assert puzzle.strip() in unknown


def test_runtime_chain_fact_wraps_each_hop_with_its_model_and_meta():
    resources = Path(str(files("bazaar_contrib_server_agent"))) / "resources"
    detail_source = (resources / "templates" / "agents" / "detail.html").read_text(
        encoding="utf-8"
    )
    start = detail_source.index('<div class="profile">')
    end = detail_source.index("{# its own tabs")
    profile_source = (
        '{% import "agents/macros.html" as m with context %}\n'
        + detail_source[start:end]
    )

    environment = Environment(
        loader=PackageLoader("bazaar_contrib_server_agent", "resources/templates"),
        autoescape=True,
    )
    environment.filters |= dict.fromkeys(
        ("tint", "lasted", "moment", "tokens", "word"), lambda value, *_: value
    )

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
        "channel": [],
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
                "values": {"effort": "medium"},
            },
        ],
    }
    row = {
        "state": "idle",
        "kept": {"agent": agent},
        "computer": {"name": "Apple M1 Max"},
        "activity": None,
    }

    rendered = environment.from_string(profile_source).render(
        agent=agent, row=row, t=_T()
    )
    assert '<span class="fact chain">' in rendered
    assert rendered.count('<span class="hop">') == 3
    assert rendered.count('<span class="then">') == 2
    assert '<b>opus</b> <span class="meta">· medium</span>' in rendered
    assert '<b>gemini-4-neon</b> <span class="meta">· xhigh · 备用</span>' in rendered
    assert '<b>默认模型</b> <span class="meta">· medium · 备用</span>' in rendered

    css = (resources / "static" / "agents.css").read_text(encoding="utf-8")
    assert ".fact.chain {" in css
    assert "flex-wrap: wrap;" in css
    assert ".fact.chain .hop {" in css
