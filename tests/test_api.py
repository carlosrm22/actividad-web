from __future__ import annotations

import time


def test_health_includes_idle_privacy_and_tracker(client_app):
    client, _app = client_app

    response = client.get("/api/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload["ok"] is True
    assert "idle" in payload
    assert "privacy" in payload
    assert "tracker" in payload
    assert "rules_count" in payload["privacy"]


def test_overview_and_export_endpoints(client_app, today_iso):
    client, app = client_app

    now = int(time.time())
    app.state.db.insert_session(now - 7200, now - 1800, "Kwin Wayland", "", "kdotool")

    overview = client.get(
        "/api/overview",
        params={"mode": "day", "anchor_date": today_iso, "group_by": "app"},
    )
    assert overview.status_code == 200
    overview_payload = overview.json()
    assert overview_payload["active_seconds"] > 0
    assert overview_payload["effective_seconds"] >= 0
    assert overview_payload["passive_seconds"] >= 0
    assert overview_payload["afk_seconds"] > 0
    assert overview_payload["sleep_seconds"] > 0
    assert len(overview_payload["by_hour_seconds"]) == 24
    assert len(overview_payload["by_hour_top_app"]) == 24
    assert len(overview_payload["by_hour_active_seconds"]) == 24
    assert len(overview_payload["by_hour_effective_seconds"]) == 24
    assert len(overview_payload["by_hour_passive_seconds"]) == 24
    assert len(overview_payload["by_hour_afk_seconds"]) == 24
    assert len(overview_payload["by_hour_sleep_seconds"]) == 24

    if overview_payload["by_day"]:
        first_day = overview_payload["by_day"][0]
        assert "top_app" in first_day
        assert "top_app_seconds" in first_day
        assert "effective_seconds" in first_day
        assert "passive_seconds" in first_day
        assert "afk_seconds" in first_day
        assert "sleep_seconds" in first_day

    export_json = client.get(
        "/api/export/sessions",
        params={"format": "json", "mode": "day", "anchor_date": today_iso},
    )
    assert export_json.status_code == 200
    export_json_payload = export_json.json()
    assert export_json_payload["count"] >= 1
    assert len(export_json_payload["items"]) >= 1

    export_csv = client.get(
        "/api/export/sessions",
        params={"format": "csv", "mode": "day", "anchor_date": today_iso},
    )
    assert export_csv.status_code == 200
    assert "text/csv" in export_csv.headers.get("content-type", "")
    assert "start_iso,end_iso,duration_seconds" in export_csv.text


def test_privacy_rules_crud_and_backup_restore(client_app):
    client, app = client_app

    create = client.post(
        "/api/privacy/rules",
        json={
            "scope": "title",
            "match_mode": "contains",
            "pattern": "secreto",
            "enabled": True,
        },
    )
    assert create.status_code == 200
    created = create.json()["item"]
    rule_id = created["id"]

    listing = client.get("/api/privacy/rules")
    assert listing.status_code == 200
    assert listing.json()["count"] >= 1

    patch = client.patch(f"/api/privacy/rules/{rule_id}", json={"enabled": False})
    assert patch.status_code == 200
    assert patch.json()["item"]["enabled"] is False

    backup = client.get("/api/backup/export")
    assert backup.status_code == 200
    backup_payload = backup.json()
    assert "sessions" in backup_payload
    assert "categories" in backup_payload
    assert "privacy_rules" in backup_payload

    app.state.db.clear_sessions()
    app.state.db.clear_app_categories()
    app.state.db.clear_privacy_rules()

    restore = client.post("/api/backup/restore?replace=1", json=backup_payload)
    assert restore.status_code == 200
    restore_payload = restore.json()
    assert restore_payload["ok"] is True
    assert restore_payload["inserted_sessions"] >= 1

    after = client.get("/api/categories")
    assert after.status_code == 200
    assert after.json()["count"] >= 1


def test_privacy_rule_hides_windows_payload(client_app):
    client, _app = client_app

    client.post(
        "/api/privacy/rules",
        json={
            "scope": "app",
            "match_mode": "exact",
            "pattern": "Deezer",
            "enabled": True,
        },
    )

    response = client.get("/api/windows", params={"limit": 5})
    assert response.status_code == 200
    payload = response.json()
    assert "app_counts" in payload
