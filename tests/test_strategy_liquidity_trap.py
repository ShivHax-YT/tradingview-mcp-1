"""Strategy Module 1 — MTF Session Liquidity Trap: LONG / SHORT / WAIT and
no-lookahead behavior on production-shaped data."""

from futures_copilot.strategies import SessionLiquidityTrap, build_context, scan

from .feature_helpers import et_ts
from .strategy_fixtures import seed_long_trap, seed_short_trap


def _detect(store, config, as_of=None):
    ctx = build_context(store, config, "MNQ", as_of_ts=as_of)
    return ctx, SessionLiquidityTrap().detect(ctx)


def test_long_trap_full_story(config, store):
    seed_long_trap(store)
    ctx, cands = _detect(store, config)

    assert cands, "expected candidates after sweep+reclaim+MSS/CISD"
    assert all(c.direction == "long" for c in cands)
    # the asia-low trap is the canonical one
    asia = [c for c in cands if c.context["swept_level"] == "asia_low"]
    assert asia, f"asia_low trap missing; got {[c.context['swept_level'] for c in cands]}"
    c = asia[0]

    assert c.strategy == "session_liquidity_trap"
    assert c.session == "ny"
    # entry is the FVG retest zone left by the impulse
    assert c.context["entry_kind"] == "fvg_retest"
    assert (c.entry_lo, c.entry_hi) == (22975.0, 23000.0)
    # stop is ATR-buffered beyond the sweep extreme (22945)
    assert c.stop < 22945.0
    # target = next buy-side liquidity above the zone: asia high (london high is below)
    assert c.target == 23080.0 and c.target_name == "asia_high"
    assert 1.5 < c.rr < 2.5
    assert c.grade == "A"
    for tag in ("fast_reclaim", "mss_confirmed", "cisd_confirmed", "fvg_entry"):
        assert tag in c.confluences, f"missing {tag}"
    assert c.context["reclaim_candles"] == 1
    assert c.context["mss_ts"] == et_ts(2026, 6, 24, 10, 5)
    # knowable only after the confirmation bar closed
    assert c.confirmed_close_ts == et_ts(2026, 6, 24, 10, 10)
    # prior-day low was never swept -> must not appear
    assert not [x for x in cands if x.context["swept_level"] == "prior_day_low"]


def test_short_trap_mirror(config, store):
    seed_short_trap(store)
    ctx, cands = _detect(store, config)

    assert cands
    assert all(c.direction == "short" for c in cands)
    c = [x for x in cands if x.context["swept_level"] == "asia_high"][0]
    assert c.stop > 23095.0                      # beyond the sweep extreme, buffered
    assert c.target < c.entry_lo                 # next sell-side liquidity below
    assert c.grade in ("A", "B")
    assert "mss_confirmed" in c.confluences and "cisd_confirmed" in c.confluences


def test_wait_before_confirmation_no_lookahead(config, store):
    """Replay at the sweep bar's close: sweep+reclaim exist, confirmation does
    not — the strategy must stay silent. The exact same data yields a candidate
    later, proving detection is horizon-driven, not data-driven."""
    seed_long_trap(store)
    as_of = et_ts(2026, 6, 24, 10, 4)            # before the 10:05 confirmation bar
    ctx, cands = _detect(store, config, as_of=as_of)
    assert ctx.state.session == "ny"
    assert cands == []

    # one bar later the confirmation close exists -> candidate appears
    as_of2 = et_ts(2026, 6, 24, 10, 14)          # 10:05 bar closed at 10:10; FVG needs 10:10 bar closed at 10:15
    ctx2, cands2 = _detect(store, config, as_of=et_ts(2026, 6, 24, 10, 20))
    assert cands2, "candidate should exist once confirmation bar has closed"


def test_no_candidates_in_dead_zone_or_without_sweep(config, store):
    from .strategy_fixtures import seed_backdrop, _seed_morning

    # quiet morning: no level is swept -> nothing to say
    seed_backdrop(store)
    quiet5 = [(9, 30 + 5 * i, 23000, 23008, 22996, 23004) for i in range(6)]
    quiet15 = [(9, 30, 23000, 23008, 22996, 23004), (9, 45, 23004, 23008, 22996, 23004)]
    _seed_morning(store, quiet5, quiet15)
    ctx, cands = _detect(store, config)
    assert cands == []


def test_scan_persists_state_and_candidates(config, store):
    seed_long_trap(store)
    result = scan(store, config, "MNQ", persist=True)
    assert result.state_id is not None
    assert result.candidates
    saved = store.latest_strategy_outputs("MNQ")
    assert saved and saved[0]["strategy_name"] == "session_liquidity_trap"
    latest_state = store.latest_market_state("MNQ")
    assert latest_state is not None and latest_state["session"] == "ny"
