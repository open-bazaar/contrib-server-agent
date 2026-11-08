"""A real node in a process of its own, as a computer of the served server:
connected through starlink with the token its page gives, and running the
workshop's channel and runtime for the agents it is given."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
lazy import asyncio
lazy import os
lazy import sys
lazy import tomllib
lazy from collections.abc import AsyncIterator
lazy from pathlib import Path
lazy from typing import Any
lazy from uuid import uuid4

import httpx
import tomli_w
from served import Served, enrol

# how long a node takes to come up and its computer to show online
WAIT_SECONDS = 120


@dataclass(frozen=True, slots=True)
class Computer:
    ref: str
    id: str
    home: Path
    node: asyncio.subprocess.Process


async def _run(*arguments: str, environment: dict[str, str]) -> str:
    process = await asyncio.create_subprocess_exec(
        *arguments,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    assert process.returncode == 0, output.decode()
    return output.decode()


def agents_on(home: Path) -> list[dict[str, Any]]:
    """The agents the node's configuration holds now."""
    document = tomllib.loads((home / "node.toml").read_text())
    return next(
        entry["config"]["agents"]
        for package in document["contribs"]
        for entry in package["enabled"]
        if entry["entry"] == "agent"
    )


@asynccontextmanager
async def computer(
    served: Served,
    session: httpx.AsyncClient,
    home: Path,
    name: str,
    agents: list[dict[str, Any]],
) -> AsyncIterator[Computer]:
    """A computer the account connects, its node running the agents."""
    ref, token = await enrol(served, session, name)
    config = home / "node.toml"
    environment = dict(os.environ) | {"BCN_HOME": str(home), "HOME": str(home)}
    await asyncio.to_thread(home.mkdir, parents=True, exist_ok=True)
    # a first run writes the defaults, which are then given what the test needs
    await _run(
        sys.executable,
        "-m",
        "bazaar_compute_node",
        "--configer-options",
        str(config),
        "--help",
        environment=environment,
    )
    document = tomllib.loads(await asyncio.to_thread(config.read_text))
    for package in document["contribs"]:
        for entry in package["enabled"]:
            if entry["entry"] == "agent":
                entry["config"]["agents"] = agents
            elif entry["entry"] == "sidecar-unix":
                # a socket path stays short whatever the temporary directory
                entry["config"] = {
                    "endpoint": f"/tmp/bcn-agents-{uuid4().hex[:12]}.sock"
                }
    document["contribs"] += [
        {
            "package": "workshop",
            "enabled": [
                {"entry": "workshop-channel-builder"},
                {"entry": "workshop-runtime-builder"},
            ],
        },
        {
            "package": "bazaar-contrib-starlink",
            "enabled": [
                {"entry": "starlink", "config": {"server": served.url, "token": token}}
            ],
        },
    ]
    await asyncio.to_thread(config.write_text, tomli_w.dumps(document))
    output = home / "node.log"
    log = await asyncio.to_thread(output.open, "wb")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("run_node.py")),
        "config-filesystem",
        str(config),
        env=environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=log,
        stderr=asyncio.subprocess.STDOUT,
    )
    await asyncio.to_thread(log.close)
    try:
        async with asyncio.timeout(WAIT_SECONDS):
            while b"bcn ready" not in await asyncio.to_thread(output.read_bytes):
                assert process.returncode is None, await asyncio.to_thread(
                    output.read_text
                )
                await asyncio.sleep(0.5)
        yield Computer(ref=ref, id=token.partition(":")[0], home=home, node=process)
    finally:
        if process.returncode is None:
            assert process.stdin is not None
            process.stdin.close()
            await process.wait()
