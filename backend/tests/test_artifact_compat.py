"""Saved artifacts must have been produced by the exact scikit-learn / xgboost pinned in requirements.txt."""
from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import joblib
import sklearn
import xgboost

ROOT = Path(__file__).resolve().parents[1]


def _pins() -> dict[str, str]:
    pins = {}
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.\[\]-]+)==([\w.]+)", line.strip())
        if m:
            pins[m.group(1).split("[")[0].lower()] = m.group(2)
    return pins


def test_requirements_pin_the_installed_ml_stack():
    pins = _pins()
    assert pins["scikit-learn"] == sklearn.__version__
    assert pins["xgboost"] == xgboost.__version__


def test_artifacts_were_trained_with_the_pinned_versions():
    for path in ("models/model_metadata.json", "outputs/forecasting/forecast_metrics.json"):
        data = json.loads((ROOT / path).read_text())
        rv = data.get("runtime_versions") or data["metadata"]["runtime_versions"]
        assert rv["scikit_learn"] == sklearn.__version__, f"{path} trained with sklearn {rv['scikit_learn']}"
        assert rv["xgboost"] == xgboost.__version__, f"{path} trained with xgboost {rv['xgboost']}"


def test_every_artifact_loads_without_any_version_warning():
    files = sorted((ROOT / "models").rglob("*.pkl")) + sorted((ROOT / "models").rglob("*.joblib"))
    assert len(files) == 13  # 3 classifier files + 10 forecasting models
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for f in files:
            joblib.load(f)
    version_warnings = [str(w.message) for w in caught if "version" in str(w.message).lower()]
    assert not version_warnings, version_warnings


def test_classifier_and_forecasts_predict_without_warnings():
    from forecasting.forecast_service import ForecastService
    from services.traffic_service import TrafficService
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        t = TrafficService()
        p = t.predict_next("Bus Stand")
        f30 = ForecastService().forecast("Bus Stand", 30)
        f60 = ForecastService().forecast("Airport Road", 60, "xgboost")
    assert p["predicted_congestion"] in {"Low", "Medium", "High"} and abs(sum(p["class_probabilities"].values()) - 1) < 1e-6
    assert f30["vehicle_count"] > 0 and f60["avg_speed"] > 0
    assert not [w for w in caught if "sklearn" in str(w.filename) or "version" in str(w.message).lower()], [str(w.message) for w in caught]


def _embedded_sklearn_version(obj):
    try:
        state = obj.__getstate__()
    except Exception:
        return None
    return state.get("_sklearn_version") if isinstance(state, dict) else None


def test_every_pickle_embeds_the_installed_sklearn_version():
    """Reads the version stamped inside each pickle (independent of metadata files)."""
    checked = 0
    for f in sorted((ROOT / "models").rglob("*.pkl")) + sorted((ROOT / "models").rglob("*.joblib")):
        obj = joblib.load(f)
        obj = obj["model"] if isinstance(obj, dict) and "model" in obj else obj
        if isinstance(obj, list):  # feature_cols.pkl is a plain list
            continue
        v = _embedded_sklearn_version(obj)
        assert v == sklearn.__version__, f"{f.name} was pickled with scikit-learn {v}"
        checked += 1
    assert checked == 12
