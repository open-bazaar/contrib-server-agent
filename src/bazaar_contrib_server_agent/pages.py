"""The agent pages: the list with its filters, an agent's page with its own
tabs and those other modules put on it, how its channels are connected,
and the parts the sidebar shows; what an account may do with each agent
decided once a page."""

from __future__ import annotations

from dataclasses import dataclass
lazy import asyncio
lazy import json
lazy from collections.abc import Mapping, Sequence
lazy from pathlib import PurePosixPath, PureWindowsPath
lazy from time import time_ns
lazy from typing import Any, cast
lazy from urllib.parse import urlencode

lazy from bazaar_contrib_server import Account, Pages, Server
lazy from bazaar_contrib_server_access import Access
lazy from bazaar_contrib_server_computer import Computers, ComputerSummary
lazy from bazaar_contrib_sidecar import RemoteError
lazy from jinja2 import pass_context
lazy from jinja2.runtime import Context
lazy from markupsafe import Markup
lazy from pydantic import ValidationError
lazy from pygments import highlight
lazy from pygments.formatters import HtmlFormatter
lazy from pygments.lexers import get_lexer_for_filename
lazy from pygments.lexers.special import TextLexer
lazy from pygments.util import ClassNotFound
lazy from starlette.datastructures import FormData
lazy from starlette.exceptions import HTTPException
lazy from starlette.requests import Request
lazy from starlette.responses import Response
lazy from starlette.routing import Route

lazy from .fleet import Fleet, Inspected
lazy from .models import Activity, AgentView, Directory, File, Providers, Session
lazy from .service import Service
lazy from .store import PAGE, Kept, Store

VIEW = "agents.view"
CREATE = "agents.create"
UPDATE = "agents.update"
DELETE = "agents.delete"

# how the list narrows by state
STATES = ("all", "working", "failed", "offline")
# an agent's own tabs, and where among those other modules put there each falls
TABS = {"activity": 10, "workspace": 30, "skills": 40, "config": 50}


def size(count: int) -> str:
    """A file's size in the unit that reads best."""
    if count < 1024:
        return f"{count} B"
    scaled = count / 1024
    for unit in ("KB", "MB"):
        if scaled < 1024:
            return f"{scaled:.1f} {unit}"
        scaled /= 1024
    return f"{scaled:.1f} GB"


def shown(path: str, content: str) -> tuple[str, Markup | None]:
    """The language a file's name says it is in, if any, and its text
    coloured as the server colours code in Markdown, by classes prefixed
    tok-; Markdown itself is read as it renders instead."""
    formatter = HtmlFormatter(nowrap=True, classprefix="tok-")
    try:
        # its first and last lines kept, blank or not, to match their numbers
        lexer = get_lexer_for_filename(path, content, stripnl=False)
    except ClassNotFound:
        return "", Markup(highlight(content, TextLexer(stripnl=False), formatter))
    if lexer.name == "Markdown":
        return lexer.name, None
    return lexer.name, Markup(highlight(content, lexer, formatter))


def located(root: str, path: str) -> str:
    """Where a file of the workspace is on its computer, written as that
    computer writes its paths."""
    return str((PureWindowsPath if "\\" in root else PurePosixPath)(root, path))


@pass_context
def word(
    context: Context, schema: Mapping[str, Any], key: str, otherwise: str = ""
) -> str:
    """A plugin's own words for one of its fields, in the page's language
    when the plugin has them, else in English, else what is given."""
    locales = schema.get("x-locales") or {}
    return (
        locales.get(context["t"].language, {}).get(key)
        or locales.get("en", {}).get(key)
        or otherwise
    )


def draft(agent: AgentView) -> dict[str, Any]:
    """An agent as its config tab edits it: the basics, and each channel and
    runtime with the connection that keeps its saved credentials, the names
    of those saved and those typed since."""
    return {
        "name": agent.name,
        "idle_timeout": agent.idle_timeout,
        "mode": agent.mode,
        "sandbox": (agent.session or Session()).config.sandbox.policy,
        **{
            label: [
                {
                    "kind": provider.kind,
                    "connection": provider.connection,
                    "values": dict(provider.values),
                    "secrets": list(provider.secrets),
                    "new": {},
                }
                for provider in getattr(agent, label)
            ]
            for label in ("channel", "harness")
        },
    }


def typed(field: Mapping[str, Any], text: str | None) -> Any:
    """A form's text as the type the field's schema gives; a blank field has
    no value, which leaves the plugin's default."""
    types = {field.get("type")} | {
        option.get("type") for option in field.get("anyOf", ())
    }
    if "boolean" in types:
        return text is not None
    if not text:
        return None
    if "integer" in types:
        return int(text)
    if "number" in types:
        return float(text)
    return text


def fresh(kind: str) -> dict[str, Any]:
    """A channel or runtime just added: nothing set and nothing saved."""
    return {"kind": kind, "connection": None, "values": {}, "secrets": [], "new": {}}


def filled(schema: Mapping[str, Any], form: FormData) -> dict[str, Any]:
    """What an item's fields hold as typed: one left blank holds nothing, and
    a box left unticked, which the form does not send, holds false."""
    fields = cast(Mapping[str, Any], schema.get("properties") or {})
    return {
        name: value
        for name, field in fields.items()
        if not field.get("writeOnly")
        and (value := typed(field, cast(str | None, form.get(f"f.{name}")))) is not None
    }


def merge(item: dict[str, Any], schema: Mapping[str, Any], form: FormData) -> None:
    """Put what the item's fields say into it: its values as typed, and the
    credentials typed anew; one left blank stays as it was."""
    fields = cast(Mapping[str, Any], schema.get("properties") or {})
    item["values"] = filled(schema, form)
    item["new"] |= {
        name: str(form[f"s.{name}"])
        for name, field in fields.items()
        if field.get("writeOnly") and form.get(f"s.{name}")
    }


def edited(drafted: Mapping[str, Any]) -> dict[str, Any]:
    """What an edit sends the computer: each channel and runtime names the
    connection whose saved credentials it keeps and carries only those
    typed anew."""
    return {
        "name": drafted["name"],
        "idle_timeout": drafted["idle_timeout"],
        "mode": drafted["mode"],
        "session": {"config": {"sandbox": {"policy": drafted["sandbox"]}}}
        if drafted["mode"] == "session"
        else None,
        **{
            label: [
                {
                    "kind": item["kind"],
                    **(
                        {"connection": item["connection"]} if item["connection"] else {}
                    ),
                    **item["values"],
                    **item["new"],
                }
                for item in drafted[label]
            ]
            for label in ("channel", "harness")
        },
    }


def lasted(since_ms: int) -> str:
    """How long since then, as hours and minutes."""
    minutes = max(0, time_ns() // 1_000_000 - since_ms) // 60_000
    return f"{minutes // 60}:{minutes % 60:02}"


@dataclass(frozen=True, slots=True)
class Row:
    """An agent as a page shows it: what its computer last gave, where that
    computer is, how the agent is now, and what the account may do."""

    kept: Kept
    computer: ComputerSummary | None
    activity: Activity | None
    update: bool
    delete: bool
    share: bool

    @property
    def online(self) -> bool:
        return self.computer is not None and self.computer.online

    @property
    def state(self) -> str:
        """working, failed, idle or offline, as the list's filter has them."""
        if not self.online:
            return "offline"
        if self.activity is None:
            return "idle"
        if self.activity.state in ("failed", "blocked"):
            return "failed"
        return "working" if self.activity.working is not None else "idle"


class Views:
    def __init__(
        self,
        server: Server,
        pages: Pages,
        access: Access,
        computers: Computers,
        store: Store,
        fleet: Fleet,
        service: Service,
    ) -> None:
        self._server = server
        self._pages = pages
        self._access = access
        self._computers = computers
        self._store = store
        self._fleet = fleet
        self._service = service
        pages.environment.filters |= {"lasted": lasted, "word": word, "size": size}
        self.routes = [
            Route("/", self._list),
            Route("/table", self._table),
            Route("/more", self._more),
            Route("/working", self._working),
            Route("/new", self._new),
            Route("/new", self._creating, methods=["POST"]),
            Route("/status/{ref}", self._status),
            Route("/{ref}", self._detail),
            Route("/{ref}/workspace", self._workspace),
            Route("/{ref}/file", self._file),
            Route("/{ref}/config", self._configure, methods=["POST"]),
            Route("/{ref}/item", self._item, methods=["POST"]),
            Route("/{ref}/share", self._share),
            Route("/{ref}/remove", self._removing),
            Route("/{ref}/remove", self._remove, methods=["POST"]),
        ]

    async def name(self, agent_id: str) -> str | None:
        """What an agent is called, for its share page."""
        kept = await self._store.get(agent_id)
        return None if kept is None else kept.agent.name

    async def _visible(self, account: Account) -> frozenset[str] | None:
        if not account.allows(VIEW):
            raise HTTPException(status_code=403)
        return await self._access.targets(account, "agent", VIEW)

    async def _shown(self, account: Account, kept: Sequence[Kept]) -> list[Row]:
        """The agents as the account sees them, with their available actions."""
        computer_ids = sorted({item.computer_id for item in kept})
        computers = {
            computer.id: computer
            for computer in await self._computers.computers(computer_ids)
        }
        updatable = await self._access.targets(account, "agent", UPDATE)
        deletable = await self._access.targets(account, "agent", DELETE)
        await self._server.refs.load([item.agent.id for item in kept])
        rows = []
        for item in kept:
            rows.append(
                Row(
                    kept=item,
                    computer=computers.get(item.computer_id),
                    activity=self._fleet.activity(item.computer_id).get(item.agent.id),
                    update=account.allows(UPDATE)
                    and (updatable is None or item.agent.id in updatable),
                    delete=account.allows(DELETE)
                    and (deletable is None or item.agent.id in deletable),
                    share=await self._access.can_share(account, "agent", item.agent.id),
                )
            )
        return rows

    async def _narrowed(
        self, request: Request, after: tuple[str, str] | None
    ) -> tuple[list[Row], bool]:
        """A page of the agents the filters keep: with a state chosen, the
        pages are read on until one is full or there are no more."""
        account: Account = request.state.account
        visible = await self._visible(account)
        state = request.query_params.get("state", "all")
        computer = await self._named(request, "computer")
        name = request.query_params.get("q", "").strip()
        rows: list[Row] = []
        while True:
            kept, more = await self._store.page(
                visible, computer_id=computer, name=name, after=after
            )
            shown = await self._shown(account, kept)
            rows += [row for row in shown if state == "all" or row.state == state]
            if not more or len(rows) >= PAGE:
                return rows[:PAGE], more or len(rows) > PAGE
            after = (kept[-1].computer_id, kept[-1].agent.id)

    async def _named(
        self, request: Request, key: str, form: FormData | None = None
    ) -> str | None:
        """The id a query parameter, or a field of the form given, names by
        its ref."""
        short = (
            request.query_params.get(key)
            if form is None
            else cast(str | None, form.get(key))
        )
        found = None if not short else await self._server.refs.values([short])
        return None if found is None else found[0]

    async def _listing(self, request: Request, template: str) -> Response:
        account: Account = request.state.account
        visible = await self._visible(account)
        everyone = await self._fleet.looked()
        rows, more = await self._narrowed(request, None)
        await self._server.refs.load([computer.id for computer in everyone])
        return await self._pages.render(
            request,
            template,
            rows=rows,
            more=more,
            total=await self._store.count(visible),
            working=len(await self._busy(visible)),
            computers=everyone,
            state=request.query_params.get("state", "all"),
            states=STATES,
            chosen=request.query_params.get("computer", ""),
            query=request.query_params.get("q", ""),
            seen=await self.listed(account, {}),
            create=account.allows(CREATE),
            refresh=template == "agents/table.html",
        )

    async def _list(self, request: Request) -> Response:
        return await self._listing(request, "agents/list.html")

    async def _table(self, request: Request) -> Response:
        """The table alone, for a refresh or a change of filter."""
        return await self._listing(request, "agents/table.html")

    async def _more(self, request: Request) -> Response:
        """The rows of the next page, for the scroll."""
        after = await self._named(request, "after")
        kept = None if after is None else await self._store.get(after)
        rows, more = await self._narrowed(
            request, None if kept is None else (kept.computer_id, kept.agent.id)
        )
        return await self._pages.render(
            request, "agents/rows.html", rows=rows, more=more
        )

    async def _busy(self, visible: frozenset[str] | None) -> list[tuple[str, Activity]]:
        """The agents among those given at work, as their computers last said;
        those whose computer is away are left out, what it last said no
        longer holding."""
        working = self._fleet.working()
        there = {
            computer.id
            for computer in await self._computers.computers(
                sorted({computer_id for computer_id, _, _ in working})
            )
            if computer.online
        }
        return [
            (agent_id, activity)
            for computer_id, agent_id, activity in working
            if computer_id in there and (visible is None or agent_id in visible)
        ]

    async def _working(self, request: Request) -> Response:
        """The sidebar's part: the agents at work and for how long."""
        account: Account = request.state.account
        visible = await self._visible(account)
        busy = await self._busy(visible)
        kept = await self._store.many([agent_id for agent_id, _ in busy])
        await self._server.refs.load(list(kept))
        return await self._pages.render(
            request,
            "agents/working.html",
            busy=[
                (kept[agent_id], activity)
                for agent_id, activity in busy
                if agent_id in kept
            ],
            seen=await self.worked(account, {}),
            now_ms=time_ns() // 1_000_000,
        )

    async def _one(self, request: Request, point: str = VIEW) -> Row:
        """The agent a page is about, for an account that may use the point on
        it; any other is not there."""
        account: Account = request.state.account
        if not account.allows(point):
            raise HTTPException(status_code=403)
        found = await self._server.refs.values([request.path_params["ref"]])
        kept = None if found is None else await self._store.get(found[0])
        if kept is None or not await self._access.can(
            account, "agent", kept.agent.id, point
        ):
            raise HTTPException(status_code=404)
        (row,) = await self._shown(account, [kept])
        if row.computer is not None:
            self._fleet.look(row.computer.id, row.computer.online)
        return row

    async def _detail(self, request: Request) -> Response:
        row = await self._one(request)
        account: Account = request.state.account
        # what other modules put on the page, for an account that may see it
        points = {
            one.permission
            for one in (*self._service.tabs, *self._service.sections)
            if one.permission is not None
        }
        allowed = {
            point
            for point in points
            if await self._access.can(account, "agent", row.kept.agent.id, point)
        }
        tabs = [
            one
            for one in self._service.tabs
            if one.permission is None or one.permission in allowed
        ]
        # the configuration is a tab only for an account that may change it
        own = [key for key in TABS if key != "config" or row.update]
        tab = request.query_params.get("tab", "activity")
        opened = next((one for one in tabs if one.key == tab), None)
        tab = tab if opened is not None or tab in own else "activity"
        # an agent's runtimes by kind, in the order it tries them: the skills
        # shown are one kind's, the first unless another is chosen
        kinds = list(dict.fromkeys(item.kind for item in row.kept.agent.harness))
        kind = request.query_params.get("kind")
        kind = kind if kind in kinds else next(iter(kinds), None)
        return await self._pages.render(
            request,
            "agents/detail.html",
            row=row,
            tab=tab,
            opened=opened,
            # what else the address asks, for the opened tab's own page
            passed=urlencode(
                [
                    (key, value)
                    for key, value in request.query_params.multi_items()
                    if key not in ("tab", "kind")
                ]
            ),
            # a tab under another lights that one in the bar it is not in
            lit=(opened.parent or tab) if opened is not None else tab,
            bar=sorted(
                [*own, *(one for one in tabs if one.parent is None)],
                key=lambda one: TABS[one] if isinstance(one, str) else one.order,
            ),
            sections=sorted(
                (
                    one
                    for one in self._service.sections
                    if one.permission is None or one.permission in allowed
                ),
                key=lambda one: one.order,
            ),
            directory=await self._directory(row, "", 0) if tab == "workspace" else None,
            kinds=kinds,
            kind=kind,
            inspected=await self._inspected(row, kind)
            if tab == "skills" and kind is not None
            else None,
            **(await self._editing(account, row) if tab == "config" else {}),
            share=await self._access.can_share(account, "agent", row.kept.agent.id),
            seen=await self.followed(account, {"agent": request.path_params["ref"]}),
            now_ms=time_ns() // 1_000_000,
        )

    async def _status(self, request: Request) -> Response:
        """The activity tab's section of how each of the agent's channels is
        connected, as its computer last said."""
        row = await self._one(request)
        return await self._pages.render(
            request,
            "agents/status.html",
            row=row,
            seen=await self.followed(
                request.state.account, {"agent": request.path_params["ref"]}
            ),
        )

    async def _providers(self, row: Row) -> Providers | None:
        """The channels and runtimes the agent's computer can run, with their
        schemas; nothing while it is away or does not answer."""
        if not row.online:
            return None
        try:
            return await self._fleet.providers(row.kept.computer_id)
        except RemoteError, TimeoutError, ValidationError:
            return None

    async def _market(self, account: Account, computer: str) -> str | None:
        if any(
            point.point == "contribs.view" for point in self._server.permissions
        ) and await self._access.can(account, "computer", computer, "contribs.view"):
            await self._server.refs.load([computer])
            return f"/market?computer={self._server.refs.ref(computer)}"
        return None

    async def _editing(self, account: Account, row: Row) -> dict[str, Any]:
        """The config tab's start: the agent as its computer holds it now,
        the revision an edit is checked against, and the schemas its
        channels and runtimes are drawn from; nothing while the computer is
        away or does not answer."""
        providers = await self._providers(row)
        if providers is None:
            return {"drafted": None}
        try:
            listing = await self._fleet.listing(row.kept.computer_id)
        except RemoteError, TimeoutError, ValidationError:
            return {"drafted": None}
        agent = next(
            (item for item in listing.agents if item.id == row.kept.agent.id), None
        )
        return {
            "drafted": None if agent is None else draft(agent),
            "revision": listing.revision,
            "providers": providers,
            "dirty": False,
            "failed": None,
            "saved": False,
            "market": await self._market(account, row.kept.computer_id),
        }

    async def _configure(self, request: Request) -> Response:
        """A change to the config tab's draft: the basics as typed, an item
        edited, removed or moved; saved, it goes to the computer in one
        edit checked against the revision the tab started from."""
        row = await self._one(request, UPDATE)
        market = await self._market(request.state.account, row.kept.computer_id)
        form = await request.form()
        drafted = json.loads(str(form["draft"]))
        revision = str(form.get("revision") or "") or None
        drafted |= {
            "name": str(form["name"]),
            "idle_timeout": float(str(form.get("idle_timeout") or 0)),
            "mode": str(form["mode"]),
            "sandbox": str(form.get("sandbox") or drafted["sandbox"]),
        }
        providers = await self._providers(row)
        if providers is None:
            raise HTTPException(status_code=409)
        action, _, rest = str(form.get("action", "")).partition(":")
        label, _, place = rest.partition(":")
        if action == "remove":
            del drafted[label][int(place)]
        elif action == "order":
            drafted[label] = [
                drafted[label][int(index)] for index in str(form["order"]).split(",")
            ]
        elif action == "item":
            item = (
                fresh(str(form["kind"]))
                if place == "new"
                else drafted[label][int(place)]
            )
            merge(item, self._schema(providers, label, item["kind"]), form)
            if place == "new":
                drafted[label].append(item)
        elif action == "save":
            try:
                await self._computers.call(
                    row.kept.computer_id,
                    "agent-configuration",
                    "edit",
                    row.kept.agent.id,
                    edited(drafted),
                    expected_revision=revision,
                )
            except (RemoteError, TimeoutError) as error:
                return await self._pages.render(
                    request,
                    "agents/form.html",
                    row=row,
                    drafted=drafted,
                    revision=revision,
                    providers=providers,
                    dirty=True,
                    failed=str(error),
                    saved=False,
                    market=market,
                )
            editing = await self._editing(request.state.account, row)
            if editing["drafted"] is None:
                # saved, but the computer does not give it back now: the tab
                # drawn anew says so
                short = self._server.refs.ref(row.kept.agent.id)
                return Response(
                    status_code=204,
                    headers={"HX-Redirect": f"/ui/agents/{short}?tab=config"},
                )
            # read again once the listing above has stored the edit, a new name with it
            return await self._pages.render(
                request,
                "agents/form.html",
                row=await self._one(request, UPDATE),
                **(editing | {"saved": True}),
            )
        return await self._pages.render(
            request,
            "agents/form.html",
            row=row,
            drafted=drafted,
            revision=revision,
            providers=providers,
            dirty=True,
            failed=None,
            saved=False,
            market=market,
        )

    async def _item(self, request: Request) -> Response:
        """A channel or runtime of the draft in the drawer, its fields drawn
        from its schema on the computer; drawn again as its model changes,
        with what its fields hold then."""
        row = await self._one(request, UPDATE)
        form = await request.form()
        drafted = json.loads(str(form["draft"]))
        label = str(form["label"])
        place = str(form["place"])
        providers = await self._providers(row)
        if providers is None:
            raise HTTPException(status_code=409)
        item = (
            fresh(str(form["kind"])) if place == "new" else drafted[label][int(place)]
        )
        schema = self._schema(providers, label, item["kind"])
        fields = cast(Mapping[str, Any], schema.get("properties") or {})
        inspected = (
            await self._inspected(row, item["kind"]) if label == "harness" else None
        )
        return await self._pages.render(
            request,
            "agents/item.html",
            row=row,
            label=label,
            place=place,
            item=item,
            values=filled(schema, form) if form.get("redraw") else item["values"],
            typed={
                name: str(form[f"s.{name}"]) for name in fields if form.get(f"s.{name}")
            },
            schema=schema,
            models=inspected.inspection.models if inspected else (),
        )

    @staticmethod
    def _schema(providers: Providers, label: str, kind: str) -> Mapping[str, Any]:
        """The schema of a channel or runtime kind the computer runs."""
        return next(
            one.json_schema for one in getattr(providers, label) if one.kind == kind
        )

    async def _new(self, request: Request) -> Response:
        """Making an agent, its first step."""
        account: Account = request.state.account
        if not account.allows(CREATE):
            raise HTTPException(status_code=403)
        drafted = {
            "name": "",
            "idle_timeout": 0,
            "mode": "session",
            "sandbox": "workspace-write",
            "computer": None,
            "channel": [],
            "harness": [],
        }
        return await self._pages.render(
            request, "agents/new.html", **await self._wizard(account, drafted, 1, None)
        )

    async def _wizard(
        self,
        account: Account,
        drafted: dict[str, Any],
        step: int,
        opened: list[Any] | None,
    ) -> dict[str, Any]:
        """What a step of making an agent shows: the computers online the
        account may make one on, and, once one is chosen, the channels and
        runtimes it runs, the runtimes with what they report."""
        allowed = await self._access.targets(account, "computer", CREATE)
        computers = [
            computer
            for computer in await self._computers.computers(None)
            if computer.online and (allowed is None or computer.id in allowed)
        ]
        await self._server.refs.load([computer.id for computer in computers])
        chosen = next(
            (computer for computer in computers if computer.id == drafted["computer"]),
            None,
        )
        providers = None
        inspected: dict[str, Inspected] = {}
        if chosen is not None:
            try:
                providers = await self._fleet.providers(chosen.id)
            except RemoteError, TimeoutError, ValidationError:
                providers = None
        if chosen is not None and providers is not None and step >= 3:

            async def ask(kind: str) -> None:
                try:
                    inspected[kind] = await self._fleet.inspect(chosen.id, kind)
                except RemoteError, TimeoutError, ValidationError:
                    pass

            async with asyncio.TaskGroup() as group:
                for schema in providers.harness:
                    group.create_task(ask(schema.kind))
        return {
            "drafted": drafted,
            "step": step,
            "opened": opened,
            "computers": computers,
            "chosen": chosen,
            "providers": providers,
            "inspected": inspected,
            "market": await self._market(account, chosen.id)
            if chosen is not None
            else None,
            "failed": None,
        }

    async def _creating(self, request: Request) -> Response:
        """A step of making an agent: the fields of the step or of the item
        open put into the draft, then what was asked for done; made, the
        account owns it and goes to its page. The draft comes back from the
        page, so the computer it names is checked at every step: one the
        account may not make agents on is not there."""
        account: Account = request.state.account
        if not account.allows(CREATE):
            raise HTTPException(status_code=403)
        form = await request.form()
        drafted = json.loads(str(form["draft"]))
        step = int(str(form["step"]))
        opened = json.loads(str(form.get("opened") or "null"))
        if step == 1:
            computer = await self._named(request, "computer", form)
            drafted |= {
                "name": str(form["name"]),
                "idle_timeout": float(str(form.get("idle_timeout") or 0)),
                "mode": str(form["mode"]),
                "sandbox": str(form.get("sandbox") or drafted["sandbox"]),
                "computer": computer,
            }
        if drafted["computer"] is not None and not await self._access.can(
            account, "computer", drafted["computer"], CREATE
        ):
            raise HTTPException(status_code=404)
        providers = None
        if drafted["computer"] is not None:
            try:
                providers = await self._fleet.providers(drafted["computer"])
            except RemoteError, TimeoutError, ValidationError:
                providers = None
        if opened is not None and providers is not None:
            label, index = opened
            item = drafted[label][index]
            merge(item, self._schema(providers, label, item["kind"]), form)
        action, _, rest = str(form.get("action", "")).partition(":")
        label, _, place = rest.partition(":")
        failed = None
        if action == "go":
            step, opened = int(rest), None
        elif action == "add":
            drafted[label].append(fresh(place))
            opened = [label, len(drafted[label]) - 1]
        elif action == "open":
            opened = [label, int(place)]
        elif action == "close":
            opened = None
        elif action == "remove":
            del drafted[label][int(place)]
            opened = None
        elif action == "order":
            drafted[label] = [
                drafted[label][int(index)] for index in str(form["order"]).split(",")
            ]
            opened = None
        elif action == "create":
            try:
                agent = AgentView.model_validate(
                    await self._computers.call(
                        drafted["computer"],
                        "agent-configuration",
                        "add",
                        edited(drafted),
                    )
                )
            except (RemoteError, TimeoutError, ValidationError) as error:
                failed = str(error)
            else:
                await self._store.add(
                    drafted["computer"], agent, time_ns() // 1_000_000, account.id
                )
                await self._server.refs.load([agent.id])
                return Response(
                    status_code=204,
                    headers={
                        "HX-Redirect": f"/ui/agents/{self._server.refs.ref(agent.id)}"
                    },
                )
        shown = await self._wizard(account, drafted, step, opened)
        shown["failed"] = failed
        return await self._pages.render(request, "agents/wizard.html", **shown)

    async def _inspected(self, row: Row, kind: str) -> Inspected | None:
        """What the agent's runtime of the kind reports in its workspace;
        nothing while its computer is away or does not answer."""
        if not row.online:
            return None
        try:
            return await self._fleet.inspect(
                row.kept.computer_id, kind, row.kept.agent.id
            )
        except RemoteError, TimeoutError, ValidationError:
            return None

    async def _directory(self, row: Row, path: str, offset: int) -> Directory | None:
        """A page of a directory in the agent's workspace; nothing while its
        computer is away or does not answer."""
        if not row.online:
            return None
        try:
            return Directory.model_validate(
                await self._computers.call(
                    row.kept.computer_id,
                    "agent-configuration",
                    "workspace",
                    row.kept.agent.id,
                    path,
                    offset,
                )
            )
        except RemoteError, TimeoutError, ValidationError:
            return None

    async def _workspace(self, request: Request) -> Response:
        """The entries under a directory opened, or the next page of them."""
        row = await self._one(request)
        path = request.query_params.get("path", "")
        offset = int(request.query_params.get("offset", "0"))
        return await self._pages.render(
            request,
            "agents/entries.html",
            row=row,
            directory=await self._directory(row, path, offset),
            depth=int(request.query_params.get("depth", "0")),
            offset=offset,
        )

    async def _file(self, request: Request) -> Response:
        """A file of the workspace in the drawer: Markdown as it reads, other
        text coloured by its language, anything else only by its size; the
        workspace's root is the one its directory gave."""
        row = await self._one(request)
        path = request.query_params.get("path", "")
        file = None
        if row.online:
            try:
                file = File.model_validate(
                    await self._computers.call(
                        row.kept.computer_id,
                        "agent-configuration",
                        "read",
                        row.kept.agent.id,
                        path,
                    )
                )
            except RemoteError, TimeoutError, ValidationError:
                file = None
        language, coloured = (
            await asyncio.to_thread(shown, path, file.content)
            if file is not None and file.content is not None
            else ("", None)
        )
        return await self._pages.render(
            request,
            "agents/file.html",
            row=row,
            file=file,
            name=PurePosixPath(path).name,
            where=located(request.query_params.get("root", ""), path),
            language=language,
            coloured=coloured,
        )

    async def _share(self, request: Request) -> Response:
        """Sharing one agent: server-access's page in a frame."""
        row = await self._one(request)
        account: Account = request.state.account
        if not await self._access.can_share(account, "agent", row.kept.agent.id):
            raise HTTPException(status_code=404)
        return await self._pages.render(request, "agents/share.html", row=row)

    async def _removing(self, request: Request) -> Response:
        return await self._pages.render(
            request,
            "agents/remove.html",
            row=await self._one(request, DELETE),
            failed=None,
        )

    async def _remove(self, request: Request) -> Response:
        """The agent stops on its computer and its configuration goes from
        there, its files staying; its owner and shares go with it."""
        row = await self._one(request, DELETE)
        try:
            await self._computers.call(
                row.kept.computer_id,
                "agent-configuration",
                "remove",
                row.kept.agent.id,
            )
        except (RemoteError, TimeoutError) as error:
            return await self._pages.render(
                request, "agents/remove.html", row=row, failed=str(error)
            )
        await self._store.remove(row.kept.computer_id, row.kept.agent.id)
        return Response(status_code=204, headers={"HX-Trigger": "agents:removed"})

    async def counted(
        self, account: Account, scope: Mapping[str, str]
    ) -> Mapping[str, object] | None:
        """The sidebar badge: how many agents the account sees."""
        del scope
        await self._fleet.looked()
        visible = await self._access.targets(account, "agent", VIEW)
        return {"text": str(await self._store.count(visible)), "kind": "count"}

    async def listed(
        self, account: Account, scope: Mapping[str, str]
    ) -> Mapping[str, object] | None:
        """What the list shows changing: an agent coming or going, how one is
        as the computers last said, or one of their computers going away,
        coming back or giving its agents changed."""
        del scope
        visible = await self._access.targets(account, "agent", VIEW)
        held = await self._store.computers(visible)
        return {
            "count": await self._store.count(visible),
            "said": {
                agent_id: said
                for agent_id, said in self._fleet.said().items()
                if visible is None or agent_id in visible
            },
            "computers": {
                computer.id: [computer.online, self._fleet.revision(computer.id)]
                for computer in await self._computers.computers(held)
            },
        }

    async def followed(
        self, account: Account, scope: Mapping[str, str]
    ) -> Mapping[str, object] | None:
        """How one agent is, as its page follows it, and whether its computer
        is there."""
        found = await self._server.refs.values([scope["agent"]])
        if found is None or not await self._access.can(
            account, "agent", found[0], VIEW
        ):
            return None
        kept = await self._store.get(found[0])
        computers = (
            [] if kept is None else await self._computers.computers([kept.computer_id])
        )
        return {
            "said": self._fleet.said().get(found[0]),
            "fetched": None if kept is None else kept.fetched_ms,
            "online": any(computer.online for computer in computers),
        }

    async def worked(
        self, account: Account, scope: Mapping[str, str]
    ) -> Mapping[str, object] | None:
        """Which agents the account sees at work, and since when."""
        del scope
        visible = await self._access.targets(account, "agent", VIEW)
        busy = await self._busy(visible)
        return {
            "busy": sorted(
                f"{agent_id}@{activity.working.since_ms}"
                for agent_id, activity in busy
                if activity.working is not None
            )
        }
