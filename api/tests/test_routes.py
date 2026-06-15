"""Route-level integration tests using FastAPI TestClient."""

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.parameters import SYMBOL_PARAMS

client = TestClient(app)

_VALID_PAYLOAD = {
    "symbol": "AAPL",
    "order_size": 100_000,
    "horizon_hours": 2.0,
    "schedule_type": "twap",
}


def test_analyse_valid_returns_200():
    response = client.post("/analyse", json=_VALID_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert "frontier" in body
    assert "schedule" in body
    assert "impact_decomp" in body
    assert "model_params" in body
    assert isinstance(body["frontier"], list)
    assert isinstance(body["schedule"], list)


def test_analyse_negative_order_size_returns_422():
    response = client.post("/analyse", json={**_VALID_PAYLOAD, "order_size": -1})
    assert response.status_code == 422


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_regime_frontier_returns_three_arrays():
    response = client.post("/api/regime-frontier", json=_VALID_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) >= {"calm", "normal", "stressed"}
    for regime in ("calm", "normal", "stressed"):
        assert isinstance(body[regime], list)
        assert len(body[regime]) > 0


@pytest.mark.parametrize("symbol", list(SYMBOL_PARAMS))
def test_analyse_all_symbols_return_200(symbol):
    response = client.post("/analyse", json={**_VALID_PAYLOAD, "symbol": symbol})
    assert response.status_code == 200


def test_ask_page_returns_html():
    response = client.get("/ask")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "<title>Ask the model" in body
    assert 'id="trace"' in body


def test_calibration_run_returns_fitted_params():
    response = client.post("/api/calibration/run", json={"seed": 42, "n_orders": 500})
    assert response.status_code == 200
    body = response.json()
    assert "fitted_params" in body and "fit_stats" in body and "plot_data" in body
    fp = body["fitted_params"]
    assert "eta_hat" in fp and "gamma_hat" in fp
    assert fp["eta_hat"] > 0 and fp["gamma_hat"] > 0
