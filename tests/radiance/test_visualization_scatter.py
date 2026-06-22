from __future__ import annotations

import numpy as np

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.engine.visualization.overlays import OverlayContext  # noqa: E402
from rad_rebuild.radiance.engine.visualization import scatter as scatter_module  # noqa: E402
from rad_rebuild.radiance.engine.visualization.scatter import build_scatter_figure  # noqa: E402


def test_scatter_figure_hides_legend_noise_and_labels_ppfd_units() -> None:
    df = scatter_module.pd.DataFrame(
        {
            "x": [0.0, 1.0, 0.0, 1.0],
            "y": [0.0, 0.0, 1.0, 1.0],
            "z": [0.0, 0.0, 0.0, 0.0],
            "ppfd": [900.0, 980.0, 1020.0, 1100.0],
        }
    )
    overlay_context = OverlayContext(
        plane="xy",
        points=[("SMD centers", [(0.5, 0.5, 0.0)], {})],
        room_bounds=(1.0, 1.0),
    )

    fig = build_scatter_figure(
        df,
        np.asarray(df.x, dtype=np.float64),
        np.asarray(df.y, dtype=np.float64),
        "x (m)",
        "y (m)",
        1000.0,
        800.0,
        1200.0,
        "viridis",
        overlay_context,
    )
    payload = fig.to_dict()

    assert payload["layout"]["showlegend"] is False
    assert payload["data"][0]["name"] == "PPFD samples"
    assert payload["data"][0]["showlegend"] is False
    assert payload["data"][1]["name"] == "SMD centers"
    assert payload["data"][1]["showlegend"] is False
    assert payload["data"][0]["marker"]["colorbar"]["title"]["text"] == ""
    assert "µmol/m²/s" in payload["layout"]["scene"]["zaxis"]["title"]["text"]
    assert "<extra></extra>" in payload["data"][0]["hovertemplate"]
