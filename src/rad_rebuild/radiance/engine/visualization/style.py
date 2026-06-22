from __future__ import annotations

import matplotlib.pyplot as plt


DOCKER_SAFE_FONT = "DejaVu Sans"


def configure_visual_style() -> None:
    plt.rcParams["font.family"] = DOCKER_SAFE_FONT
    plt.rcParams["font.sans-serif"] = [DOCKER_SAFE_FONT]


def plotly_font() -> dict[str, str]:
    return {"family": DOCKER_SAFE_FONT}
