from pathlib import Path

import pytest

from local_game_data import extract


@pytest.fixture(scope="session")
def extracted(tmp_path_factory):
    source = Path(__file__).resolve().parents[2] / "sts2_source_code"
    if not source.is_dir():
        pytest.skip("Real recovered game source is local-only; see README for integration tests")
    output = tmp_path_factory.mktemp("game-data")
    extract(source, "0.113.1", output)
    return output
