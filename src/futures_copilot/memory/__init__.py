"""Trading memory. v1 = deterministic SQL similar-setup lookup (always on).
Optional local vector adapter lives behind config and is NOT required."""

from .sql_memory import similar_setups
from .vector import (
    InMemoryVectorMemory, NullVectorMemory, VectorMemory,
    get_vector_memory, signal_feature_vector,
)

__all__ = [
    "similar_setups",
    "VectorMemory", "NullVectorMemory", "InMemoryVectorMemory",
    "get_vector_memory", "signal_feature_vector",
]
