from vayunetra.ingestion.static_layers import _build_grid


def test_grid_covers_bbox_with_expected_count():
    # Roughly 50 km x 50 km box centered on Delhi → ~2500 cells at 1km
    bbox = (76.84, 28.40, 77.35, 28.88)
    cells = _build_grid(bbox, cell_m=1000)
    # Sanity bounds — building blocks for full validation in Phase 1.7.
    assert 1500 <= len(cells) <= 3500
    # All cell ids unique
    ids = {c["cell_id"] for c in cells}
    assert len(ids) == len(cells)


def test_grid_resolution_doubling_quarters_count():
    bbox = (77.0, 28.5, 77.2, 28.7)
    a = _build_grid(bbox, cell_m=1000)
    b = _build_grid(bbox, cell_m=2000)
    assert 0.20 <= len(b) / len(a) <= 0.30
