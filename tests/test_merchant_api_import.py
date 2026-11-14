from fastapi.testclient import TestClient
import pytest

from spending_api import merchant_ai
from spending_api.merchant_import import DatasetStore
from spending_api.merchant_main import create_app

CSV = 'kind,id,customer_id,order_id,date,amount_cents,status\norder,o1,c1,,2026-11-01,1000,completed\norder,o2,c1,,2026-11-02,1500,completed\nrefund,r1,,o1,2026-11-03,200,\n'
PAYLOAD = {"csv": CSV, "business_name": "Corner Cafe", "synthetic": False}
HEADERS = {"Authorization": "Bearer owner-token", "Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    monkeypatch.delenv("OWNER_DATA_DIR", raising=False)
    monkeypatch.setenv("OWNER_AI_ENABLED", "false")


def import_data(client, payload=PAYLOAD):
    response = client.post("/api/v1/owner/import/preview", json=payload, headers=HEADERS)
    assert response.status_code == 200, response.text
    preview = response.json()
    response = client.post("/api/v1/owner/import/commit", json={**payload, "preview_hash": preview["preview_hash"]}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["dataset_id"]


def test_preview_commit_restart_selection_and_metadata(tmp_path):
    with TestClient(create_app(token="owner-token", data_dir=tmp_path)) as client:
        assert client.get("/health").json() == {"status": "ok", "application": "regulars"}
        preview = client.post("/api/v1/owner/import/preview", json=PAYLOAD, headers=HEADERS)
        assert preview.status_code == 200
        listing = client.get("/api/v1/owner/datasets", headers=HEADERS).json()
        assert listing["active_id"] == "demo" and len(listing["datasets"]) == 1
        key = import_data(client)
        data = client.get("/api/v1/owner/dashboard", headers=HEADERS).json()
        assert data["source"]["dataset_id"] == key
        assert data["source"]["synthetic"] is False
        assert data["business"]["name"] == "Corner Cafe"
        assert data["business"]["id"] == "owner-business"
        assert data["period"] == {"start": "2026-11-01", "end": "2026-11-03"}
        assert data["metrics"]["net_revenue_cents"] == 2300
        assert not any("fictional" in value for value in data["source"]["limitations"])
        assert import_data(client) == key  # idempotent duplicate import
        assert len(client.get("/api/v1/owner/datasets", headers=HEADERS).json()["datasets"]) == 2
    with TestClient(create_app(token="owner-token", data_dir=tmp_path)) as restarted:
        assert restarted.get("/api/v1/owner/datasets", headers=HEADERS).json()["active_id"] == key
        restored = restarted.post("/api/v1/owner/datasets/select", json={"dataset_id": "demo"}, headers=HEADERS)
        assert restored.status_code == 200
        assert restarted.get("/api/v1/owner/dashboard", headers=HEADERS).json()["source"]["synthetic"] is True
        assert len(restarted.get("/api/v1/owner/datasets", headers=HEADERS).json()["datasets"]) == 2


def test_import_auth_origin_json_bounds_and_failed_commit_preserve_selection(tmp_path):
    with TestClient(create_app(token="owner-token", data_dir=tmp_path)) as client:
        for path in ("datasets", "import/template.csv", "compare"):
            assert client.get("/api/v1/owner/" + path).status_code == 401
        for path in ("import/preview", "import/commit", "datasets/select"):
            assert client.post("/api/v1/owner/" + path, json={}).status_code == 401
        hostile = {**HEADERS, "Origin": "http://attacker.example"}
        assert client.post("/api/v1/owner/import/preview", json=PAYLOAD, headers=hostile).status_code == 403
        assert client.post("/api/v1/owner/import/preview", content="hello", headers=HEADERS).status_code == 415
        assert client.post("/api/v1/owner/import/preview", content='{"csv":"' + "x" * 800_000 + '"}', headers={**HEADERS, "Content-Type": "application/json"}).status_code == 413
        assert client.post("/api/v1/owner/import/preview", json={**PAYLOAD, "synthetic": "false"}, headers=HEADERS).status_code == 422
        assert client.post("/api/v1/owner/import/preview", json={**PAYLOAD, "csv": CSV.replace("1000", "-1")}, headers=HEADERS).status_code == 422
        response = client.post("/api/v1/owner/import/commit", json={**PAYLOAD, "preview_hash": "0" * 64}, headers=HEADERS)
        assert response.status_code == 409
        assert client.post("/api/v1/owner/datasets/select", json={"dataset_id": "f" * 64}, headers=HEADERS).status_code == 404
        assert client.get("/api/v1/owner/datasets", headers=HEADERS).json()["active_id"] == "demo"
        assert len(client.get("/api/v1/owner/datasets", headers=HEADERS).json()["datasets"]) == 1


def test_comparison_export_and_ask_use_active_import(tmp_path):
    with TestClient(create_app(token="owner-token", data_dir=tmp_path)) as client:
        key = import_data(client)
        compared = client.get("/api/v1/owner/compare?start=2026-11-02&end=2026-11-03", headers=HEADERS)
        assert compared.status_code == 200, compared.text
        result = compared.json()
        assert result["source"]["dataset_id"] == key
        assert result["current"]["metrics"]["net_revenue_cents"] == 1300
        assert result["baseline"]["metrics"]["net_revenue_cents"] == 1000
        assert result["delta_cents"] == 300
        assert result["fully_covered"] is False
        exported = client.get("/api/v1/owner/export.csv", headers=HEADERS)
        assert ",2300," in exported.text and ",False" in exported.text
        answer = client.post("/api/v1/owner/ask", json={"question": "How are sales?"}, headers=HEADERS).json()
        assert "$23.00" in answer["answer"]
        assert answer["source"]["dataset_id"] == key


def test_no_implicit_storage_and_extreme_dates_fail_cleanly():
    with TestClient(create_app(token="owner-token")) as client:
        assert client.get("/api/v1/owner/status", headers=HEADERS).json()["can_import"] is False
        assert client.post("/api/v1/owner/import/preview", json=PAYLOAD, headers=HEADERS).status_code == 503
        for endpoint in ("dashboard", "compare"):
            for day in ("0001-01-01", "9999-12-31"):
                assert client.get(f"/api/v1/owner/{endpoint}?end={day}", headers=HEADERS).status_code == 422
        assert "kind,id,customer_id" in client.get("/api/v1/owner/import/template.csv", headers=HEADERS).text


def test_ai_uses_one_bound_snapshot_and_reports_its_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(merchant_ai, "status", lambda: {"enabled": True, "available": True})
    seen = []
    async def fake_investigate(snapshot, question):
        seen.append(snapshot)
        DatasetStore(tmp_path).select("demo")  # concurrent selection cannot change bound result
        return {"status": "answered", "answer": "A mocked AI answer", "evidence": []}
    monkeypatch.setattr(merchant_ai, "investigate", fake_investigate)
    with TestClient(create_app(token="owner-token", data_dir=tmp_path)) as client:
        key = import_data(client)
        result = client.post("/api/v1/owner/ask", json={"question": "How are sales?"}, headers=HEADERS).json()
        assert seen[0]["metrics"]["net_revenue_cents"] == 2300
        assert result["source"]["dataset_id"] == key
        assert client.get("/api/v1/owner/datasets", headers=HEADERS).json()["active_id"] == "demo"


def test_offline_action_and_slipping_prompts_are_distinct():
    with TestClient(create_app(token="owner-token")) as client:
        quiet = client.post("/api/v1/owner/ask", json={"question": "Which quiet customers should return?"}, headers=HEADERS).json()
        assert "does not prove churn" in quiet["answer"]
        plan = client.post("/api/v1/owner/ask", json={"question": "Plan an experiment for returning customers"}, headers=HEADERS).json()
        assert "holdout" in plan["answer"]
        assert "No customer has been assigned" in plan["answer"]


def test_calendar_dates_reject_timestamps_and_numeric_coercions(tmp_path):
    with TestClient(create_app(token="owner-token", data_dir=tmp_path)) as client:
        for invalid in (0, 1793577600, "0", "2026-11-01T00:00:00", "20261101"):
            body = {"question": "sales", "start": invalid}
            assert client.post("/api/v1/owner/ask", json=body, headers=HEADERS).status_code == 422
        for invalid in ("0", "1793577600", "20261101", "2026-11-01T00:00:00"):
            for endpoint in ("dashboard", "compare", "export.csv"):
                assert client.get(f"/api/v1/owner/{endpoint}?start={invalid}", headers=HEADERS).status_code == 422
        assert client.post("/api/v1/owner/import/preview", json=PAYLOAD,
                           headers={**HEADERS, "Origin": "http://[invalid"}).status_code == 403


def test_experiment_plan_preserves_audience_when_budget_is_exceeded():
    body = {"segment": "slipping", "max_customers": 20, "offer_cents": 500,
            "window_days": 14, "budget_cents": 1000}
    with TestClient(create_app(token="owner-token")) as client:
        assert client.post("/api/v1/owner/experiments/plan", json=body).status_code == 401
        response = client.post("/api/v1/owner/experiments/plan", json=body, headers=HEADERS)
        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["proposed_audience_cap"] == 20
        assert plan["treatment_customers"] == 10
        assert plan["maximum_offer_cost_cents"] == 5000
        assert plan["within_budget"] is False and plan["feasible_pilot"] is False
        assert plan["source"]["dataset_id"] == "demo"
        assert "No customer is assigned" in " ".join(plan["limitations"])
        for changed in ({"offer_cents": True}, {"max_customers": 10.0}, {"window_days": 0}, {"budget_cents": -1}, {"end": "20261101"}):
            assert client.post("/api/v1/owner/experiments/plan", json={**body, **changed}, headers=HEADERS).status_code == 422


def test_experiment_plan_uses_active_dataset(tmp_path):
    with TestClient(create_app(token="owner-token", data_dir=tmp_path)) as client:
        key = import_data(client)
        body = {"segment": "promising", "max_customers": 20, "offer_cents": 500, "window_days": 14}
        response = client.post("/api/v1/owner/experiments/plan", json=body, headers=HEADERS)
        assert response.status_code == 200
        plan = response.json()
        assert plan["source"]["dataset_id"] == key
        assert plan["observed_segment_customers"] == 1
        assert plan["proposed_audience_cap"] == 1
        assert plan["feasible_pilot"] is False


def test_export_identifies_bound_dataset_revision(tmp_path):
    with TestClient(create_app(data_dir=tmp_path, token='')) as client:
        dashboard = client.get('/api/v1/owner/dashboard').json()
        response = client.get('/api/v1/owner/export.csv')
        assert response.status_code == 200
        assert response.headers['X-Source-Revision'] == dashboard['source']['revision']
        assert response.headers['X-Dataset-ID'] == dashboard['source']['dataset_id']


def test_malformed_quoted_header_returns_validation_error_without_switching(tmp_path):
    with TestClient(create_app(data_dir=tmp_path, token='')) as client:
        response = client.post('/api/v1/owner/import/preview', json={
            'business_name': 'Cafe',
            'csv': '"kind,id,customer_id,order_id,date,amount_cents,status\norder,o1,c1,,2026-11-01,1000,completed\n',
        })
        assert response.status_code == 422
        assert client.get('/api/v1/owner/datasets').json()['active_id'] == 'demo'


def test_pilot_api_rejects_unobserved_future_end():
    with TestClient(create_app(token='')) as client:
        response = client.post('/api/v1/owner/experiments/plan', json={
            'segment':'slipping','max_customers':20,'offer_cents':250,'window_days':14,
            'start':'2026-11-15','end':'2026-12-14',
        })
        assert response.status_code == 422
        assert 'within observed coverage' in response.json()['detail']


def test_offline_revenue_change_uses_same_comparison_as_dashboard():
    with TestClient(create_app(token='')) as client:
        comparison = client.get('/api/v1/owner/compare').json()
        result = client.post('/api/v1/owner/ask', json={'question':'What changed in revenue this period?'}).json()
        assert result['status'] == 'answered'
        evidence = result['evidence'][0]
        assert evidence['delta_cents'] == comparison['delta_cents']
        assert sum(row['delta_cents'] for row in evidence['groups']) == comparison['delta_cents']
        assert 'not why customer behavior changed' in result['answer']
        assert result['source']['dataset_id'] == 'demo'
        assert 'contributors' not in evidence


def test_imported_short_history_change_discloses_unknown_baseline(tmp_path):
    with TestClient(create_app(token='owner-token',data_dir=tmp_path)) as client:
        key = import_data(client)
        result = client.post('/api/v1/owner/ask', json={'question':'Why did sales go up?'}, headers=HEADERS).json()
        assert result['source']['dataset_id'] == key
        assert result['evidence'][0]['fully_covered'] is False
        assert result['evidence'][0]['change_fraction'] is None
        assert 'unknown dates' in result['answer']
