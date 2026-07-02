"""CLI entry point.

    copilot init-db                     create/upgrade the SQLite schema
    copilot health                      check the TradingView MCP bridge + chart
    copilot backfill [--symbols MNQ]    deep pull of all configured timeframes
    copilot collect [--loop]            incremental 1m collection (+ derived TFs)
    copilot status                      DB coverage, freshness, gaps
    copilot state --symbol MNQ          structured market-state JSON from stored bars
    copilot scan  --symbol MNQ          run strategies on stored bars -> candidates
    copilot load-fixtures <csv>         load fixture bars (tests/offline dev ONLY)

This tool never places orders. LONG/SHORT/WAIT/REJECT output (later phases)
is decision support for MANUAL paper trading only.
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import Config, load_config
from .db.store import Store
from .errors import CopilotError


def _make_source(config: Config):
    # Product path only. Fixtures are loaded explicitly via load-fixtures and
    # never wired in as a live source — that's a design rule, not an oversight.
    from .data.tvmcp import TradingViewMcpCandleSource

    return TradingViewMcpCandleSource(config)


def cmd_init_db(config: Config, _args) -> int:
    with Store(config.db_file) as store:
        store.init_schema()
    print(f"schema ready: {config.db_file}")
    return 0


def cmd_health(config: Config, _args) -> int:
    source = _make_source(config)
    try:
        info = source.health_check()
        print(json.dumps(info, indent=2, default=str))
        return 0
    finally:
        source.close()


def cmd_backfill(config: Config, args) -> int:
    from .data.collector import backfill

    source = _make_source(config)
    try:
        with Store(config.db_file) as store:
            store.init_schema()
            report = backfill(config, source, store, args.symbols)
        print(report.summary())
        return 1 if report.errors and not report.written else 0
    finally:
        source.close()


def cmd_collect(config: Config, args) -> int:
    from .data.collector import collect_loop, collect_once

    source = _make_source(config)
    try:
        with Store(config.db_file) as store:
            store.init_schema()
            if args.loop:
                collect_loop(config, source, store, args.symbols)
            else:
                report = collect_once(config, source, store, args.symbols)
                print(report.summary())
                return 1 if report.errors and not report.written else 0
        return 0
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0
    finally:
        source.close()


def cmd_status(config: Config, _args) -> int:
    from .data.collector import status

    with Store(config.db_file) as store:
        store.init_schema()
        print(json.dumps(status(config, store), indent=2))
    return 0


def cmd_state(config: Config, args) -> int:
    from .features.market_state import build_market_state

    with Store(config.db_file) as store:
        state = build_market_state(store, config, args.symbol, as_of_ts=args.as_of)
        print(state.to_json())
    return 0


def cmd_scan(config: Config, args) -> int:
    import json as _json

    from .gate import evaluate
    from .strategies.engine import scan

    with Store(config.db_file) as store:
        store.init_schema()
        result = scan(store, config, args.symbol, as_of_ts=args.as_of,
                      persist=not args.no_persist)
        gate = evaluate(result.state, result.candidates, store, config,
                        persist=not args.no_persist)
        if args.json:
            out = result.to_dict()
            out["gate"] = {
                "decision": gate.decision,
                "chosen_signal_id": gate.chosen.signal_id if gate.chosen else None,
                "evaluations": [
                    {"decision": g.decision, "signal_id": g.signal_id,
                     "duplicate": g.duplicate, "reasons": g.reasons, "warnings": g.warnings,
                     "invalidation": g.invalidation,
                     "checklist": [c.__dict__ for c in g.checklist],
                     "candidate": g.candidate.model_dump()}
                    for g in gate.evaluations
                ],
            }
            print(_json.dumps(out, indent=2, default=str))
        else:
            st = result.state
            print(f"{st.symbol} {st.trading_day} session={st.session} price={st.current_price}")
            if not result.candidates:
                print("no candidates — WAIT is the default posture")
            for c in result.candidates:
                print(f"  [{c.grade}] {c.strategy} {c.direction.upper()} "
                      f"entry~{c.entry_ref} stop={c.stop} target={c.target} ({c.target_name}) "
                      f"rr={c.rr:.2f} confluences={','.join(c.confluences)}")
                if c.warnings:
                    print(f"      warnings: {'; '.join(c.warnings)}")
            print(f"GATE: {gate.summary()}")
            for g in gate.evaluations:
                if g.decision == "REJECT":
                    print(f"  REJECT [{g.candidate.grade}] {g.candidate.setup_type} "
                          f"{g.candidate.direction}: {'; '.join(g.reasons)}")
            print("decision support only — you approve and (paper) trade manually; WAIT is default")
    return 0


def cmd_load_fixtures(config: Config, args) -> int:
    from .data.fixtures import load_fixture_csv

    candles = load_fixture_csv(args.csv)
    with Store(config.db_file) as store:
        store.init_schema()
        n = store.upsert_candles(candles)
    print(f"loaded {n} FIXTURE bars from {args.csv} (source='fixture' — not live data)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="copilot",
        description="Futures AI Chart Copilot — manual paper-trading decision support. "
                    "Python calculates, Claude explains, human approves.",
    )
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create/upgrade the SQLite schema")
    sub.add_parser("health", help="check bridge + TradingView Desktop connection")

    p = sub.add_parser("backfill", help="deep pull of all configured timeframes")
    p.add_argument("--symbols", nargs="*", default=None, help="subset of symbols (default: all)")

    p = sub.add_parser("collect", help="incremental 1m collection")
    p.add_argument("--loop", action="store_true", help="poll continuously")
    p.add_argument("--symbols", nargs="*", default=None)

    sub.add_parser("status", help="DB coverage / freshness / gaps")

    p = sub.add_parser("state", help="market-state JSON from stored bars (no bridge needed)")
    p.add_argument("--symbol", default="MNQ")
    p.add_argument("--as-of", dest="as_of", type=int, default=None,
                   help="epoch seconds; reconstruct state as of this moment (replay)")

    p = sub.add_parser("scan", help="run strategy detection on stored bars (no bridge needed)")
    p.add_argument("--symbol", default="MNQ")
    p.add_argument("--as-of", dest="as_of", type=int, default=None)
    p.add_argument("--json", action="store_true", help="full JSON output")
    p.add_argument("--no-persist", action="store_true", help="don't write scan results to the DB")

    p = sub.add_parser("load-fixtures", help="load fixture CSV (tests/offline dev only)")
    p.add_argument("csv")

    args = parser.parse_args(argv)
    handlers = {
        "init-db": cmd_init_db,
        "health": cmd_health,
        "backfill": cmd_backfill,
        "collect": cmd_collect,
        "status": cmd_status,
        "state": cmd_state,
        "scan": cmd_scan,
        "load-fixtures": cmd_load_fixtures,
    }
    try:
        config = load_config(args.config)
        return handlers[args.command](config, args)
    except CopilotError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
