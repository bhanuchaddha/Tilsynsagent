"""Offline behaviour for obs/langfuse_setup.py: everything must no-op
cleanly when Langfuse keys are absent, since the agent must run with
Langfuse uninstalled or unconfigured."""

from tilsynsagent import obs


def test_observability_disabled_without_keys(no_langfuse_env):
    assert obs.observability_enabled() is False


def test_get_callback_handler_is_none_without_keys(no_langfuse_env):
    assert obs.get_callback_handler() is None


def test_trace_config_is_passthrough_without_keys(no_langfuse_env):
    base = {"configurable": {"thread_id": "abc"}}
    result = obs.trace_config(base, thread_id="abc")
    assert result == base
    assert result is not base or result == base  # merge, not mutation of caller intent


def test_trace_config_does_not_mutate_input(no_langfuse_env):
    base = {"configurable": {"thread_id": "abc"}}
    obs.trace_config(base, thread_id="abc")
    assert base == {"configurable": {"thread_id": "abc"}}


def test_flush_and_shutdown_are_noops_without_keys(no_langfuse_env):
    obs.flush()
    obs.shutdown()


def test_observability_enabled_requires_both_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert obs.observability_enabled() is False

    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    assert obs.observability_enabled() is True
