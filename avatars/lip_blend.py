"""Helpers for blending Wav2Lip output without replacing the upper face."""

from __future__ import annotations

import cv2
import numpy as np


def build_mouth_mask(height: int, width: int) -> np.ndarray:
    """Return a feathered lower-face mask in the range ``[0, 1]``."""
    if height <= 0 or width <= 0:
        raise ValueError("height and width must be positive")

    mask = np.zeros((height, width), dtype=np.float32)
    center = (width // 2, int(round(height * 0.70)))
    axes = (
        max(1, int(round(width * 0.36))),
        max(1, int(round(height * 0.23))),
    )
    cv2.ellipse(mask, center, axes, 0, 0, 360, 1.0, thickness=-1)

    # Feather the edge, then explicitly protect the eyes and upper face.
    sigma = max(1.0, min(height, width) * 0.035)
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=sigma, sigmaY=sigma)
    mask[: int(round(height * 0.46)), :] = 0.0
    return np.clip(mask, 0.0, 1.0)


def blend_mouth_region(
    original_face: np.ndarray,
    generated_face: np.ndarray,
    strength: float,
) -> np.ndarray:
    """Blend generated pixels only around the mouth and lower face.

    ``strength`` is clamped to ``[0, 1]``. The result keeps the source dtype,
    and the protected upper-face region is copied without modification.
    """
    if original_face.shape != generated_face.shape:
        raise ValueError("original and generated faces must have matching shapes")
    if original_face.ndim != 3 or original_face.shape[2] != 3:
        raise ValueError("faces must be HxWx3 images")

    amount = float(np.clip(strength, 0.0, 1.0))
    if amount == 0.0:
        return original_face.copy()

    height, width = original_face.shape[:2]
    alpha = build_mouth_mask(height, width)[..., None] * amount
    blended = (
        original_face.astype(np.float32) * (1.0 - alpha)
        + generated_face.astype(np.float32) * alpha
    )
    return np.rint(blended).clip(0, 255).astype(original_face.dtype)
