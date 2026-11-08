"""An agent's workspace, read from its computer a directory and a file at a
time."""

from __future__ import annotations

lazy import asyncio
lazy import re
lazy from pathlib import Path

from computer import computer
from served import admin, client, serving

KANA = {
    "id": "kana",
    "name": "有马佳奈",
    "channel": [{"kind": "workshop-channel", "bot": "kana-bot"}],
    "harness": [{"kind": "workshop-runtime"}],
}


def _fill(workspace: Path) -> None:
    (workspace / "notes").mkdir(parents=True, exist_ok=True)
    (workspace / "notes" / "today.md").write_text(
        "# 今日\n\n- 发版\n", encoding="utf-8"
    )
    (workspace / "settings.toml").write_text('name = "kana"\n', encoding="utf-8")


async def test_the_workspace_tab_reads_folders_and_files_from_the_computer(
    tmp_path: Path,
):
    async with serving() as served, client() as boss:
        await admin(served, boss)
        home = tmp_path / "kana-vm"
        async with computer(served, boss, home, "kana-vm", [KANA]):
            await asyncio.to_thread(_fill, home / ".bcn" / "workspaces" / "kana")
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
            found = re.search(r'href="/ui/agents/([^"?/]+)"', table.text)
            assert found, table.text
            page = f"{served.url}/ui/agents/{found.group(1)}"

            tab = await boss.get(page, params={"tab": "workspace"})
            assert "settings.toml" in tab.text
            assert "notes" in tab.text

            notes = await boss.get(
                f"{page}/workspace", params={"path": "notes", "depth": "1"}
            )
            assert "today.md" in notes.text

            markdown = await boss.get(f"{page}/file", params={"path": "notes/today.md"})
            assert "今日" in markdown.text

            toml = await boss.get(f"{page}/file", params={"path": "settings.toml"})
            assert "TOML" in toml.text
            assert "kana" in toml.text
