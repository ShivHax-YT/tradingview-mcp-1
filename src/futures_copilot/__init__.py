"""Futures AI Chart Copilot — manual paper-trading decision support.

Python calculates. Claude explains. Human approves.
No broker execution. No live orders. No autonomous trading.
"""

__version__ = "0.1.0"

# Permanent safety invariants. These are code-level constants, not config,
# so no configuration file can ever flip them.
MANUAL_APPROVAL_REQUIRED = True
AUTO_EXECUTION_ENABLED = False
