"""Safe serialization utilities.

Provides NaN/Inf-safe float conversion and a JSON encoder that handles
numpy types and pandas NaN values, preventing serialization failures
in the API layer.
"""
from __future__ import annotations

import json
import math
from typing import Any

import numpy as np


def _safe_float(val: Any) -> float | None:
    """Convert a value to float, returning None for NaN/Inf/None.

    Args:
        val: Value to convert.

    Returns:
        Float value or None if the value is not finite.
    """
    try:
        v = float(val)
        return None if (math.isnan(v) or math.isinf(v)) else v
    except Exception:
        return None


def safe_float_or_zero(val: Any) -> float:
    """Convert to float, returning 0.0 for NaN/Inf/None.

    Args:
        val: Value to convert.

    Returns:
        Float value or 0.0 for non-finite values.
    """
    result = _safe_float(val)
    return result if result is not None else 0.0


class SafeJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types, NaN, and Inf.

    Converts:
    - numpy integers → int
    - numpy floats → float (NaN/Inf → None)
    - numpy arrays → list
    - pandas NaT → None
    """

    def default(self, obj: Any) -> Any:
        """Handle non-serializable types.

        Args:
            obj: Object to serialize.

        Returns:
            JSON-serializable representation.
        """
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            f = float(obj)
            return None if (math.isnan(f) or math.isinf(f)) else f
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

    def encode(self, obj: Any) -> str:
        """Encode to JSON string, replacing NaN/Inf with null.

        Args:
            obj: Object to encode.

        Returns:
            JSON string.
        """
        return super().encode(obj)

    def iterencode(self, obj: Any, _one_shot: bool = False):
        """Iterator over JSON-encoded chunks with NaN handling."""
        # Replace Python float NaN/Inf before encoding
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return iter(["null"])
        return super().iterencode(obj, _one_shot)


def safe_json_dumps(obj: Any, **kwargs: Any) -> str:
    """Serialize object to JSON string using SafeJSONEncoder.

    Args:
        obj: Object to serialize.
        **kwargs: Additional arguments for json.dumps.

    Returns:
        JSON string.
    """
    return json.dumps(obj, cls=SafeJSONEncoder, **kwargs)
