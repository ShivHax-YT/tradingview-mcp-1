"""Trading memory. v1 = deterministic SQL similar-setup lookup (always on).
Optional local vector adapter lives behind config and is NOT required."""

from .sql_memory import similar_setups

__all__ = ["similar_setups"]
