from fastapi.testclient import TestClient

from pds_harness.webapp import app


client = TestClient(app)


def test_health_reports_enabled_drafts_and_cloud_but_submit_off():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "write_drafts_enabled": True,
        "submit_enabled": False,
        "cloud_enabled": True,
    }


def test_home_renders_confirmed_defaults():
    response = client.get("/")
    assert response.status_code == 200
    assert "黑盒测试" in response.text
    assert "文档检查" in response.text
    assert "计划提交" in response.text
    assert "PDS 专用浏览器连接" in response.text
    assert "项目材料只读测试" in response.text
    assert "模型连接测试" in response.text
    assert "project-document-links" in response.text


def test_pds_connection_is_stopped_until_user_opens_browser():
    response = client.get("/api/pds-connection/status")
    assert response.status_code == 200
    assert response.json() == {
        "state": "stopped",
        "browser_open": False,
        "authenticated": False,
        "last_error": None,
    }


def test_invalid_catalog_returns_422():
    response = client.post("/api/validate-catalog", json={"invalid": True})
    assert response.status_code == 422


def test_exception_download_requires_explicit_acknowledgement():
    response = client.post(
        "/api/projects/STS1260215/download-exception-sample", json={}
    )
    assert response.status_code == 422


def test_archive_file_route_rejects_paths_outside_archive_root():
    response = client.get(
        "/api/archive-file", params={"path": "C:/Windows/win.ini"}
    )
    assert response.status_code == 403


def test_review_package_rejects_invalid_project_code():
    response = client.get("/api/projects/not-a-project/review-package")
    assert response.status_code == 422
