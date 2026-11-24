import pandas as pd

from services.weather_service import WeatherService
from forecasting.forecast_pipeline import build_forecast_frame, regression_metrics


def test_simulated_weather_fallback_is_explicitly_labelled():
    result = WeatherService().simulated_fallback(seed=7)
    assert result["is_simulated"] is True
    assert result["is_live"] is False
    assert result["source"] == "synthetic_weather_fallback"
    assert "SIMULATED WEATHER" in result["notice"]


def test_forecast_frame_has_lags_and_future_targets_without_future_features():
    rows = []
    times = pd.date_range("2024-01-01", periods=40, freq="30min")
    for i, ts in enumerate(times):
        rows.append({"timestamp": ts, "location": "Road A", "vehicle_count": 100 + i,
                     "avg_speed": 50 - i * 0.2, "rainfall": i % 2, "clouds": 20 + i,
                     "temperature": 25, "congestion_label": "Low" if i < 20 else "Medium"})
    frame, features = build_forecast_frame(pd.DataFrame(rows), 2)
    assert "target_vehicle_count" not in features
    assert "target_avg_speed" not in features
    assert "target_congestion_label" not in features
    assert "lag_vehicle_count_0" in features
    assert (pd.to_datetime(frame.target_timestamp) - pd.to_datetime(frame.timestamp)).eq(pd.Timedelta(minutes=60)).all()


def test_regression_metrics_are_finite():
    metrics = regression_metrics([10, 20, 30], [11, 18, 33])
    assert metrics["n"] == 3
    assert metrics["mae"] > 0 and metrics["rmse"] > 0 and metrics["smape_pct"] > 0


def test_open_meteo_adapter_parses_current_payload_and_caches():
    import httpx
    from services.weather_service import WeatherService

    calls = {"count": 0}
    def handler(request):
        calls["count"] += 1
        return httpx.Response(200, json={
            "timezone": "Asia/Kolkata",
            "current": {
                "time": "2026-11-05T15:00",
                "temperature_2m": 29.4,
                "relative_humidity_2m": 61,
                "precipitation": 0.2,
                "rain": 0.2,
                "showers": 0.0,
                "weather_code": 61,
                "cloud_cover": 74,
                "wind_speed_10m": 12.0,
                "wind_direction_10m": 180,
                "wind_gusts_10m": 20.0,
            },
            "current_units": {"temperature_2m": "°C"},
        })
    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = WeatherService(client=client, cache_ttl_seconds=600)
    first = service.get_current(force_refresh=True)
    second = service.get_current()
    assert first["status"] == "ok" and first["is_live"] is True
    assert first["temperature_c"] == 29.4 and first["rain_mm"] == 0.2
    assert second["cache_status"] == "fresh_cache"
    assert calls["count"] == 1


def test_weather_errors_do_not_silently_turn_into_live_values():
    import httpx
    from services.weather_service import WeatherService

    def handler(request):
        raise httpx.ConnectError("offline", request=request)
    service = WeatherService(client=httpx.Client(transport=httpx.MockTransport(handler)))
    unavailable = service.get_current(force_refresh=True, allow_simulated_fallback=False)
    simulated = service.get_current(force_refresh=True, allow_simulated_fallback=True)
    assert unavailable["status"] == "unavailable" and unavailable["is_live"] is False
    assert simulated["status"] == "simulated_fallback" and simulated["is_simulated"] is True


def test_forecast_features_at_origin_do_not_change_when_future_measurements_change():
    rows = []
    times = pd.date_range("2024-01-01", periods=40, freq="30min")
    for i, ts in enumerate(times):
        rows.append({"timestamp": ts, "location": "Road A", "vehicle_count": 100 + i,
                     "avg_speed": 50 - i * 0.2, "rainfall": i % 2, "clouds": 20 + i,
                     "temperature": 25 + i * 0.1, "congestion_label": "Low" if i < 20 else "Medium"})
    raw = pd.DataFrame(rows)
    frame_a, features = build_forecast_frame(raw, 2)
    origin = times[10]
    row_a = frame_a.loc[frame_a.timestamp == origin].iloc[0]
    altered = raw.copy()
    future = altered.timestamp > origin
    altered.loc[future, "vehicle_count"] += 10000
    altered.loc[future, "avg_speed"] = 1
    altered.loc[future, "rainfall"] += 50
    altered.loc[future, "clouds"] = 100
    altered.loc[future, "temperature"] += 20
    frame_b, _ = build_forecast_frame(altered, 2)
    row_b = frame_b.loc[frame_b.timestamp == origin].iloc[0]
    pd.testing.assert_series_equal(row_a[features], row_b[features], check_names=False)
    assert row_a["target_vehicle_count"] != row_b["target_vehicle_count"]
