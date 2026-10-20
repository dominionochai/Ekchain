"""Click CLI entry point for Ekchain."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import click

from ekchain import __version__
from ekchain.patterns import (
    DB_DIR_ENV_VAR,
    DEFAULT_DB_DIR,
    DEFAULT_MAX_COMPACTIONS,
    DEFAULT_WORKSPACE_DIR,
    resolve_db_dir,
)

if TYPE_CHECKING:  # heavy imports stay inside the command bodies
    from rich.console import Console

    from ekchain.assets.fetch import Fetcher
    from ekchain.assets.install import Plan, Result, SetupOptions

#: The default ``.mcp.json`` shipped as package data, copied into a fresh
#: workspace on first run so a ``pipx``/``uv tool`` install works out of the box.
DEFAULT_MCP_CONFIG = Path(__file__).resolve().parent / "data" / "mcp.json"


def _is_interactive() -> bool:
    """Check if stderr is connected to an interactive terminal.

    Returns:
        True if stderr is a TTY (interactive session), False if piped or in CI.
    """
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


@click.group()
@click.version_option(version=__version__, prog_name="ekchain")
def cli() -> None:
    """Ekchain: climate forensics MCP server."""


@cli.command()
@click.option("--case-id", default=None, help="Pre-load this case on startup.")
@click.option(
    "--db-dir",
    default=DEFAULT_DB_DIR,
    envvar=DB_DIR_ENV_VAR,
    show_default=True,
    help="Directory containing per-case databases (env: EKCHAIN_DB_DIR).",
)
@click.option(
    "--transport",
    default="stdio",
    show_default=True,
    type=click.Choice(["stdio", "streamable-http"]),
    help="MCP transport to use.",
)
@click.option(
    "--workers",
    default=8,
    show_default=True,
    help="Number of parallel extraction workers for ingestion.",
)
@click.option(
    "--mem-limit",
    default=90.0,
    show_default=True,
    help="Memory usage %% threshold; tools wait when exceeded (0 to disable).",
)
@click.option(
    "--cpu-limit",
    default=90.0,
    show_default=True,
    help="CPU usage %% threshold; tools wait when exceeded (0 to disable).",
)
def serve(
    case_id: str | None,
    db_dir: str,
    transport: str,
    workers: int,
    mem_limit: float,
    cpu_limit: float,
) -> None:
    """Start the Ekchain MCP server.

    Without --case-id, starts in ready state. The agent can then call
    scan_evidence() to create a new case, or open_case() to load an
    existing one.  The agent drives analysis by calling climate tools
    (parse_cems_hourly, check_emissions_load_consistency,
    derive_operating_timeline) and other Tier 2 tools on demand.

    With --case-id, pre-loads that case so analysis tools work immediately.
    """
    from ekchain.server.app import init_server
    from ekchain.server.app import mcp as mcp_server

    db_dir_path = Path(db_dir).expanduser()
    db_dir_path.mkdir(parents=True, exist_ok=True)

    log_file = db_dir_path / "ekchain.log"
    file_handler = logging.FileHandler(str(log_file), mode="a")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(threadName)s] %(levelname)s %(name)s: %(message)s")
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)

    if case_id:
        click.echo(f"Initialising MCP server for case '{case_id}' ...", err=True)
    else:
        click.echo("Initialising MCP server (no case pre-loaded) ...", err=True)
    click.echo(f"Logging to {log_file}", err=True)

    init_server(
        db_dir=db_dir_path,
        case_id=case_id,
        max_workers=workers,
        mem_percent_limit=mem_limit,
        cpu_percent_limit=cpu_limit,
    )

    click.echo(f"Starting MCP server (transport={transport}) ...", err=True)
    # ``MCPServer.run`` is overloaded per transport, so the literal has to
    # reach it directly rather than through the click-supplied ``str``.
    if transport == "streamable-http":
        mcp_server.run(transport="streamable-http")
    else:
        mcp_server.run(transport="stdio")


@cli.command()
@click.argument("case_id")
@click.option(
    "--db-dir",
    default=DEFAULT_DB_DIR,
    envvar=DB_DIR_ENV_VAR,
    show_default=True,
    help="Directory containing per-case databases (env: EKCHAIN_DB_DIR).",
)
def report(case_id: str, db_dir: str) -> None:
    """Generate HTML + Markdown reports from an existing case database.

    Reads the case DB and audit log from DB_DIR, renders both report
    formats, and writes them alongside the database.  Does not require
    the MCP server to be running.
    """
    from ekchain.audit import AuditLog
    from ekchain.db import CaseDB
    from ekchain.report.renderer import ReportRenderer

    db_dir_path = Path(db_dir).expanduser()
    db_path = db_dir_path / f"{case_id}.db"
    audit_path = db_dir_path / f"{case_id}.audit.jsonl"

    if not db_path.exists():
        raise click.ClickException(f"Case database not found: {db_path}")

    click.echo(f"Loading case '{case_id}' from {db_path} ...")

    with CaseDB(db_path) as case_db:
        case_metadata = case_db.get_case_metadata()
        findings = case_db.get_findings()
        sources_list = case_db.get_sources()
        evidence_integrity = case_db.get_evidence_registry()

        audit = AuditLog(audit_path)
        audit_summary = audit.summary()

        click.echo(f"  {len(findings)} findings, {len(sources_list)} sources")

        renderer = ReportRenderer()

        md_path = db_dir_path / f"{case_id}.report.md"
        md_text = renderer.render(
            case_metadata=case_metadata,
            findings=findings,
            audit_summary=audit_summary,
            audit_log_path=audit_path,
            sources_list=sources_list,
            evidence_integrity=evidence_integrity,
        )
        md_path.write_text(md_text, encoding="utf-8")
        click.echo(f"  Markdown: {md_path}")

        _MAX_WINDOWS = 50
        source_names = [s.source_name for s in sources_list]
        bulk_windows = case_db.get_capped_windows_by_sources(source_names, _MAX_WINDOWS)
        source_windows: dict[str, list[dict[str, object]]] = {}
        for sname, (windows, total) in bulk_windows.items():
            source_windows[sname] = [
                {
                    "line_start": w.line_start,
                    "line_end": w.line_end,
                    "event_time": w.event_time,
                    "raw_text": w.raw_text,
                    "total": total,
                    "truncated": total > _MAX_WINDOWS,
                }
                for w in windows
            ]

        html_path = db_dir_path / f"{case_id}.report.html"
        try:
            html_text = renderer.render_html(
                case_metadata=case_metadata,
                findings=findings,
                audit_summary=audit_summary,
                audit_log_path=audit_path,
                sources_list=sources_list,
                evidence_integrity=evidence_integrity,
                source_windows=source_windows,
            )
            html_path.write_text(html_text, encoding="utf-8")
            click.echo(f"  HTML:     {html_path}")
        except Exception as exc:
            click.echo(f"  HTML generation failed: {exc}", err=True)

    click.echo("Done.")


@cli.command()
@click.argument("evidence_path")
@click.argument("case_id")
@click.option(
    "--model",
    default=None,
    help="Fallback model for all roles.",
)
@click.option(
    "--planner-model",
    default=None,
    help="Model for planner agents (decides what to do).",
)
@click.option(
    "--executor-model",
    default=None,
    help="Model for executor agents (calls tools).",
)
@click.option(
    "--analyst-model",
    default=None,
    help="Model for analyst agents (interprets results).",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(),
    help="YAML config file for models and settings.",
)
@click.option(
    "--effort",
    default="max",
    type=click.Choice(["max", "xhigh", "high"]),
    show_default=True,
    help="Effort level.",
)
@click.option(
    "--no-thinking",
    is_flag=True,
    help="Disable extended thinking for all queries; overrides --effort.",
)
@click.option(
    "--db-dir",
    default=DEFAULT_DB_DIR,
    envvar=DB_DIR_ENV_VAR,
    show_default=True,
    help="Case database directory (env: EKCHAIN_DB_DIR).",
)
@click.option(
    "--cwd",
    default=DEFAULT_WORKSPACE_DIR,
    envvar="EKCHAIN_CWD",
    show_default=True,
    help="Working directory for agent sessions (env: EKCHAIN_CWD).",
)
@click.option(
    "--workers",
    default=3,
    show_default=True,
    help="Max parallel extraction sessions.",
)
@click.option(
    "--max-compactions",
    default=DEFAULT_MAX_COMPACTIONS,
    envvar="EKCHAIN_MAX_COMPACTIONS",
    type=click.IntRange(min=0),
    show_default=True,
    help="Continuation sessions allowed after context exhaustion; 0 disables "
    "(env: EKCHAIN_MAX_COMPACTIONS).",
)
@click.option(
    "--proxy-config",
    default=None,
    type=click.Path(exists=True),
    help="LiteLLM config YAML for custom model routing.",
)
@click.option(
    "--show-cli-stderr",
    is_flag=True,
    help="Stream agent CLI diagnostics to the dashboard and orchestrator.log.",
)
def investigate(
    evidence_path: str,
    case_id: str,
    model: str | None,
    planner_model: str | None,
    executor_model: str | None,
    analyst_model: str | None,
    config_path: str | None,
    effort: str,
    db_dir: str,
    cwd: str,
    workers: int,
    max_compactions: int,
    proxy_config: str | None,
    no_thinking: bool,
    show_cli_stderr: bool,
) -> None:
    """Run a full multi-pass forensic investigation.

    Uses a planner/executor/analyst pipeline for each phase. Configure
    models per role via CLI flags or a YAML config file.

    EVIDENCE_PATH is the filesystem path to the evidence directory.
    CASE_ID is the unique identifier for this investigation (used as the
    database filename and referenced by all phases).

    \b
      ekchain investigate /evidence Rocba \\
        --planner-model claude-opus-5-5 \\
        --executor-model claude-haiku-5-5 \\
        --analyst-model claude-opus-5-5

    Non-Claude models are supported via LiteLLM proxy (auto-started when
    a model ID uses a provider prefix like bedrock/ or openai/).
    """
    import asyncio
    from typing import cast

    from ekchain.orchestrator.models import ModelConfig
    from ekchain.orchestrator.runner import Orchestrator
    from ekchain.orchestrator.types import EffortLevel

    cwd_path = Path(cwd).expanduser()
    try:
        cwd_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise click.ClickException(f"Cannot create working directory {cwd_path}: {exc}") from exc
    # The *expanded* path is what must reach Orchestrator(cwd=...) below, or the
    # agent sessions are handed a literal "~/.ekchain/workspace" string.
    cwd = str(cwd_path)

    mcp_config_file = cwd_path / ".mcp.json"
    if not mcp_config_file.exists():
        try:
            mcp_config_file.write_text(
                DEFAULT_MCP_CONFIG.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except OSError as exc:
            raise click.ClickException(
                f"Cannot write MCP configuration to {mcp_config_file}: {exc}"
            ) from exc
        click.echo(f"Created default MCP configuration at {mcp_config_file}", err=True)

    log_dir = resolve_db_dir(db_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "orchestrator.log"
    file_handler = logging.FileHandler(str(log_file), mode="a")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)

    model_config = ModelConfig.from_args(
        model=model,
        planner_model=planner_model,
        executor_model=executor_model,
        analyst_model=analyst_model,
        config_path=config_path,
    )

    click.echo(
        f"Ekchain v{__version__} \u2014 The truth is in the data.",
        err=True,
    )
    click.echo(f"Starting multi-pass investigation of {evidence_path}", err=True)
    click.echo("Models:", err=True)
    click.echo(f"  Planner:  {model_config.planner}", err=True)
    click.echo(f"  Executor: {model_config.executor}", err=True)
    click.echo(f"  Analyst:  {model_config.analyst}", err=True)
    if no_thinking:
        click.echo(f"Thinking: disabled (effort ignored), Workers: {workers}", err=True)
    else:
        click.echo(f"Effort: {effort}, Workers: {workers}", err=True)
    click.echo(f"Logging to {log_file}", err=True)

    orchestrator = Orchestrator(
        evidence_path=evidence_path,
        cwd=cwd,
        model_config=model_config,
        # click.Choice above is the validation boundary for this value.
        effort=cast("EffortLevel", effort),
        env={},
        parallel_extractions=workers,
        proxy_config=proxy_config,
        case_id=case_id,
        db_dir=log_dir,
        no_thinking=no_thinking,
        show_cli_stderr=show_cli_stderr,
        max_compactions=max_compactions,
    )

    from ekchain.orchestrator.errors import (
        AuthenticationError,
        ModelNotAvailableError,
    )

    try:
        result = asyncio.run(orchestrator.run())
    except AuthenticationError as exc:
        orchestrator.dashboard.stop()
        click.echo(f"\nError: {exc}", err=True)
        if exc.suggestion:
            click.echo(f"\n{exc.suggestion}", err=True)
        raise SystemExit(2) from None
    except ModelNotAvailableError as exc:
        orchestrator.dashboard.stop()
        click.echo(f"\nError: {exc}", err=True)
        if exc.alternative:
            if _is_interactive():
                if click.confirm(
                    f"Would you like to try {exc.alternative} instead?",
                    default=True,
                ):
                    click.echo(
                        f"\nRe-run with: ekchain investigate ... --model {exc.alternative}",
                        err=True,
                    )
            else:
                click.echo(
                    f"\nTry: ekchain investigate ... --model {exc.alternative}",
                    err=True,
                )
        else:
            click.echo(
                "\nSpecify a different model with --model <model-id>",
                err=True,
            )
        raise SystemExit(2) from None

    orchestrator.dashboard.print_summary(result)

    if not result.success:
        raise SystemExit(1)


@cli.command()
@click.option(
    "--asset-root",
    default=None,
    type=click.Path(),
    help="Override the asset root (env: EKCHAIN_ASSET_ROOT). Exclusive: setting it "
    "disables the /opt search entirely.",
)
@click.option("--dry-run", is_flag=True, help="Print the plan and exit. Issues no requests.")
@click.option(
    "--verify",
    is_flag=True,
    help="Validate what is installed; fetch nothing. Exits 4 if anything is missing, "
    "invalid, or shadowed by a copy ekchain does not manage.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the result document on stdout.")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt for large plans.")
def setup(
    asset_root: str | None,
    dry_run: bool,
    verify: bool,
    as_json: bool,
    yes: bool,
) -> None:
    """Download the forensic data, rule sets and helper tools pip cannot ship.

    Installs everything ekchain owns.  Never installs OS packages, and never
    needs sudo -- it refuses to run as root, because root-owned clones make git
    refuse to update the YARA rules afterwards.

    \b
      ekchain setup                 # install everything (~2.2 GB)
      ekchain setup --dry-run       # print the plan, download nothing
      ekchain setup --verify        # check an existing install, touch no network

    Assets are written to $EKCHAIN_ASSET_ROOT if set, else /opt when it is
    writable, else ~/.local/share/ekchain/assets.  Ekchain *reads* /opt first, so
    an existing SIFT layout keeps working untouched.
    """
    import os

    from rich.console import Console

    from ekchain.assets import install as install_mod
    from ekchain.assets.fetch import HttpFetcher, OfflineFetcher
    from ekchain.assets.paths import asset_write_root, reset_asset_caches
    from ekchain.assets.paths import bin_dir as resolve_bin_dir

    console = Console(stderr=True, no_color=bool(os.environ.get("NO_COLOR")))

    if asset_root:
        # Set the variable rather than threading a second value around, so the
        # flag and the environment can never disagree.
        os.environ["EKCHAIN_ASSET_ROOT"] = str(Path(asset_root).expanduser())
        reset_asset_caches()

    options = install_mod.SetupOptions(verify=verify, dry_run=dry_run)

    write_root = asset_write_root()
    try:
        plan = install_mod.build_plan(options, write_root, resolve_bin_dir())
        install_mod.preflight(plan, options)
    except install_mod.PlanError as exc:
        console.print(f"[red]{exc}[/red]", highlight=False)
        raise SystemExit(exc.exit_code) from None

    console.print(
        f"Asset root: {write_root}  |  shims: {plan.bin_dir}  |  arch: {plan.arch}",
        highlight=False,
    )

    fetcher = OfflineFetcher() if verify else HttpFetcher(timeout=120.0)

    if verify:
        console.print(f"Verifying {len(plan.selected)} assets; no network.", highlight=False)
    elif dry_run:
        # Manifest estimates only: --dry-run issues no requests of any kind.
        console.print(
            f"Plan: {len(plan.selected)} assets, about "
            f"{_human_bytes(plan.total_bytes)} to download.",
            highlight=False,
        )
    else:
        _confirm_size(console, plan, fetcher, yes)

    def _on_event(_key: str, message: str) -> None:
        console.print(f"  {message}", highlight=False)

    try:
        result = install_mod.provision(plan, options, fetch=fetcher, on_event=_on_event)
    except install_mod.PlanError as exc:
        console.print(f"[red]{exc}[/red]", highlight=False)
        raise SystemExit(exc.exit_code) from None
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted; nothing was left half-written.[/yellow]")
        raise SystemExit(3) from None

    _print_summary(console, result, plan, options)

    if as_json:
        click.echo(_setup_json(result, plan))

    raise SystemExit(result.exit_code)


def _confirm_size(console: Console, plan: Plan, fetcher: Fetcher, yes: bool) -> None:
    """Refine the plan total from Content-Length, then prompt above 1 GB."""
    total = plan.total_bytes
    head = getattr(fetcher, "head", None)
    if head is not None:
        refined = 0
        for asset in plan.selected:
            if asset.kind not in ("file", "archive"):
                refined += asset.size_estimate
                continue
            measured = head(asset.url_for(plan.arch))
            refined += measured if measured else asset.size_estimate
        total = refined

    console.print(
        f"Plan: {len(plan.selected)} assets, about {_human_bytes(total)} to download.",
        highlight=False,
    )
    if total <= 1024**3 or yes:
        return
    if not _is_interactive():
        console.print(
            "[red]This plan downloads more than 1 GB and stdin is not a terminal. "
            "Re-run with --yes to proceed.[/red]"
        )
        raise SystemExit(2)
    if not click.confirm(f"Download about {_human_bytes(total)}?", default=True):
        raise SystemExit(2)


def _print_summary(console: Console, result: Result, plan: Plan, options: SetupOptions) -> None:
    """One row per manifest asset, selected or not.

    Nothing is ever omitted, so a silent partial install is not expressible.
    """
    from rich.table import Table

    from ekchain.assets.install import capability_gaps

    table = Table(title="Summary", title_justify="left")
    table.add_column("asset")
    table.add_column("status")
    table.add_column("detail")
    installed = shadowed = failed = missing = 0
    for outcome in result.outcomes:
        status = outcome.status
        colour = "green"
        if outcome.shadowed_by:
            status = f"{status}, shadowed by {outcome.shadowed_by}"
            colour = "yellow"
            shadowed += 1
        elif outcome.failed:
            colour = "red"
            failed += 1
        elif not outcome.selected:
            colour = "dim"
        elif outcome.status in ("missing", "invalid"):
            colour = "yellow"
            missing += 1
        else:
            installed += 1
        table.add_row(outcome.key, f"[{colour}]{status}[/{colour}]", outcome.detail)
    console.print(table)

    gaps = [] if options.dry_run else capability_gaps(result.outcomes)
    if gaps:
        console.print("\n[bold]Consequences[/bold]")
        for gap in gaps:
            console.print(f"  {gap}", highlight=False)

    for remedy in result.remedies:
        console.print(f"\n[yellow]{remedy}[/yellow]", highlight=False)

    selected = len(plan.selected)
    if options.dry_run:
        console.print(
            f"\nDry run: {selected} assets would be provisioned; nothing was downloaded."
        )
    elif failed:
        console.print(
            f"\n{installed} installed, {failed} failed - re-run 'ekchain setup' to retry; "
            "see the errors above."
        )
    elif missing:
        # Never fall through to "All ... present" here: the table two lines up
        # says otherwise, and the last line is the one a human actually reads.
        console.print(
            f"\n{installed} present, {missing} missing - run 'ekchain setup' to install them."
        )
    elif shadowed:
        console.print(
            f"\n{installed} installed, {shadowed} shadowed - ekchain is still reading "
            "the older copies."
        )
    else:
        console.print(f"\nAll {selected} selected assets present.")


def _setup_json(result: Result, plan: Plan) -> str:
    import json

    return json.dumps(
        {
            "schema": 1,
            "root": str(plan.write_root),
            "arch": plan.arch,
            "assets": [
                {
                    "key": outcome.key,
                    "status": outcome.status,
                    "selected": outcome.selected,
                    "detail": outcome.detail,
                    "bytes": outcome.size,
                    "shadowed_by": outcome.shadowed_by,
                }
                for outcome in result.outcomes
            ],
            "exit": result.exit_code,
        },
        indent=2,
    )


def _human_bytes(count: int) -> str:
    """Render a byte count the way the docs quote sizes."""
    if count >= 1024**3:
        return f"{count / 1024**3:.1f} GB"
    return f"{count / 1024**2:.0f} MB"


@cli.command("export-iocs")
@click.argument("case_id")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["stix", "csv", "all"]),
    default="all",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--output-dir",
    default=None,
    help="Directory for output files. Defaults to the case report directory.",
)
@click.option(
    "--db-dir",
    default=DEFAULT_DB_DIR,
    envvar=DB_DIR_ENV_VAR,
    show_default=True,
    help="Directory containing per-case databases (env: EKCHAIN_DB_DIR).",
)
def export_iocs_cmd(case_id: str, fmt: str, output_dir: str | None, db_dir: str) -> None:
    """Export IOCs from a completed investigation as STIX 2.1 or CSV."""
    from ekchain.db import CaseDB
    from ekchain.report.ioc_export import export_iocs

    db_dir_path = Path(db_dir).expanduser()
    db_path = db_dir_path / f"{case_id}.db"

    if not db_path.exists():
        raise click.ClickException(f"Case database not found: {db_path}")

    out_dir = Path(output_dir) if output_dir else db_dir_path

    with CaseDB(db_path) as case_db:
        findings = case_db.get_findings()
        click.echo(f"Exporting IOCs from {len(findings)} findings ...")
        result = export_iocs(case_id, findings, out_dir, fmt=fmt)

    if result.get("csv_path"):
        click.echo(f"  CSV:  {result['csv_path']}")
    if result.get("stix_path"):
        click.echo(f"  STIX: {result['stix_path']}")
    click.echo("Done.")


@cli.command("export-navigator")
@click.argument("case_id")
@click.option(
    "--output-dir",
    default=None,
    help="Directory for the output file. Defaults to the case report directory.",
)
@click.option(
    "--domain",
    type=click.Choice(["enterprise-attack", "ics-attack"]),
    default="enterprise-attack",
    show_default=True,
    help="ATT&CK domain for the layer.",
)
@click.option(
    "--db-dir",
    default=DEFAULT_DB_DIR,
    envvar=DB_DIR_ENV_VAR,
    show_default=True,
    help="Directory containing per-case databases (env: EKCHAIN_DB_DIR).",
)
def export_navigator_cmd(case_id: str, output_dir: str | None, domain: str, db_dir: str) -> None:
    """Export a MITRE ATT&CK Navigator layer from investigation findings."""
    from ekchain.db import CaseDB
    from ekchain.report.navigator import export_navigator_layer

    db_dir_path = Path(db_dir).expanduser()
    db_path = db_dir_path / f"{case_id}.db"

    if not db_path.exists():
        raise click.ClickException(f"Case database not found: {db_path}")

    out_dir = Path(output_dir) if output_dir else db_dir_path

    with CaseDB(db_path) as case_db:
        findings = case_db.get_findings()
        click.echo(f"Building Navigator layer from {len(findings)} findings ...")
        layer_path = export_navigator_layer(case_id, findings, out_dir, domain=domain)

    if layer_path:
        click.echo(f"  Layer: {layer_path}")
    else:
        click.echo("  No MITRE technique IDs found; no layer generated.")
    click.echo("Done.")


@cli.command("verify-ledger")
@click.argument("case_id")
@click.option(
    "--db-dir",
    default=DEFAULT_DB_DIR,
    envvar=DB_DIR_ENV_VAR,
    show_default=True,
    help="Directory containing per-case databases (env: EKCHAIN_DB_DIR).",
)
def verify_ledger_cmd(case_id: str, db_dir: str) -> None:
    """Verify the hash chain of a case audit log."""
    from ekchain.audit import verify_chain

    db_dir_path = Path(db_dir).expanduser()
    audit_path = db_dir_path / f"{case_id}.audit.jsonl"
    if not audit_path.exists():
        raise click.ClickException(f"Audit log not found: {audit_path}")

    res = verify_chain(audit_path)
    if not res["ok"]:
        click.echo(f"Ledger invalid at line {res['first_bad_index']}: {res['reason']}", err=True)
        raise SystemExit(1)

    click.echo("Ledger valid.")
    click.echo(f"  Entries: {res['entries']}")
    click.echo(f"  Head:    {res['head']}")


@cli.command("anchor")
@click.argument("case_id")
@click.option(
    "--db-dir",
    default=DEFAULT_DB_DIR,
    envvar=DB_DIR_ENV_VAR,
    show_default=True,
    help="Directory containing per-case databases (env: EKCHAIN_DB_DIR).",
)
def anchor_cmd(case_id: str, db_dir: str) -> None:
    """Verify local audit log and anchor its head on-chain."""
    import json
    import os

    from ekchain.audit import verify_chain

    required = [
        "EKCHAIN_RPC_URL",
        "EKCHAIN_PRIVATE_KEY",
        "EKCHAIN_ANCHOR_ADDRESS",
        "EKCHAIN_CHAIN_ID",
    ]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        click.echo(
            f"Error: Missing required environment variables: {', '.join(missing)}", err=True
        )
        raise SystemExit(2)

    db_dir_path = Path(db_dir).expanduser()
    audit_path = db_dir_path / f"{case_id}.audit.jsonl"
    if not audit_path.exists():
        raise click.ClickException(f"Audit log not found: {audit_path}")

    res = verify_chain(audit_path)
    if not res["ok"]:
        click.echo(
            f"Audit log verification failed at line {res['first_bad_index']}: {res['reason']}",
            err=True,
        )
        raise SystemExit(1)

    head = str(res["head"])
    entries = int(cast(int, res["entries"]))

    try:
        from ekchain.chain.client import ChainClient
    except (ImportError, ModuleNotFoundError):
        click.echo("web3 not installed. Run: pip install 'ekchain[chain]'", err=True)
        raise SystemExit(2) from None

    client = ChainClient.from_env()
    click.echo(f"Anchoring case '{case_id}' head {head} ({entries} entries) ...")
    result = client.anchor(case_id, head, entries)
    click.echo(f"  TX Hash: {result['tx_hash']}")
    click.echo(f"  Explorer: {result['explorer_url']}")

    anchor_info = {
        "tx_hash": result["tx_hash"],
        "chain_id": client.chain_id,
        "block": result["block_number"],
        "head": head,
        "entry_count": entries,
        "contract_address": client.anchor_address,
        "case_id": case_id,
    }
    anchor_json_path = db_dir_path / f"{case_id}.anchor.json"
    anchor_json_path.write_text(json.dumps(anchor_info, indent=2), encoding="utf-8")
    click.echo(f"  Saved anchor record: {anchor_json_path}")


@cli.command("verify-anchor")
@click.argument("case_id")
@click.option(
    "--db-dir",
    default=DEFAULT_DB_DIR,
    envvar=DB_DIR_ENV_VAR,
    show_default=True,
    help="Directory containing per-case databases (env: EKCHAIN_DB_DIR).",
)
def verify_anchor_cmd(case_id: str, db_dir: str) -> None:
    """Verify local audit log head against on-chain anchor record."""
    import os

    from ekchain.audit import verify_chain

    required = ["EKCHAIN_RPC_URL", "EKCHAIN_ANCHOR_ADDRESS", "EKCHAIN_CHAIN_ID"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        click.echo(
            f"Error: Missing required environment variables: {', '.join(missing)}", err=True
        )
        raise SystemExit(2)

    db_dir_path = Path(db_dir).expanduser()
    audit_path = db_dir_path / f"{case_id}.audit.jsonl"
    if not audit_path.exists():
        raise click.ClickException(f"Audit log not found: {audit_path}")

    res = verify_chain(audit_path)
    if not res["ok"]:
        click.echo(
            "Local audit log verification failed at line "
            f"{res['first_bad_index']}: {res['reason']}",
            err=True,
        )
        raise SystemExit(1)

    head = str(res["head"])

    try:
        from ekchain.chain.client import ChainClient
    except (ImportError, ModuleNotFoundError):
        click.echo("web3 not installed. Run: pip install 'ekchain[chain]'", err=True)
        raise SystemExit(2) from None

    anchor_address = os.environ["EKCHAIN_ANCHOR_ADDRESS"]

    client = ChainClient.from_env(anchor_address=anchor_address)
    v_res = client.verify_anchor(case_id, head)

    if v_res["match"]:
        click.echo(
            "MATCH: Local head "
            f"{v_res['local_head']} matches on-chain head {v_res['on_chain_head']}"
        )
    else:
        click.echo(
            "MISMATCH: Local head "
            f"{v_res['local_head']} differs from on-chain head {v_res['on_chain_head']} "
            f"(Reason: {v_res['reason']})",
            err=True,
        )
        raise SystemExit(1)


def _strip_paths(text: str) -> str:
    import re

    def _replace_path(match: re.Match[str]) -> str:
        p = match.group(0)
        try:
            return Path(p).name if Path(p).name else p
        except Exception:
            return p

    text = re.sub(r"[a-zA-Z]:\\[^\s<>:\"|?*]+", _replace_path, text)
    text = re.sub(r"/(?:[^\s0/]+/)+[^\s0/]+", _replace_path, text)
    return text


@cli.command("export-case")
@click.argument("case_id")
@click.option(
    "--db-dir",
    default=DEFAULT_DB_DIR,
    envvar=DB_DIR_ENV_VAR,
    show_default=True,
    help="Directory containing per-case databases (env: EKCHAIN_DB_DIR).",
)
def export_case_cmd(case_id: str, db_dir: str) -> None:
    """Export case summary, evidence sources, findings, narrative, and audit stats to JSON."""
    import json
    from datetime import datetime, timezone

    from ekchain.audit import verify_chain
    from ekchain.db import CaseDB
    from ekchain.report.redactor import Redactor

    db_dir_path = Path(db_dir).expanduser()
    db_path = db_dir_path / f"{case_id}.db"
    audit_path = db_dir_path / f"{case_id}.audit.jsonl"

    if not db_path.exists():
        raise click.ClickException(f"Case database not found: {db_path}")

    redactor = Redactor()

    def clean(val: str | None) -> str | None:
        if not val:
            return None
        stripped = _strip_paths(val)
        redacted, _ = redactor.redact(stripped)
        return redacted

    with CaseDB(db_path) as case_db:
        meta = case_db.get_case_metadata()
        sources = case_db.get_sources()
        findings = case_db.get_findings()
        evidence_reg = case_db.get_evidence_registry()

        sha_map = {e["file_path"]: e["sha256"] for e in evidence_reg}

        evidence_sources = []
        for s in sources:
            sha = s.source_hash or sha_map.get(s.source_path)
            evidence_sources.append(
                {
                    "name": s.source_name,
                    "artifact_type": s.extractor,
                    "sha256": sha if sha else None,
                }
            )

        exported_findings = []
        for f in findings:
            time_range = None
            if f.event_time_start or f.event_time_end:
                time_range = {
                    "start": f.event_time_start,
                    "end": f.event_time_end,
                }
            exported_findings.append(
                {
                    "id": f.finding_id,
                    "title": clean(f.title),
                    "description": clean(f.description),
                    "severity": f.severity,
                    "confidence": f.confidence,
                    "anomaly_ids": f.mitre_attack_ids,
                    "status": None,
                    "evidence_references": f.evidence_refs,
                    "time_range": time_range,
                }
            )

        narrative = clean(meta.narrative) if meta.narrative else None

        entry_count = 0
        head_hash = None
        if audit_path.exists():
            v_res = verify_chain(audit_path)
            entry_count = int(cast(int, v_res["entries"]))
            head_hash = str(v_res["head"]) if v_res["chained"] else None

        case_export = {
            "case_id": case_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "evidence_sources": evidence_sources,
            "findings": exported_findings,
            "narrative": narrative,
            "gate_results": None,
            "audit_stats": {
                "entry_count": entry_count,
                "head_hash": head_hash,
            },
        }

    out_path = db_dir_path / f"{case_id}.case.json"
    out_path.write_text(
        json.dumps(case_export, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    click.echo(f"Exported case data to {out_path}")
    click.echo("Done.")


if __name__ == "__main__":
    cli()
