#!/usr/bin/env python3
"""Build a still-avatar video with a short, natural eye blink."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


DEFAULT_OPEN_IMAGE = Path("assets/avatar_sources/business_consultant_v2_open.png")
DEFAULT_BLINK_IMAGE = Path("assets/avatar_sources/business_consultant_v2_blink.png")
DEFAULT_OUTPUT = Path("data/avatar_sources/business_consultant_v2.mp4")


def _read_and_resize(path: Path, width: int, height: int) -> np.ndarray:
    image = cv2.imread(str(path))
    if image is None:
        raise FileNotFoundError(f"Unable to read image: {path}")
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)


def _eye_mask(width: int, height: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.float32)
    # Coordinates are normalized to the aligned 1086x1448 source portraits.
    for center_x, center_y in ((0.420, 0.282), (0.522, 0.279)):
        center = (int(round(width * center_x)), int(round(height * center_y)))
        axes = (
            max(1, int(round(width * 0.043))),
            max(1, int(round(height * 0.019))),
        )
        cv2.ellipse(mask, center, axes, 0, 0, 360, 1.0, thickness=-1)
    sigma = max(1.0, min(width, height) * 0.009)
    return cv2.GaussianBlur(mask, (0, 0), sigmaX=sigma, sigmaY=sigma)


def build_video(
    open_image_path: Path,
    blink_image_path: Path,
    output_path: Path,
    width: int = 576,
    height: int = 768,
    fps: int = 25,
    frame_count: int = 100,
) -> None:
    if frame_count < 20:
        raise ValueError("frame_count must be at least 20")

    open_image = _read_and_resize(open_image_path, width, height)
    blink_image = _read_and_resize(blink_image_path, width, height)
    eye_alpha = _eye_mask(width, height)[..., None]

    # Fast close, slightly slower reopen: a six-frame (240 ms) natural blink.
    blink_center = frame_count // 2
    blink_weights = {
        blink_center - 2: 0.20,
        blink_center - 1: 0.72,
        blink_center: 1.00,
        blink_center + 1: 0.82,
        blink_center + 2: 0.42,
        blink_center + 3: 0.12,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Unable to create video: {output_path}")

    try:
        for index in range(frame_count):
            weight = blink_weights.get(index, 0.0)
            if weight:
                alpha = eye_alpha * weight
                frame = np.rint(
                    open_image.astype(np.float32) * (1.0 - alpha)
                    + blink_image.astype(np.float32) * alpha
                ).clip(0, 255).astype(np.uint8)
            else:
                frame = open_image
            writer.write(frame)
    finally:
        writer.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--open-image", type=Path, default=DEFAULT_OPEN_IMAGE)
    parser.add_argument("--blink-image", type=Path, default=DEFAULT_BLINK_IMAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--frame-count", type=int, default=100)
    args = parser.parse_args()

    build_video(
        args.open_image,
        args.blink_image,
        args.output,
        frame_count=args.frame_count,
    )
    print(f"Wrote blink avatar video: {args.output}")


if __name__ == "__main__":
    main()
