"""Memory: deterministic SQL similar-setup lookup + optional vector scaffold."""

import json

from futures_copilot.memory import (
    InMemoryVectorMemory, NullVectorMemory, get_vector_memory,
    signal_feature_vector, similar_setups,
)


def _sig(direction="long", session="ny", grade="A", rr=1.9, swept="asia_low"):
    return {
        "candidate": {
            "setup_type": "session_liquidity_trap", "direction": direction,
            "session": session, "grade": grade, "rr": rr,
            "context": {"swept_level": swept, "reclaim_candles": 1,
                        "mss_ts": 1, "cisd_ts": 1, "entry_kind": "fvg_retest",
                        "premium_discount_day": "discount"},
        },
    }


def _save(store, *, day, ts, direction="long", session="ny", grade="A", rr=1.9,
          swept="asia_low", decision="LONG"):
    return store.save_signal(
        symbol="MNQ", ts=ts, session=session, trading_day=day, decision=decision,
        setup="session_liquidity_trap", grade=grade, entry_lo=1, entry_hi=2, stop=0,
        tp1=5, tp2=None, rr=rr, reasons="[]", warnings="[]", invalidation="[]",
        json_signal=json.dumps(_sig(direction, session, grade, rr, swept)),
    )


def test_sql_memory_matches_bands_and_attaches_outcomes(store):
    base_ts = 1_782_311_100
    a = _save(store, day="2026-06-20", ts=base_ts - 400_000)                       # match
    b = _save(store, day="2026-06-21", ts=base_ts - 300_000, grade="B", rr=1.4)    # match (band edges)
    c = _save(store, day="2026-06-22", ts=base_ts - 200_000, direction="short")    # direction mismatch
    d = _save(store, day="2026-06-23", ts=base_ts - 100_000, session="london")     # session mismatch
    e = _save(store, day="2026-06-23", ts=base_ts - 90_000, grade="C")             # grade band > 1
    f = _save(store, day="2026-06-23", ts=base_ts - 80_000, rr=4.0)                # rr out of band
    g = _save(store, day="2026-06-23", ts=base_ts - 70_000, swept="prior_day_low") # sweep kind mismatch
    store.add_trade_review(signal_id=a, symbol="MNQ", taken=True, result_r=1.8,
                           mistake_tags='["exited_early"]')

    hits = similar_setups(store, symbol="MNQ", setup="session_liquidity_trap",
                          direction="long", session="ny", grade="A", rr=1.9,
                          sweep_level="asia_low")
    ids = [h["signal_id"] for h in hits]
    assert a in ids and b in ids
    for bad in (c, d, e, f, g):
        assert bad not in ids
    # newest first + outcome attached
    assert ids == sorted(ids, reverse=True)
    hit_a = [h for h in hits if h["signal_id"] == a][0]
    assert hit_a["outcome"]["result_r"] == 1.8
    assert json.loads(hit_a["outcome"]["mistake_tags"]) == ["exited_early"]


def test_sql_memory_excludes_self_and_respects_limit(store):
    ids = [_save(store, day=f"2026-06-{10+i:02d}", ts=1_782_000_000 + i * 1000) for i in range(8)]
    hits = similar_setups(store, symbol="MNQ", setup="session_liquidity_trap",
                          direction="long", session="ny", limit=3,
                          exclude_signal_id=ids[-1])
    assert len(hits) == 3
    assert ids[-1] not in [h["signal_id"] for h in hits]


def test_feature_vector_is_deterministic_and_discriminative():
    v1 = signal_feature_vector(_sig())
    v2 = signal_feature_vector(_sig())
    assert v1 == v2
    assert signal_feature_vector(_sig(direction="short")) != v1
    assert signal_feature_vector(_sig(swept="prior_day_low")) != v1


def test_in_memory_vector_adapter_ranks_by_similarity():
    mem = InMemoryVectorMemory()
    mem.index_signal(1, _sig())                                  # identical to query
    mem.index_signal(2, _sig(grade="B", rr=1.4))                 # near
    mem.index_signal(3, _sig(direction="short", swept="prior_day_high", grade="C"))  # far
    res = mem.query(_sig(), top_k=3)
    assert [r["signal_id"] for r in res][0] == 1
    assert res[0]["score"] > res[1]["score"] > res[2]["score"]
    assert mem.count() == 3


def test_vector_memory_disabled_by_default(config):
    assert config.memory.vector.enabled is False
    mem = get_vector_memory(config)
    assert isinstance(mem, NullVectorMemory)
    assert mem.query(_sig()) == []


def test_vector_memory_config_selects_in_memory_backend(config):
    config.memory.vector.enabled = True
    config.memory.vector.backend = "memory"
    mem = get_vector_memory(config)
    assert isinstance(mem, InMemoryVectorMemory)
