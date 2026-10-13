import pytest
from fastapi.testclient import TestClient

from teglab.ansys_io import build_cases, parse_files
from teglab.api.main import create_app
from teglab.demo import thesis_a0_tt_csv, thesis_a_series_csv
from teglab.design import reference_design
from teglab.materials import default_library
from teglab.reference import VARIANTS


@pytest.fixture
def lib():
    return default_library()


@pytest.fixture
def ref_design():
    return reference_design()


@pytest.fixture
def tt_design(ref_design):
    d = ref_design.model_copy(deep=True)
    d.boundary.hot_mode = "T"
    d.boundary.t_hot_c = 100.0
    d.boundary.t_cold_c = 20.0
    return d


def variant(design, name):
    g = VARIANTS[name]
    return design.with_geometry(g["a_hot_mm"], g["a_cold_mm"], g["height_mm"])


@pytest.fixture
def a0tt_case():
    table = parse_files([("a0tt.csv", thesis_a0_tt_csv().encode())])
    cases, _ = build_cases(
        table,
        [
            {
                "index": 1,
                "name": "A0-TT",
                "meta": {
                    "bc": "TT",
                    "t_hot_c": 100,
                    "t_cold_c": 20,
                    "a_hot_mm": 1.4,
                    "a_cold_mm": 1.4,
                    "height_mm": 1.4,
                },
            }
        ],
    )
    return cases[0]


@pytest.fixture
def aseries_cases():
    table = parse_files([("aseries.csv", thesis_a_series_csv().encode())])
    defs = []
    for k, v in enumerate(["A0", "A5", "A10"]):
        g = VARIANTS[v]
        defs.append(
            {
                "index": k + 1,
                "name": f"{v}-QT",
                "meta": {
                    "bc": "QT",
                    "q_hot_w": 0.3152,
                    "t_cold_c": 20,
                    "a_hot_mm": g["a_hot_mm"],
                    "a_cold_mm": g["a_cold_mm"],
                    "height_mm": g["height_mm"],
                },
            }
        )
    cases, _ = build_cases(table, defs)
    return cases


@pytest.fixture
def client():
    app = create_app(db_path=":memory:", seed_demo=True)
    with TestClient(app) as c:
        yield c
