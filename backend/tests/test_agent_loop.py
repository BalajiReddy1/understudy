import json
import os
from types import SimpleNamespace as NS

import httpx
import openai
import pytest
from test_agent_tools import P, load, pseudonymised

from understudy.agent.loop import ask, check, route
from understudy.agent.tools import Context, Result
from understudy.agent.trace import Recorder, TracedDB
from understudy.db import drop
from understudy.sample import SAMPLE_ORG

GRAPH = "test:loop"


def tool_call(i, name, args):
    raw = args if isinstance(args, str) else json.dumps(args)
    return NS(id=f"call{i}", function=NS(name=name, arguments=raw))


def message(content=None, calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=calls))])


class Script:
    """A fake chat client that plays back responses and records what it was sent."""

    def __init__(self, responses, failing_models=()):
        self.responses = list(responses)
        self.failing = set(failing_models)
        self.calls = []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        if kwargs["model"] in self.failing:
            raise openai.APITimeoutError(request=httpx.Request("POST", "https://test"))
        return self.responses.pop(0)


@pytest.fixture
def ctx(db):
    load(db, GRAPH, pseudonymised(SAMPLE_ORG))
    yield Context(TracedDB(db, Recorder()), GRAPH)
    drop(db, GRAPH)


def test_check():
    rs = [
        Result(
            "ok",
            "pkg/kubelet: 9 people know it and 6 approve it, but its understudy number is 1.",
            {
                "system": "pkg/kubelet",
                "routes": [["contributor-ab12"]],
                "kep": 2535,
                "url": "https://github.com/kubernetes/enhancements/tree/master/keps/x",
            },
        )
    ]
    assert check("pkg/kubelet has 9 knowers but only 1 route, via contributor-ab12.", rs) is None
    assert check("See KEP-2535 at github.com/kubernetes/enhancements.", rs) is None
    assert "pkg/proxy" in check("pkg/proxy is also at risk.", rs)
    assert "contributor-ffff" in check("Ask contributor-ffff.", rs)
    assert "KEP-1234" in check("It came from KEP-1234.", rs)
    assert "7" in check("Its number is 7.", rs)  # a number at the end of a sentence
    assert check("Losing them leaves 2 of 9.", rs, question="what if 2 people left") is None


def test_a_clean_answer(ctx):
    model = Script(
        [
            message(calls=[tool_call(1, "understudies", {"system": "billing"})]),
            message("billing has 3 people who know it and 1 approver, so its number is 1."),
        ]
    )
    events = []
    reply = ask("how exposed is billing?", ctx, client=model, emit=events.append)
    assert reply.checked and reply.kind == "path" and reply.model == "openai/gpt-oss-120b"
    assert reply.text.startswith("billing has 3 people")
    assert [s.tool for s in reply.steps] == ["understudies"]
    assert reply.steps[0].queries and any(
        "algo.maxFlow" in q["cypher"] for q in reply.steps[0].queries
    )
    assert events[0]["type"] == "tool" and events[0]["status"] == "ok"
    # The model saw the tool result, as JSON, before answering.
    sent = model.calls[1]["messages"][-1]
    assert sent["role"] == "tool" and json.loads(sent["content"])["data"]["understudy"] == 1


@pytest.mark.parametrize(
    "made_up",
    [
        "billing is fine, ask contributor-ffff.",
        "billing depends on pkg/payments/core too.",
        "billing has 12 people who know it.",
    ],
)
def test_made_up_answers_are_replaced(ctx, made_up):
    model = Script(
        [message(calls=[tool_call(1, "understudies", {"system": "billing"})]), message(made_up)]
    )
    reply = ask("how exposed is billing?", ctx, client=model)
    assert not reply.checked and reply.note
    assert reply.text.startswith("billing: 3 people know it")  # the tool's own sentence


def test_bad_arguments_and_unknown_names(ctx):
    model = Script(
        [
            message(calls=[tool_call(1, "understudies", "{not json")]),
            message(calls=[tool_call(2, "propose_fix", {"system": "payroll"})]),
            message("I couldn't find a system called payroll."),
        ]
    )
    reply = ask("fix payroll", ctx, client=model)
    assert [s.status for s in reply.steps] == ["unknown", "unknown"]
    assert reply.kind == "unknown" and reply.checked


def test_a_busy_model_falls_back(ctx):
    model = Script(
        [message(calls=[tool_call(1, "understudies", {})]), message("The riskiest is billing.")],
        failing_models={"openai/gpt-oss-120b"},
    )
    events = []
    reply = ask("what's riskiest?", ctx, client=model, emit=events.append)
    assert reply.model == "openai/gpt-oss-20b" and reply.checked
    assert any(e["type"] == "notice" for e in events)


def test_every_model_busy(ctx):
    model = Script([], failing_models={"openai/gpt-oss-120b", "openai/gpt-oss-20b"})
    reply = ask(f"what if {P['dev']} left billing?", ctx, client=model)
    assert reply.model is None and not reply.checked
    assert [s.tool for s in reply.steps] == ["rehearse_departure", "understudies"]
    assert "would drop" in reply.text


def test_no_key_routes_the_question(ctx):
    _, steps = route("why is the billing number so low? any fix?", ctx)
    assert [s.tool for s in steps] == ["understudies", "why", "propose_fix"]
    assert route("hello there", ctx)[1][0].tool == "understudies"  # falls back to the board


@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_MODEL"),
    reason="spends free-tier quota; set RUN_LIVE_MODEL=1 to run against Groq",
)
def test_live_model(ctx):
    reply = ask("How exposed is billing, and what's the cheapest fix?", ctx)
    print(reply)
    assert reply.model is not None and reply.steps
    assert reply.kind == "path"


def test_a_skipped_tool_is_asked_for(ctx):
    # Asked for a fix, the model answers from understudies alone. It gets sent back
    # once to call propose_fix, and must call a tool on that turn.
    model = Script(
        [
            message(calls=[tool_call(1, "understudies", {"system": "billing"})]),
            message("Add another approver to billing."),
            message(calls=[tool_call(2, "propose_fix", {"system": "billing"})]),
            message("billing is at 1; making one more knower an approver takes it to 2."),
        ]
    )
    events = []
    reply = ask(
        "how exposed is billing and what's the cheapest fix?", ctx, client=model, emit=events.append
    )
    assert [s.tool for s in reply.steps] == ["understudies", "propose_fix"]
    assert reply.checked and "takes it to 2" in reply.text
    nudge = model.calls[2]["messages"][-1]
    assert nudge["role"] == "user" and "propose_fix" in nudge["content"]
    assert model.calls[2]["tool_choice"] == "required"
    assert any(e["type"] == "notice" and "propose_fix" in e["text"] for e in events)


def test_number_words_and_sweeping_claims_are_checked():
    rs = [Result("ok", "pkg/kubelet/cm: 0 of its 1 approvers are still active here.", {"n": 0})]
    # Presence, not meaning: 1 was returned (as the total), so this passes even though
    # 0 are active. The check stops invented names and numbers, not misreadings.
    assert check("Only one approver is still active.", rs) is None
    assert check("Two approvers are still active.", rs) == "said 'two', but no tool returned 2"
    assert check("No one knows; none of its approvers are active.", rs) == (
        "claimed 'none of', which no tool said"
    )
    said_it = [Result("ok", "No approver has a line to anyone who knows it.", {"n": 0})]
    assert check("No approver has a line to anyone.", said_it) is None
    assert "no remaining" in check("They have no remaining approvers.", rs)


def test_router_says_when_a_path_is_not_modelled(ctx):
    results, steps = route("How safe is test/e2e/node?", ctx)
    assert [s.tool for s in steps] == ["find_system"]
    assert results[0].status == "not_in_graph"


def test_a_stumbling_model_is_retried(ctx):
    class Stumbles(Script):
        def create(self, **kwargs):
            if not self.calls:
                self.calls.append(kwargs)
                raise openai.BadRequestError(
                    "Error code: 400 - tool_use_failed",
                    response=httpx.Response(400, request=httpx.Request("POST", "https://t")),
                    body=None,
                )
            return super().create(**kwargs)

    model = Stumbles(
        [message(calls=[tool_call(1, "understudies", {})]), message("The riskiest is billing.")]
    )
    reply = ask("what's riskiest?", ctx, client=model)
    assert reply.model == "openai/gpt-oss-120b" and reply.checked


def bad_request(name, args):
    generated = json.dumps({"name": name, "arguments": args})
    return openai.BadRequestError(
        "Error code: 400 - tool_use_failed",
        response=httpx.Response(400, request=httpx.Request("POST", "https://t")),
        body={"error": {"code": "tool_use_failed", "failed_generation": generated}},
    )


def test_a_misspelled_tool_is_repaired(ctx):
    class Misspells(Script):
        def create(self, **kwargs):
            if not self.calls:
                self.calls.append(kwargs)
                raise bad_request("unstudies", {"system": "billing"})
            return super().create(**kwargs)

    model = Misspells([message("billing's number is 1.")])
    reply = ask("how exposed is billing?", ctx, client=model)
    assert [s.tool for s in reply.steps] == ["understudies"] and reply.checked
    assert reply.model == "openai/gpt-oss-120b"


def test_a_short_rate_limit_is_waited_out(ctx):
    class Limited(Script):
        def create(self, **kwargs):
            if not self.calls:
                self.calls.append(kwargs)
                raise openai.RateLimitError(
                    "Rate limit reached. Please try again in 0.01s.",
                    response=httpx.Response(429, request=httpx.Request("POST", "https://t")),
                    body=None,
                )
            return super().create(**kwargs)

    model = Limited(
        [message(calls=[tool_call(1, "understudies", {})]), message("The riskiest is billing.")]
    )
    events = []
    reply = ask("what's riskiest?", ctx, client=model, emit=events.append)
    assert reply.model == "openai/gpt-oss-120b"  # waited, didn't drop to the small model
    assert any("waiting" in e.get("text", "") for e in events)


def test_citations():
    from understudy.agent.loop import Reply, citations

    data = {
        "system": "pkg/kubelet/apis/config",
        "systems": [{"name": "pkg/kubelet"}],
        "decisions": [
            {"kep": 2535, "title": "Ensure Secret Pulled Images", "url": "https://x/2535"}
        ],
    }
    text = (
        "pkg/kubelet/apis/config scores 1; contributor-ab12 is critical. "
        "KEP-2535 shaped it, and contributor-ab12 approved it."
    )
    cites = citations(Reply(text, "path", True, [], None, None, [Result("ok", "", data)]))
    # pkg/kubelet is in the results but only appears inside a longer name, so it isn't cited.
    assert cites["systems"] == ["pkg/kubelet/apis/config"]
    assert cites["people"] == ["contributor-ab12"]
    assert cites["keps"] == [
        {"number": 2535, "title": "Ensure Secret Pulled Images", "url": "https://x/2535"}
    ]
