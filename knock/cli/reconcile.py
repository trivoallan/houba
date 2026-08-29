"""The `knock reconcile <dir>` command (copy path)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from knock.cli._di import build_container
from knock.cli.render import render_report
from knock.errors import ConfigError
from knock.logging import configure
from knock.use_cases.loader import load_policy_dir
from knock.use_cases.reconcile import reconcile_policies
from knock.use_cases.report import report_exit_code


def reconcile(
    directory: Annotated[Path, typer.Argument(help="Directory of MirrorPolicy files (recursive).")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Plan only — no copies, no deletes.")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Unfold per-operation detail in text output.")
    ] = False,
    concurrency: Annotated[
        int | None,
        typer.Option(
            "--concurrency",
            "-j",
            min=1,
            help="Max parallel tag operations (overrides KNOCK_MAX_CONCURRENCY; 1 = sequential).",
        ),
    ] = None,
    shard_index: Annotated[
        int,
        typer.Option(
            "--shard-index",
            min=0,
            help="This shard's 0-based index (pass $JOB_COMPLETION_INDEX in an Indexed Job).",
        ),
    ] = 0,
    shard_count: Annotated[
        int,
        typer.Option("--shard-count", min=1, help="Total shards N (1 = process all policies)."),
    ] = 1,
    report_json: Annotated[
        bool,
        typer.Option(
            "--report-json",
            help="Emit the reconcile report as JSON to stdout (for piping to `knock scan enqueue`).",  # noqa: E501
        ),
    ] = False,
) -> None:
    """Reconcile all MirrorPolicy files under DIRECTORY against their destinations."""
    container = build_container()
    configure(format_=container.settings.log_format, level=container.settings.log_level)

    if shard_index >= shard_count:
        raise ConfigError(f"--shard-index ({shard_index}) must be < --shard-count ({shard_count})")

    policies = load_policy_dir(directory)
    report = reconcile_policies(
        policies,
        registry=container.registry,
        builder=container.builder,
        source=container.source,
        archiver=container.archiver,
        roster=container.settings.registries,
        ca_certs=container.settings.transform_ca_certs,
        package_mirrors=container.settings.transform_package_mirrors,
        build_platform=container.settings.build_platform,
        now=container.clock.now(),
        label_prefix=container.settings.label_prefix,
        dry_run_tags=dry_run or container.settings.dry_run_tags,
        dry_run_deletions=dry_run or container.settings.dry_run_deletions,
        deletion_mode=container.settings.deletion_mode,
        retention_global=container.settings.retention,
        reporter=container.reporter,
        work_dir=container.settings.work_dir,
        max_concurrency=(
            concurrency if concurrency is not None else container.settings.max_concurrency
        ),
        shard_index=shard_index,
        shard_count=shard_count,
        attestor=container.attestor,
        attest_builder_id=container.settings.attest_builder_id,
        sbom_generator=container.sbom_generator,
        sbom_formats=container.settings.sbom_formats,
    )
    fmt = "json" if report_json else container.settings.log_format
    render_report(report, fmt=fmt, verbose=verbose, stream=sys.stdout)
    raise typer.Exit(report_exit_code(report))
