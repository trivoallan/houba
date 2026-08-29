"""One planner per source class, so `reconcile` is a driver rather than a path plus a filter.

Not a port: `ports/` is reserved for I/O boundaries an adapter implements, and this is an
internal seam between use cases. What differs between planners lives in their constructors
— the registry planner takes a builder, transform rosters, an SBOM generator and an
attestor; the git planner takes a source and an archiver — which is what keeps this
protocol small enough to be a contract rather than a copy of a signature.

Batch-at-a-time, not policy-at-a-time: `plan` returns the alias entries the driver needs
for its cross-planner collision check, and each planner keeps its own plan representation
private.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from knock.domain.collision import AliasTarget
from knock.domain.mirror_policy import MirrorPolicy
from knock.ports.reporter import Reporter
from knock.use_cases.report import PolicyReport


class PolicyPlanner(Protocol):
    def handles(self, policy: MirrorPolicy) -> bool:
        """True when this planner owns `policy`, decided on its source class."""
        ...

    def plan(self, policies: list[MirrorPolicy]) -> list[AliasTarget]:
        """Cheap reads only — **never a mutation of a destination registry**, and never
        an expensive materialisation. Configuring local access to read a source (a
        registry login, a credential helper) is fine and expected; placing, deleting or
        annotating anything is not. Raises to fail the whole run before anything is
        written. Returns the alias entries the driver collision-checks across planners.
        """
        ...

    def apply(
        self, *, reporter: Reporter, executor: ThreadPoolExecutor | None
    ) -> list[PolicyReport]:
        """Act on the batch `plan` accepted, one isolated `PolicyReport` per policy.

        `reporter` and `executor` are parameters, not constructor fields, because of
        the rule that keeps this protocol small: what the DRIVER owns or shares across
        planners is passed in per call; what only one planner needs (its builder, its
        archiver, its transform rosters) is a constructor field. Concurrency and
        reporting are driver-wide, so they arrive here.
        """
        ...
