"""OPTIONAL local vector memory — scaffold behind config, OFF by default.

v0.1 truth: the packet's similar-setup lookup is the deterministic SQL memory
(sql_memory.py). This module exists so a vector path can be turned on later
without redesign, under hard constraints:

- LOCAL ONLY. No network embeddings, no API keys, no paid services.
- The "embedding" is a deterministic, auditable feature vector built from the
  signal's structured fields — not a language model.
- Backends: "memory" (in-process, tests/dev) and "chroma" (optional local
  ChromaDB persistence; requires `pip install chromadb`, refused gracefully
  when missing). LanceDB would slot in the same way.

Enable via config.yaml:
    memory:
      vector: {enabled: true, backend: memory, top_k: 5}
"""

from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from typing import Any

from ..config import Config
from ..errors import ConfigError

SESSIONS = ("asia", "london", "ny")
GRADES = ("A", "B", "C")
DIRECTIONS = ("long", "short")
SWEEP_KINDS = ("prior_day", "asia", "london", "ny", "opening_range")


def signal_feature_vector(sig: dict[str, Any]) -> list[float]:
    """Deterministic, human-auditable vector for a gated signal dict
    (shape of `signals.json_signal`->candidate). Order is fixed; every slot
    is documented by position below."""
    cand = sig.get("candidate", sig)
    ctx = cand.get("context") or {}
    v: list[float] = []
    v.extend(1.0 if cand.get("direction") == d else 0.0 for d in DIRECTIONS)
    v.extend(1.0 if cand.get("session") == s else 0.0 for s in SESSIONS)
    v.extend(1.0 if cand.get("grade") == g else 0.0 for g in GRADES)
    swept = str(ctx.get("swept_level") or "")
    v.extend(1.0 if swept.startswith(k) else 0.0 for k in SWEEP_KINDS)
    rr = float(cand.get("rr") or 0.0)
    v.append(min(rr, 5.0) / 5.0)                            # rr, capped
    v.append(min(float(ctx.get("reclaim_candles") or 0), 5.0) / 5.0)
    v.append(1.0 if ctx.get("mss_ts") else 0.0)
    v.append(1.0 if ctx.get("cisd_ts") else 0.0)
    v.append(1.0 if ctx.get("entry_kind") == "fvg_retest" else 0.0)
    pdd = ctx.get("premium_discount_day")
    v.extend(1.0 if pdd == x else 0.0 for x in ("discount", "equilibrium", "premium"))
    return v


def cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return 0.0 if da == 0 or db == 0 else num / (da * db)


class VectorMemory(ABC):
    """Adapter interface. Implementations must be local and deterministic."""

    @abstractmethod
    def index_signal(self, signal_id: int, signal: dict[str, Any]) -> None: ...

    @abstractmethod
    def query(self, signal: dict[str, Any], top_k: int = 5) -> list[dict[str, Any]]:
        """Returns [{'signal_id': int, 'score': float}] best-first."""

    @abstractmethod
    def count(self) -> int: ...


class NullVectorMemory(VectorMemory):
    """What you get when vector memory is disabled (the v0.1 default)."""

    def index_signal(self, signal_id: int, signal: dict[str, Any]) -> None:
        return None

    def query(self, signal: dict[str, Any], top_k: int = 5) -> list[dict[str, Any]]:
        return []

    def count(self) -> int:
        return 0


class InMemoryVectorMemory(VectorMemory):
    """Pure-python cosine store. Dev/tests; non-persistent by design."""

    def __init__(self) -> None:
        self._rows: dict[int, list[float]] = {}

    def index_signal(self, signal_id: int, signal: dict[str, Any]) -> None:
        self._rows[int(signal_id)] = signal_feature_vector(signal)

    def query(self, signal: dict[str, Any], top_k: int = 5) -> list[dict[str, Any]]:
        q = signal_feature_vector(signal)
        scored = [{"signal_id": sid, "score": round(cosine(q, vec), 6)}
                  for sid, vec in self._rows.items()]
        scored.sort(key=lambda r: (-r["score"], r["signal_id"]))
        return scored[:top_k]

    def count(self) -> int:
        return len(self._rows)


class ChromaVectorMemory(VectorMemory):
    """Local ChromaDB persistence. Optional dependency, imported lazily."""

    def __init__(self, path: str) -> None:
        try:
            import chromadb  # type: ignore
        except ImportError as e:  # pragma: no cover - depends on optional install
            raise ConfigError(
                "memory.vector.backend=chroma but chromadb is not installed",
                hint="pip install chromadb  (local only; no network embeddings are used)",
            ) from e
        self._client = chromadb.PersistentClient(path=path)  # pragma: no cover
        self._col = self._client.get_or_create_collection("copilot_signals")  # pragma: no cover

    def index_signal(self, signal_id: int, signal: dict[str, Any]) -> None:  # pragma: no cover
        self._col.upsert(ids=[str(signal_id)],
                         embeddings=[signal_feature_vector(signal)],
                         documents=[json.dumps(signal, default=str)])

    def query(self, signal: dict[str, Any], top_k: int = 5) -> list[dict[str, Any]]:  # pragma: no cover
        res = self._col.query(query_embeddings=[signal_feature_vector(signal)], n_results=top_k)
        ids = res.get("ids", [[]])[0]
        dists = res.get("distances", [[]])[0] if res.get("distances") else [0.0] * len(ids)
        return [{"signal_id": int(i), "score": round(1.0 - d, 6)} for i, d in zip(ids, dists)]

    def count(self) -> int:  # pragma: no cover
        return self._col.count()


def get_vector_memory(config: Config) -> VectorMemory:
    vcfg = config.memory.vector
    if not vcfg.enabled:
        return NullVectorMemory()
    if vcfg.backend == "memory":
        return InMemoryVectorMemory()
    if vcfg.backend == "chroma":
        return ChromaVectorMemory(str(config.resolve(vcfg.path)))
    raise ConfigError(f"unknown memory.vector.backend {vcfg.backend!r}",
                      hint="use 'memory' or 'chroma' (both local-only)")
