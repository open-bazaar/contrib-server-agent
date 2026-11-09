"""What other modules hold server-agent to: the tabs and the activity
sections they put on an agent's page, and the agents an account may see."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
lazy from collections.abc import Sequence
lazy from contextlib import AbstractAsyncContextManager

from bazaar_core import ServiceKey
from pydantic import BaseModel, ConfigDict, Field
lazy from bazaar_contrib_server import Account

lazy from .models import Activity, AgentView

# one segment of an address, as the server's own registrations take
NAME = r"^[a-z][a-z0-9-]*$"


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentTab(_Item):
    """A tab of an agent's page, its page loaded in an iframe at
    <src>/<the agent's ref>, for the accounts that may use its permission
    point on the agent. One with a parent tab stays out of the tab bar and
    lights its parent while it is open."""

    key: str = Field(pattern=NAME)
    # the text key of its name
    text: str
    # an SVG icon stroked in currentColor, as lucide's are
    icon: str
    src: str
    permission: str | None = None
    order: int = 0
    # the update bus topic, of the agent's ref, whose state's text is the count beside the name
    count: str | None = None
    parent: str | None = None


class AgentSection(_Item):
    """A section of an agent's activity tab, its page loaded in an iframe at
    <src>/<the agent's ref>, for the accounts that may use its permission
    point on the agent: in the main column or the side one on a wide
    screen, the main before the side on a narrow one."""

    key: str = Field(pattern=NAME)
    src: str
    column: Literal["main", "side"]
    permission: str | None = None
    order: int = 0
    # whether a phone shows it
    narrow: bool = True


@dataclass(frozen=True, slots=True)
class Agent:
    """An agent as its computer last gave it: the computer it runs on and
    whether that is here, its configuration as agent-configuration lists
    it, and how it was as agent-activity last said, nothing before."""

    id: str
    computer_id: str
    name: str
    online: bool
    config: AgentView
    activity: Activity | None


class Agents(Protocol):
    def tab(self, tab: AgentTab) -> AbstractAsyncContextManager[None]:
        """Show the tab on every agent's page while open."""
        ...

    def section(self, section: AgentSection) -> AbstractAsyncContextManager[None]:
        """Show the section in every agent's activity tab while open."""
        ...

    async def agents(self, account: Account) -> Sequence[Agent]:
        """The agents the account may see, as their computers last gave
        them; every computer here is asked again about what is a minute
        old, one just connected having given none yet."""
        ...

    async def agent(self, account: Account, agent_id: str) -> Agent | None:
        """The agent as its computer last gave it, that computer asked again
        about what is a minute old; nothing when the account may not see it
        or no computer has it."""
        ...


AGENTS = ServiceKey[Agents]("server.agents")
