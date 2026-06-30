"""TriageBot application package.

Configures a small ``triagebot`` logger so that the classification notices
(model vs. heuristic) are always visible on the CLI where the app runs, no
matter how uvicorn configures its own loggers.
"""

from __future__ import annotations

import logging
import sys

_logger = logging.getLogger("triagebot")
if not _logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s - %(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)
    # Keep propagation on so test fixtures (caplog) can still capture records.
