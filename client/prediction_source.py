"""Re-export shim — PredictionSource now lives in ffast.core.prediction_source (ADR 0047 Phase 2)."""
from ffast.core.prediction_source import (  # noqa: F401
    InProcessSource,
    PredictionSource,
    RemoteSource,
)
