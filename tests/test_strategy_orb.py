"""ORB skeleton: config-gated, conservative, breakout-with-retest only."""

from futures_copilot.strategies import OrbBreakout, build_context

from .strategy_fixtures import seed_orb_breakout


def _detect(store, config):
    ctx = build_context(store, config, "MNQ")
    return OrbBreakout().detect(ctx)


def test_disabled_by_default_returns_nothing(config, store):
    seed_orb_breakout(store)
    assert config.strategies.orb.enabled is False
    assert _detect(store, config) == []


def test_breakout_with_retest_emits_conservative_candidate(config, store):
    seed_orb_breakout(store)
    config.strategies.orb.enabled = True
    cands = _detect(store, config)
    assert len(cands) == 1
    c = cands[0]
    assert c.direction == "long"
    assert c.grade == "C"                        # hard-capped: skeleton output
    assert "orb_skeleton_experimental" in c.warnings
    assert c.entry_ref == 23010.0                # the OR high edge
    assert c.stop < 23000.0                      # below OR midpoint, buffered
    assert c.target == 23080.0                   # next liquidity: asia high
    assert c.rr > 1.0


def test_fakeout_cancels_story(config, store):
    seed_orb_breakout(store, fakeout=True)
    config.strategies.orb.enabled = True
    assert _detect(store, config) == []
