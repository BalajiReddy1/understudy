"""The agent: a small tool loop over an OpenAI-compatible chat API (Groq's free tier).

The model picks tools from the fixed catalogue and writes the final sentence; it never
writes a query. Every tool call is traced (arguments, answer type, time, the Cypher
that ran), and the reply is checked before anyone sees it: every system, pseudonym,
KEP and number it mentions must appear in what the tools returned. A reply that fails
the check is replaced by the tools' own sentences, so the agent gets flatter rather
than wrong. With no key, or when the model is busy, a fixed router runs the obvious
tools for whatever the question names, and the answer is those sentences.
"""

import json
import os
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import get_close_matches
from types import SimpleNamespace
from typing import Any

from openai import APIError, BadRequestError, OpenAI, RateLimitError

from .. import config  # noqa: F401  (loads .env, where the model key lives)
from .tools import TOOLS, Context, Result, call, match_system, system_names

MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]  # the second is the fallback
BASE_URL = "https://api.groq.com/openai/v1"
MAX_STEPS = 6
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)
HYPHENS = (chr(0x2010), chr(0x2011))  # hyphen and non-breaking hyphen

# Questions that need a particular tool. A model that answers one of these without
# calling it is sent back once to call it, because its answer would otherwise be
# general advice that no graph result backs up.
NEEDS = [
    (
        re.compile(r"\b(fix|cheapest|improve|raise|what (should|can) (we|i) do)", re.IGNORECASE),
        "propose_fix",
        "a fix",
    ),
    (
        re.compile(r"\b(why|decisions?|keps?|built the way|designed)\b", re.IGNORECASE),
        "why",
        "the decisions behind it",
    ),
    (re.compile(r"\breview", re.IGNORECASE), "who_can_review", "reviewers"),
]
MAX_TOOL_CHARS = 2500  # free-tier token limits are tight; tool results are trimmed

SYSTEM = """You are Understudy, an agent over a FalkorDB graph of Kubernetes: who knows each \
system (from 18 months of PRs and reviews), who approves it (OWNERS files), and the routes \
between them. A system's understudy number is how many people can leave before no approver \
can reach anyone who knows it.

Rules:
- Answer only from tool results. Never invent systems, people, numbers or KEPs.
- People are pseudonyms like contributor-1a2b. Never guess or ask for real names.
- If unsure what system the user means, call find_system. If a tool returns "unknown" or \
"not_in_graph", say that plainly; "not_in_graph" does not mean safe.
- When it helps, contrast the headcount (people who know it, approvers) with the \
understudy number.
- For "what if X left" use rehearse_departure; it changes nothing. Use take_off_stage or \
apply_fix only when the user asks to actually make the change; those touch only their \
own copy.
- Call the tools the question needs before answering; never describe what a tool could \
do instead of calling it. How exposed or risky a system is: understudies. What if someone \
leaves: rehearse_departure. A fix, the cheapest change, how to improve it: propose_fix, \
then report its top fix with the names and numbers it returned. Who should review: \
who_can_review. Why it is built this way, or which decisions shaped it: why. What was \
found before: recall. An unclear name: find_system.
- Explain from what the tools said and don't generalise beyond it: a drop from 2 to 1 \
still leaves a route, and only 0 means nobody is left. Add no opinions or labels the \
tools didn't give, such as "legacy" or "healthy".
- Reply in 2 to 4 short plain sentences. No markdown, no tables, no em dashes."""


@dataclass
class Step:
    tool: str
    args: dict
    status: str
    summary: str
    ms: float
    queries: list[dict] = field(default_factory=list)


@dataclass
class Reply:
    text: str
    kind: str  # "path", "memory", "not_in_graph" or "unknown"
    checked: bool  # the model's own sentence passed the answer check
    steps: list[Step]
    model: str | None  # None when no model was used
    note: str | None = None  # why the model's sentence wasn't used, if it wasn't
    results: list[Result] = field(default_factory=list, repr=False)


# --- the answer check ----------------------------------------------------------------

PATH = re.compile(r"\b[\w.-]+(?:/[\w.-]+)+\b")
PSEUDONYM = re.compile(r"\bcontributor-[0-9a-f]+\b")
KEP = re.compile(r"\bKEP[- ]?(\d+)\b", re.IGNORECASE)
NUMBER = re.compile(r"(?<![\w.-])\d+(?:\.\d+)?(?![\w-]|\.\d)")
WORDS = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
]
NUMBER_WORD = re.compile(r"(?<![\w-])(" + "|".join(WORDS) + r")(?![\w-])")
# Sweeping claims a model likes to add. They fail the check unless a tool said them.
ABSOLUTE = re.compile(
    r"\b(no remaining|no longer|nobody|no one left|none of|no approvers?|all of them|"
    r"every system|no route|lose all|all other)\b"
)


def _facts(results: list[Result]) -> tuple[set[str], set[str]]:
    """Every string and every number the tools returned."""
    strings, numbers = set(), set()

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list | tuple):
            for x in v:
                walk(x)
        elif isinstance(v, bool) or v is None:
            return
        elif isinstance(v, int | float):
            numbers.add(str(int(v)) if float(v).is_integer() else str(v))
        else:
            s = str(v)
            strings.add(s)
            numbers.update(NUMBER.findall(s))

    for r in results:
        walk(r.data)
        walk(r.summary)
    return strings, numbers


def check(text: str, results: list[Result], question: str = "") -> str | None:
    """None if every name and number in `text` came from the tools, else the reason.

    It checks presence, not meaning: a reply that relates two returned numbers wrongly
    ("1 approver is active" when the tool said 0 of 1) passes. Sweeping claims are the
    exception: they must appear in a tool's own sentence."""
    strings, numbers = _facts(results)
    numbers |= set(NUMBER.findall(question))
    blob = "\n".join(strings)
    for p in PATH.findall(text):
        if p not in blob:
            return f"named {p}, which no tool returned"
    for h in PSEUDONYM.findall(text):
        if h not in strings and h not in blob:
            return f"named {h}, who no tool returned"
    for k in KEP.findall(text):
        if k not in numbers:
            return f"cited KEP-{k}, which no tool returned"
    for n in NUMBER.findall(PATH.sub(" ", PSEUDONYM.sub(" ", text))):
        if n not in numbers:
            return f"used the number {n}, which no tool returned"
    for word, n in _number_words(text):
        if n not in numbers:
            return f"said '{word}', but no tool returned {n}"
    said = "\n".join(r.summary.lower() for r in results)
    for claim in ABSOLUTE.findall(text.lower()):
        if claim not in said:
            return f"claimed '{claim}', which no tool said"
    return None


def _number_words(text: str) -> list[tuple[str, str]]:
    """Number words with their digits. 'one' is left out in 'no one' and 'one of',
    where it isn't a count."""
    out = []
    for m in NUMBER_WORD.finditer(text.lower()):
        word = m.group(1)
        before, after = text.lower()[: m.start()], text.lower()[m.end() :]
        if word == "one" and (before.endswith("no ") or after.startswith(" of")):
            continue
        out.append((word, str(WORDS.index(word))))
    return out


def _tidy(text: str) -> str:
    """Plain spaces and no em dashes, whatever the model sends."""
    text = "".join(" " if unicodedata.category(c) == "Zs" else c for c in text)
    for h in HYPHENS:
        text = text.replace(h, "-")
    for dash in (EM_DASH, EN_DASH):
        text = text.replace(f" {dash} ", ", ")
    return text.replace(EM_DASH, ", ").strip()


def _kind(results: list[Result]) -> str:
    statuses = [r.status for r in results]
    if "memory" in statuses:
        return "memory"
    if "ok" in statuses:
        return "path"
    if "not_in_graph" in statuses:
        return "not_in_graph"
    return "unknown"


def _fallback(results: list[Result]) -> str:
    seen, out = set(), []
    for r in results:
        if r.summary not in seen:
            seen.add(r.summary)
            out.append(r.summary)
    return " ".join(out[-3:]) or "The graph has nothing to say about that yet."


def citations(reply: "Reply") -> dict:
    """The systems, people and KEPs the reply names, in the order it names them, so the
    UI can link each one to its place on the stage. Only things a tool returned count."""
    text = reply.text
    systems: set[str] = set()
    keps: dict[int, dict] = {}

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            if isinstance(v.get("kep"), int) and v.get("url"):
                keps[v["kep"]] = {"number": v["kep"], "title": v.get("title"), "url": v["url"]}
            for k, x in v.items():
                if k in ("system", "name") and isinstance(x, str):
                    systems.add(x)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for r in reply.results:
        walk(r.data)
    # A name counts only as a whole: pkg/kubelet isn't cited by pkg/kubelet/cm.
    named = [
        (m.start(), s) for s in systems if (m := re.search(re.escape(s) + r"(?![\w/-])", text))
    ]
    return {
        "systems": [s for _, s in sorted(named)],
        "people": list(dict.fromkeys(PSEUDONYM.findall(text))),
        "keps": [keps[int(n)] for n in dict.fromkeys(KEP.findall(text)) if int(n) in keps],
    }


# --- running tools ------------------------------------------------------------------


def _trim(v: Any, depth: int = 0) -> Any:
    if isinstance(v, dict):
        return {k: _trim(x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        kept = [_trim(x, depth + 1) for x in v[:5]]
        return kept + ([f"... {len(v) - 5} more"] if len(v) > 5 else [])
    return v


def _tool_message(r: Result) -> str:
    body = json.dumps({"status": r.status, "summary": r.summary, "data": _trim(r.data)})
    return body if len(body) <= MAX_TOOL_CHARS else body[:MAX_TOOL_CHARS] + "... (cut)"


def _run_tool(ctx: Context, name: str, args: dict, emit) -> tuple[Result, Step]:
    recorder = getattr(ctx.db, "recorder", None)
    if recorder:
        recorder.take()
    t0 = time.perf_counter()
    r = call(ctx, name, args)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    queries = [q.__dict__ for q in recorder.take()] if recorder else []
    step = Step(name, args, r.status, r.summary, ms, queries)
    emit({"type": "tool", **step.__dict__})
    return r, step


def route(question: str, ctx: Context, emit=lambda e: None) -> tuple[list[Result], list[Step]]:
    """No model: run the obvious tools for whatever the question names."""
    results, steps = [], []

    def run(name, args):
        r, s = _run_tool(ctx, name, args, emit)
        results.append(r)
        steps.append(s)

    people = PSEUDONYM.findall(question.lower())
    paths = PATH.findall(question)
    words = re.sub(r"[^\w\s/.-]", " ", question.lower()).split()
    names = system_names(ctx)
    system = None
    grams = [" ".join(words[i : i + n]) for n in (3, 2, 1) for i in range(len(words) - n + 1)]
    for candidate in paths + grams:
        if len(candidate) < 3:
            continue
        name, problem = match_system(names, candidate)
        if name:
            system = name
            break
        if candidate in paths and problem.status == "not_in_graph":
            # A real path nothing modelled covers: say so, don't show something else.
            run("find_system", {"query": candidate})
            return results, steps
    if people:
        run("rehearse_departure", {"people": people})
    if system:
        run("understudies", {"system": system})
        if re.search(r"\bwhy\b|\bkep\b|decision", question.lower()):
            run("why", {"system": system})
        if re.search(r"\bfix|cheap|improve|raise|help\b", question.lower()):
            run("propose_fix", {"system": system})
        if re.search(r"review", question.lower()):
            run("who_can_review", {"system": system})
    if not results:
        run("understudies", {})
    return results, steps


# --- the loop -----------------------------------------------------------------------


def _client():
    key = os.environ.get("GROQ_API_KEY") or os.environ.get("groq_api_key")
    if not key:
        return None
    return OpenAI(api_key=key, base_url=BASE_URL, timeout=30, max_retries=0)


RETRY_AFTER = re.compile(r"try again in ([\d.]+)s")
MAX_WAIT_SECONDS = 8  # longer than this, and the fallback model is the better bet


def _repaired(e: BadRequestError):
    """Groq rejects a call to a tool that doesn't exist with 400 'tool_use_failed' and
    includes what the model generated. gpt-oss-20b keeps calling 'unstudies'; when the
    name is that close to a real tool, the call is repaired and run instead of lost."""
    body = e.body if isinstance(e.body, dict) else {}
    err = body.get("error", body) if isinstance(body.get("error", body), dict) else {}
    if err.get("code") != "tool_use_failed":
        return None
    try:
        call_ = json.loads(err.get("failed_generation") or "")
    except ValueError:
        return None
    if not isinstance(call_, dict) or not isinstance(call_.get("arguments"), dict):
        return None
    close = get_close_matches(str(call_.get("name", "")), [t.name for t in TOOLS], 1, 0.75)
    if not close:
        return None
    fn = SimpleNamespace(name=close[0], arguments=json.dumps(call_["arguments"]))
    msg = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(id="repaired-0", function=fn)])
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _complete(client, model: str, messages: list[dict], required: bool, emit):
    """One chat request, made sturdier against the free tier's habits:
    - a call to a misspelled tool is repaired when the name is close to a real one;
    - a rate limit with a short "try again in N s" is waited out on the same model,
      which answers better than the smaller fallback."""
    for attempt in range(3):
        try:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                tools=_tool_specs(),
                tool_choice="required" if required else "auto",
                temperature=0.2,
                max_completion_tokens=900,
                reasoning_effort="low",
            )
        except BadRequestError as e:
            fixed = _repaired(e)
            if fixed is not None:
                return fixed
            if "tool_use_failed" not in str(e) or attempt == 2:
                raise
        except RateLimitError as e:
            wait = RETRY_AFTER.search(str(e))
            if not wait or float(wait.group(1)) > MAX_WAIT_SECONDS or attempt == 2:
                raise
            emit({"type": "notice", "text": f"The free model is busy; waiting {wait.group(1)} s."})
            time.sleep(float(wait.group(1)) + 0.5)
    raise AssertionError("unreachable")


def _tool_specs() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in TOOLS
    ]


def ask(
    question: str,
    ctx: Context,
    history: list[dict] | None = None,
    client=None,
    emit: Callable[[dict], None] = lambda e: None,
) -> Reply:
    client = client if client is not None else _client()
    if client is None:
        results, steps = route(question, ctx, emit)
        return Reply(
            _fallback(results), _kind(results), False, steps, None, "no model key", results
        )

    messages = [{"role": "system", "content": SYSTEM}]
    for turn in (history or [])[-6:]:
        if turn.get("role") in ("user", "assistant") and isinstance(turn.get("content"), str):
            messages.append({"role": turn["role"], "content": turn["content"][:1000]})
    messages.append({"role": "user", "content": question})

    results: list[Result] = []
    steps: list[Step] = []
    text, used, note = None, None, None
    nudged = force_tool = False
    for model in MODELS:
        try:
            for _ in range(MAX_STEPS):
                # The first step must be a tool call, so an answer always rests on the
                # graph rather than on what the model thinks is usual.
                response = _complete(
                    client, model, messages, required=not steps or force_tool, emit=emit
                )
                msg = response.choices[0].message
                force_tool = False
                if not msg.tool_calls:
                    text = _tidy(msg.content or "")
                    called = {s.tool for s in steps}
                    missing = [
                        (tool, what)
                        for rx, tool, what in NEEDS
                        if rx.search(question) and tool not in called
                    ]
                    if missing and not nudged:
                        nudged, force_tool, text = True, True, None
                        messages.append({"role": "assistant", "content": msg.content or ""})
                        messages.append(
                            {
                                "role": "user",
                                "content": "Before answering, call "
                                + " and ".join(t for t, _ in missing)
                                + ": the question asks for "
                                + " and ".join(w for _, w in missing)
                                + ", so the answer has to come from that result.",
                            }
                        )
                        emit(
                            {
                                "type": "notice",
                                "text": "Checking with " + ", ".join(t for t, _ in missing),
                            }
                        )
                        continue
                    break
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": c.id,
                                "type": "function",
                                "function": {
                                    "name": c.function.name,
                                    "arguments": c.function.arguments,
                                },
                            }
                            for c in msg.tool_calls
                        ],
                    }
                )
                for c in msg.tool_calls:
                    try:
                        args = json.loads(c.function.arguments or "{}")
                        if not isinstance(args, dict):
                            raise TypeError
                    except (ValueError, TypeError):
                        r = Result(
                            "unknown", f"The arguments for {c.function.name} weren't valid JSON."
                        )
                        step = Step(c.function.name, {}, r.status, r.summary, 0.0)
                        emit({"type": "tool", **step.__dict__})
                    else:
                        r, step = _run_tool(ctx, c.function.name, args, emit)
                    results.append(r)
                    steps.append(step)
                    messages.append(
                        {"role": "tool", "tool_call_id": c.id, "content": _tool_message(r)}
                    )
            used = model
            break
        except APIError as e:  # rate limits, timeouts, provider errors: try the next model
            note = f"{model} failed: {type(e).__name__}"
            emit({"type": "notice", "text": f"The model {model} is busy, trying another."})
            continue

    if used is None:
        # Every model is busy: answer from the graph alone.
        more, more_steps = route(question, ctx, emit) if not results else ([], [])
        results += more
        steps += more_steps
        return Reply(_fallback(results), _kind(results), False, steps, None, note, results)

    problem = "the model gave no answer" if not text else check(text, results, question)
    if problem:
        if not results:
            more, more_steps = route(question, ctx, emit)
            results += more
            steps += more_steps
        return Reply(_fallback(results), _kind(results), False, steps, used, problem, results)
    return Reply(text, _kind(results), True, steps, used, None, results)
