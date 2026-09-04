"""Prompt rollback: moving the production label back to an earlier version,
and timing it.

**Why this is worth a module rather than a UI click.** "Rollback is possible"
is not a claim worth making - every system's rollback is possible in
principle. What matters is whether it has *happened*, how long it took, and
whether the thing that was rolled back actually stopped being served. This
records all three, so the number that gets published is measured rather than
estimated.

**What a rollback here actually is.** The production prompt is whatever
version carries the ``production`` label (see llm/prompts.py). Rolling back is
moving that label to an earlier version. No code changes, no deploy, no
restart - which is the entire argument for having a registry in the first
place.

**The elapsed time is not the whole story, and this says so.** Moving the
label is near-instant. What the caller actually experiences is bounded by
``prompts.CACHE_TTL_SECONDS``, because a running process serves its cached
copy until that expires. Both numbers are reported: the label move, and the
worst-case time until every running process is serving the rolled-back
version. Publishing only the first would be true and misleading.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from tilsynsagent.llm.prompts import CACHE_TTL_SECONDS, PRODUCTION_LABEL


@dataclass(frozen=True)
class RollbackResult:
    prompt_name: str
    from_version: int | None
    to_version: int
    label_move_seconds: float
    verified_version: int | None

    @property
    def worst_case_propagation_seconds(self) -> float:
        """When every already-running process is guaranteed to have stopped
        serving the old version: the label move plus one full cache TTL."""
        return self.label_move_seconds + CACHE_TTL_SECONDS

    @property
    def verified(self) -> bool:
        return self.verified_version == self.to_version

    def summary(self) -> str:
        status = "verified" if self.verified else f"NOT VERIFIED (reads back v{self.verified_version})"
        return (
            f"{self.prompt_name}: v{self.from_version} -> v{self.to_version} "
            f"in {self.label_move_seconds:.2f}s ({status}); "
            f"worst case {self.worst_case_propagation_seconds:.0f}s until every "
            f"running process has it, bounded by the {CACHE_TTL_SECONDS}s prompt cache"
        )


def current_version(name: str, *, label: str = PRODUCTION_LABEL) -> int | None:
    """The version the label currently points at, read from Langfuse
    uncached - a rollback must not be measured against a stale local copy."""
    from langfuse import get_client

    prompt = get_client().get_prompt(name, label=label, cache_ttl_seconds=0)
    return getattr(prompt, "version", None)


def list_versions(name: str) -> list[int]:
    """Every version of this prompt, oldest first."""
    from langfuse import get_client

    client = get_client()
    response = client.api.prompts.list(name=name)
    versions: list[int] = []
    for item in response.data:
        versions.extend(getattr(item, "versions", None) or [])
    return sorted(set(versions))


def rollback(name: str, *, to_version: int, label: str = PRODUCTION_LABEL) -> RollbackResult:
    """Moves the label to ``to_version`` and times it.

    The clock covers exactly the operation an operator performs: the label
    move. Reading the version back afterwards is verification, deliberately
    outside the timed section - a rollback that is fast but did not take
    effect is not a fast rollback.
    """
    from langfuse import get_client

    client = get_client()
    from_version = current_version(name, label=label)

    start = time.monotonic()
    client.update_prompt(name=name, version=to_version, new_labels=[label])
    label_move_seconds = time.monotonic() - start

    verified = current_version(name, label=label)
    return RollbackResult(
        prompt_name=name,
        from_version=from_version,
        to_version=to_version,
        label_move_seconds=label_move_seconds,
        verified_version=verified,
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Roll a prompt back to an earlier version.")
    parser.add_argument("name", help="Prompt name, e.g. tilsynsagent-summarise-system")
    parser.add_argument(
        "--to", type=int, default=None, help="Version to roll back to. Default: the previous one."
    )
    parser.add_argument("--list", action="store_true", help="List versions and exit.")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()

    from tilsynsagent import obs

    if not obs.observability_enabled():
        raise SystemExit("LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY not set.")

    versions = list_versions(args.name)
    current = current_version(args.name)
    print(f"{args.name}: versions {versions}, production = v{current}")

    if args.list:
        obs.shutdown()
        return 0

    target = args.to
    if target is None:
        earlier = [v for v in versions if current is not None and v < current]
        if not earlier:
            print("no earlier version to roll back to.")
            obs.shutdown()
            return 1
        target = earlier[-1]

    result = rollback(args.name, to_version=target)
    print(result.summary())
    obs.shutdown()
    return 0 if result.verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
