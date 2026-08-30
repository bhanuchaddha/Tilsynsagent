"""Offline tests for token accounting. No network, no real Groq client."""

from types import SimpleNamespace

from tilsynsagent.llm.usage import Usage


def _fake_resp(prompt=100, completion=50, total=150):
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=total
        )
    )


def test_from_groq_reads_usage_block():
    resp = _fake_resp(prompt=120, completion=40, total=160)
    usage = Usage.from_groq(resp, model="openai/gpt-oss-120b")
    assert usage.model == "openai/gpt-oss-120b"
    assert usage.prompt_tokens == 120
    assert usage.completion_tokens == 40
    assert usage.total_tokens == 160
    assert usage.latency_ms is None


def test_from_groq_carries_latency_when_given():
    resp = _fake_resp()
    usage = Usage.from_groq(resp, model="m", latency_ms=812.5)
    assert usage.latency_ms == 812.5


def test_to_dict_is_plain_dict():
    usage = Usage(model="m", prompt_tokens=10, completion_tokens=5, total_tokens=15)
    d = usage.to_dict()
    assert d == {
        "model": "m",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "latency_ms": None,
    }
    assert isinstance(d, dict)
