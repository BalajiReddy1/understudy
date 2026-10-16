"""The agent's tools. The model never writes Cypher: it picks one of these, a person
wrote every query behind them, and every name it passes is checked against the graph
before anything runs.

Every tool returns a Result:
    status   "ok" (answered from the graph, with paths), "memory" (from findings an
             earlier session saved), "not_in_graph" (the thing may exist, but nothing
             modelled covers it, which is not the same as "safe"), or "unknown" (the
             name doesn't resolve, so no query ran)
    summary  one plain sentence built from the data, which is also what the agent says
             when the model's own reply fails the answer check
    data     the facts, compact, with the routes and names a reply may cite

The same catalogue backs the in-app agent and the MCP server."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from falkordb import FalkorDB

from ..engine.fixes import FixRejected, apply_fix, checked_fix, propose
from ..engine.rehearse import depart, rehearse
from ..engine.stage import call_board, stage
from . import memory

PSEUDONYM = re.compile(r"^contributor-[0-9a-f]{4,16}$")
TOKEN = re.compile(r"[a-z0-9]+")
MAX_PEOPLE = 10


@dataclass
class Context:
    db: FalkorDB  # usually a TracedDB, so the queries show up in the trace
    graph: str  # what reads see: the visitor's session copy once they have one
    visitor: str | None = None  # memory goes to memory:<visitor>; None means no memory
    session: Callable[[], tuple[str, str]] | None = None  # makes or returns (id, graph)


@dataclass
class Result:
    status: str
    summary: str
    data: dict = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    run: Callable[..., Result]
    acts: bool = False  # changes the visitor's session copy (never the base graph)


# --- resolving names against the graph --------------------------------------------


def system_names(ctx: Context) -> list[str]:
    g = ctx.db.select_graph(ctx.graph)
    return [r[0] for r in g.ro_query("MATCH (s:System) RETURN s.name").result_set]


def resolve_system(ctx: Context, text: str) -> tuple[str | None, Result | None]:
    return match_system(system_names(ctx), text)


def match_system(names: list[str], text: str) -> tuple[str | None, Result | None]:
    """A system name from what someone typed: an exact name, a path inside a system,
    or words that pick out one system ("kubelet config")."""
    q = (text or "").strip().strip("/").lower()
    q = q.removeprefix("kubernetes/kubernetes/").removeprefix("kubernetes/")
    for n in names:
        if n.lower() == q:
            return n, None
    if "/" in q:
        owners = [n for n in names if q.startswith(n.lower() + "/")]
        if owners:
            return max(owners, key=len), None
        return None, Result(
            "not_in_graph",
            f"None of the {len(names)} modelled systems covers {text}. Only directories "
            "with their own OWNERS file under pkg/, cmd/, plugin/ and staging/ are "
            "modelled, so this says nothing either way about how safe it is.",
            {"query": text},
        )
    tokens = TOKEN.findall(q)
    found = [n for n in names if tokens and all(t in n.lower() for t in tokens)]
    if len(found) > 1:
        # "kubelet" means pkg/kubelet, not every system with kubelet in its path.
        last = [n for n in found if n.lower().rsplit("/", 1)[-1] == tokens[-1]]
        if len(last) == 1:
            found = last
    if len(found) == 1:
        return found[0], None
    if found:
        options = sorted(found, key=lambda n: (len(n), n))[:8]
        return None, Result(
            "unknown",
            f"'{text}' matches {len(found)} systems, so nothing ran. Pick one: "
            + ", ".join(options)
            + ".",
            {"query": text, "candidates": options},
        )
    return None, Result(
        "unknown", f"No system called '{text}', so nothing ran.", {"query": text, "candidates": []}
    )


def resolve_people(ctx: Context, handles: list[str]) -> tuple[list[str] | None, Result | None]:
    handles = [h.strip().lower() for h in handles or [] if h and h.strip()]
    if not handles:
        return None, Result("unknown", "No people were named, so nothing ran.", {})
    if len(handles) > MAX_PEOPLE:
        return None, Result("unknown", f"At most {MAX_PEOPLE} people at a time.", {})
    if real := [h for h in handles if not PSEUDONYM.match(h)]:
        return None, Result(
            "unknown",
            "People only appear as pseudonyms like contributor-1a2b, so names like "
            f"{', '.join(real)} are never looked up.",
            {"refused": real},
        )
    g = ctx.db.select_graph(ctx.graph)
    found = {
        r[0]
        for r in g.ro_query(
            "MATCH (p:Person) WHERE p.handle IN $h RETURN p.handle", {"h": handles}
        ).result_set
    }
    if missing := [h for h in handles if h not in found]:
        return None, Result(
            "unknown", f"Nobody in the graph is called {', '.join(missing)}.", {"missing": missing}
        )
    return sorted(set(handles)), None


def _remembered(ctx: Context, system: str) -> list[dict]:
    """Read memory before reasoning: what earlier sessions found about this system."""
    return memory.recall(ctx.db, ctx.visitor, system, limit=3) if ctx.visitor else []


def _remember(ctx: Context, systems: list[str], text: str, kind: str) -> None:
    if ctx.visitor and systems:
        memory.remember(ctx.db, ctx.visitor, systems, text, kind, ctx.graph)


def _session_graph(ctx: Context) -> str | None:
    if ctx.session is None:
        return None
    _, graph = ctx.session()
    ctx.graph = graph  # reads follow the visitor into their own copy
    return graph


# --- the tools ----------------------------------------------------------------------


def find_system(ctx: Context, query: str) -> Result:
    name, problem = resolve_system(ctx, query)
    if problem:
        return problem
    return Result("ok", f"'{query}' is {name}.", {"system": name})


def understudies(ctx: Context, system: str | None = None, limit: int = 5) -> Result:
    g = ctx.db.select_graph(ctx.graph)
    if not system:
        rows = call_board(g)[: max(1, min(int(limit), 20))]
        top = rows[0]
        return Result(
            "ok",
            f"The riskiest system is {top['name']}: {top['knowers']} people know it and "
            f"{top['approvers']} approve it, but its understudy number is {top['understudy']}.",
            {
                "systems": [
                    {k: r[k] for k in ("name", "understudy", "knowers", "approvers", "critical")}
                    for r in rows
                ]
            },
        )
    name, problem = resolve_system(ctx, system)
    if problem:
        return problem
    s = stage(g, name)
    knowers = [k for k in s["knowers"] if not k["off_stage"]]
    approvers = [a for a in s["approvers"] if not a["off_stage"]]
    critical = sorted({p["handle"] for p in knowers + approvers if p["critical"]})
    n = s["understudy"]
    summary = (
        f"{name}: {len(knowers)} people know it and {len(approvers)} can approve changes, "
        f"but its understudy number is {n}."
    )
    if n == 0:
        summary += " No approver has a line to anyone who knows it."
    elif critical:
        summary += f" Losing any one of {', '.join(critical)} would lower it."
    if s["inactive_owners"]:
        summary += f" Everyone listed in {s['inactive_owners']} has gone quiet."
    return Result(
        "ok",
        summary,
        {
            "system": name,
            "understudy": n,
            "knowers": len(knowers),
            "approvers": len(approvers),
            "routes": s["routes"],
            "critical": critical,
            "team": s["team"],
            "sigs": s["sigs"],
            "inactive_owners": s["inactive_owners"],
            "emeritus_approvers": s["emeritus_approvers"],
            "remembered": _remembered(ctx, name),
        },
    )


def _changes_text(people: list[str], changes: list, total: int, verb: str) -> str:
    who = ", ".join(people)
    if not changes:
        return f"If {who} {verb}, no system's number would drop: they carry no routes."
    worst = "; ".join(f"{c.system} {c.before} to {c.after}" for c in changes[:3])
    return f"If {who} {verb}, {len(changes)} of {total} systems would drop. Worst: {worst}."


def rehearse_departure(ctx: Context, people: list[str]) -> Result:
    handles, problem = resolve_people(ctx, people)
    if problem:
        return problem
    r = rehearse(ctx.db, ctx.graph, handles)
    summary = _changes_text(r.people, r.changes, r.total_systems, "left")
    _remember(ctx, [c.system for c in r.changes], f"Rehearsal: {summary}", "rehearsal")
    return Result(
        "ok",
        summary,
        {
            "people": r.people,
            "changes": [
                {"system": c.system, "before": c.before, "after": c.after, "routes": c.routes}
                for c in r.changes[:10]
            ],
            "systems_changed": len(r.changes),
            "systems_rerun": len(r.rerun),
            "total_systems": r.total_systems,
            "ms": r.timings_ms.get("total"),
        },
    )


def take_off_stage(ctx: Context, people: list[str]) -> Result:
    handles, problem = resolve_people(ctx, people)
    if problem:
        return problem
    graph = _session_graph(ctx)
    if graph is None:
        return Result("unknown", "Changes need a session, and there isn't one here.", {})
    r = depart(ctx.db, graph, handles)
    summary = f"Took {', '.join(r.people)} off stage in your own copy."
    if r.changes:
        worst = "; ".join(f"{c.system} {c.before} to {c.after}" for c in r.changes[:3])
        summary += f" {len(r.changes)} of {r.total_systems} systems dropped. Worst: {worst}."
    else:
        summary += " No number dropped: they carried no routes."
    _remember(ctx, [c.system for c in r.changes], summary, "departure")
    return Result(
        "ok",
        summary,
        {
            "people": r.people,
            "changes": [
                {"system": c.system, "before": c.before, "after": c.after} for c in r.changes[:10]
            ],
            "systems_changed": len(r.changes),
        },
    )


def propose_fix(ctx: Context, system: str) -> Result:
    name, problem = resolve_system(ctx, system)
    if problem:
        return problem
    p = propose(ctx.db, ctx.graph, name)
    if not p.fixes:
        summary = p.reason or f"No single change raises {name}'s number."
        return Result(
            "ok", summary, {"system": name, "understudy": p.before, "fixes": [], "tried": p.tried}
        )
    best = p.fixes[0]
    also = f" It also lifts {len(best.also)} other systems." if best.also else ""
    summary = f"{best.summary} That takes {name} from {p.before} to {p.before + best.gain}.{also}"
    _remember(ctx, [name], f"Suggested fix: {summary}", "fix_suggested")
    return Result(
        "ok",
        summary,
        {
            "system": name,
            "understudy": p.before,
            "tried": p.tried,
            "fixes": [
                {
                    "kind": f.kind,
                    "knower": f.knower,
                    "approver": f.approver,
                    "gain": f.gain,
                    "also": [
                        {"system": c.system, "before": c.before, "after": c.after}
                        for c in f.also[:5]
                    ],
                    "summary": f.summary,
                    "owners_diff": f.owners_diff,
                }
                for f in p.fixes[:3]
            ],
            "remembered": _remembered(ctx, name),
        },
    )


def make_fix(
    ctx: Context, system: str, kind: str, knower: str, approver: str | None = None
) -> Result:
    name, problem = resolve_system(ctx, system)
    if problem:
        return problem
    people = [knower] + ([approver] if approver else [])
    _, problem = resolve_people(ctx, people)
    if problem:
        return problem
    graph = _session_graph(ctx)
    if graph is None:
        return Result("unknown", "Changes need a session, and there isn't one here.", {})
    try:
        fix = checked_fix(ctx.db.select_graph(graph), name, kind, knower, approver)
    except FixRejected as e:
        return Result("unknown", e.message + " Nothing changed.", {})
    changes = apply_fix(ctx.db, graph, fix)
    moved = "; ".join(f"{c.system} {c.before} to {c.after}" for c in changes[:4])
    what = f"paired {knower} with {approver}" if kind == "pair" else f"made {knower} an approver"
    summary = f"In your copy, {what}. Numbers that changed: {moved or 'none'}."
    _remember(ctx, [c.system for c in changes] or [name], f"Fix applied: {summary}", "fix_applied")
    return Result(
        "ok",
        summary,
        {
            "system": name,
            "fix": {k: getattr(fix, k) for k in ("kind", "knower", "approver", "team")},
            "changes": [
                {"system": c.system, "before": c.before, "after": c.after} for c in changes
            ],
        },
    )


def who_can_review(ctx: Context, system: str) -> Result:
    name, problem = resolve_system(ctx, system)
    if problem:
        return problem
    g = ctx.db.select_graph(ctx.graph)
    rows = g.ro_query(
        """MATCH (p:Person)-[k:KNOWS]->(s:System {name: $s})<-[:DEPENDS_ON]-(t:Team)
           WHERE p.left_at IS NULL
           OPTIONAL MATCH (p)-[m:MEMBER_OF]->(t)
           OPTIONAL MATCH (p)-[:COLLABORATES]->(a:Person)-[:MEMBER_OF]->(t)
           WITH p, k, s, m IS NOT NULL AS approves, count(DISTINCT a) AS works_with
           OPTIONAL MATCH (p)-[c:CRITICAL]->(s)
           RETURN p.handle, k.score, approves, works_with, c IS NOT NULL
           ORDER BY k.score DESC, p.handle LIMIT 6""",
        {"s": name},
    ).result_set
    if not rows:
        return Result(
            "not_in_graph",
            f"Nobody active in the last 18 months knows {name}, so there is no one to suggest.",
            {"system": name, "reviewers": []},
        )
    reviewers = [
        {
            "handle": h,
            "score": round(score, 2),
            "approves": approves,
            "works_with_approvers": works_with,
            "critical": critical,
        }
        for h, score, approves, works_with, critical in rows
    ]
    names = ", ".join(r["handle"] for r in reviewers[:3])
    summary = f"The people who know {name} best are {names}."
    spare = [r["handle"] for r in reviewers if not r["critical"] and not r["approves"]]
    if spare:
        summary += f" {spare[0]} isn't critical anywhere here, so their reviews add a route."
    return Result("ok", summary, {"system": name, "reviewers": reviewers})


def why(ctx: Context, system: str) -> Result:
    name, problem = resolve_system(ctx, system)
    if problem:
        return problem
    g = ctx.db.select_graph(ctx.graph)
    rows = g.ro_query(
        """MATCH (d:Decision)-[r:SHAPED]->(:System {name: $s})
           RETURN d.number, d.title, d.stage, d.status, d.url, d.approvers,
                  d.approvers_total, r.prs
           ORDER BY r.prs DESC, d.number LIMIT 5""",
        {"s": name},
    ).result_set
    if not rows:
        return Result(
            "not_in_graph",
            f"No merged PR touching {name} since January 2024 names a KEP, so the graph "
            "can't say which design decisions shaped it.",
            {"system": name, "decisions": []},
        )
    around = {
        r[0]
        for r in g.ro_query(
            """MATCH (d:Decision)-[:SHAPED]->(:System {name: $s})
               UNWIND d.approvers AS a
               MATCH (p:Person {handle: a}) WHERE p.left_at IS NULL
               RETURN DISTINCT p.handle""",
            {"s": name},
        ).result_set
    }
    decisions = [
        {
            "kep": number,
            "title": title,
            "stage": stage_,
            "status": status,
            "url": url,
            "prs": prs,
            "approvers_still_around": sorted(set(approvers or []) & around),
            "approvers_still_active": len(set(approvers or []) & around),
            "approvers_total": total,
        }
        for number, title, stage_, status, url, approvers, total, prs in rows
    ]
    top = decisions[0]
    summary = (
        f"The design decision behind the most work on {name} is KEP-{top['kep']}, "
        f'"{top["title"]}" ({top["prs"]} PRs). {len(top["approvers_still_around"])} of its '
        f"{top['approvers_total']} approvers are still active here."
    )
    return Result("ok", summary, {"system": name, "decisions": decisions})


def recall_findings(ctx: Context, system: str) -> Result:
    name, problem = resolve_system(ctx, system)
    if problem:
        return problem
    found = memory.recall(ctx.db, ctx.visitor, name) if ctx.visitor else []
    if not found:
        return Result(
            "ok", f"Nothing remembered about {name} yet.", {"system": name, "findings": []}
        )
    latest = found[0]
    return Result(
        "memory",
        f"From {latest['at'][:10]}: {latest['text']}",
        {"system": name, "findings": found},
    )


# --- the catalogue ------------------------------------------------------------------

SYSTEM = {"type": "string", "description": "A system name, a path inside one, or words for it"}
PEOPLE = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Pseudonyms like contributor-1a2b",
    "maxItems": MAX_PEOPLE,
}


def _schema(props: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


TOOLS: list[Tool] = [
    Tool(
        "find_system",
        "Resolve what the user called a system to its exact name, or list candidates.",
        _schema({"query": {"type": "string"}}, ["query"]),
        find_system,
    ),
    Tool(
        "understudies",
        "How exposed a system is: people who know it, approvers, the understudy number "
        "(how many people can leave before no approver can reach anyone who knows it), "
        "its routes and its critical people. With no system, the riskiest systems.",
        _schema({"system": SYSTEM, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, []),
        understudies,
    ),
    Tool(
        "rehearse_departure",
        "What breaks if these people leave: copies the graph, takes them off it and "
        "compares every number they carried. Changes nothing.",
        _schema({"people": PEOPLE}, ["people"]),
        rehearse_departure,
    ),
    Tool(
        "propose_fix",
        "The cheapest changes that raise a system's understudy number, each tried on its "
        "own copy of the graph, best first.",
        _schema({"system": SYSTEM}, ["system"]),
        propose_fix,
    ),
    Tool(
        "who_can_review",
        "Who knows a system best and whether they approve it, work with its approvers, "
        "or are critical to it.",
        _schema({"system": SYSTEM}, ["system"]),
        who_can_review,
    ),
    Tool(
        "why",
        "The design decisions (Kubernetes KEPs) behind a system, from the PRs that name "
        "them, and whether the people who approved them are still active.",
        _schema({"system": SYSTEM}, ["system"]),
        why,
    ),
    Tool(
        "recall",
        "What earlier sessions found about a system, with dates.",
        _schema({"system": SYSTEM}, ["system"]),
        recall_findings,
    ),
    Tool(
        "take_off_stage",
        "Take people off stage for real in the user's own copy of the company.",
        _schema({"people": PEOPLE}, ["people"]),
        take_off_stage,
        acts=True,
    ),
    Tool(
        "apply_fix",
        "Apply a fix in the user's own copy: 'pair' a knower with an approver, or "
        "'approve' (make the knower an approver).",
        _schema(
            {
                "system": SYSTEM,
                "kind": {"type": "string", "enum": ["pair", "approve"]},
                "knower": {"type": "string"},
                "approver": {"type": "string"},
            },
            ["system", "kind", "knower"],
        ),
        make_fix,
        acts=True,
    ),
]
BY_NAME = {t.name: t for t in TOOLS}


def call(ctx: Context, name: str, args: dict[str, Any]) -> Result:
    """Run a tool by name with arguments from a model or an MCP client. Unknown tools
    and arguments are refused rather than guessed at."""
    tool = BY_NAME.get(name)
    if tool is None:
        return Result("unknown", f"There is no tool called {name}.", {})
    allowed = set(tool.parameters["properties"])
    if extra := set(args) - allowed:
        return Result("unknown", f"{name} doesn't take {', '.join(sorted(extra))}.", {})
    if missing := [p for p in tool.parameters["required"] if p not in args]:
        return Result("unknown", f"{name} needs {', '.join(missing)}.", {})
    return tool.run(ctx, **args)
