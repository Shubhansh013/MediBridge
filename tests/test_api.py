"""Automated integration tests for MediBridge REST API Gateway."""

import pytest
from fastapi.testclient import TestClient

from api.main import app

@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_health_check(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"


def test_medicine_search(client):
    response = client.get("/api/medicines/search", params={"q": "dolo"})
    assert response.status_code == 200
    items = response.json()
    assert len(items) >= 1
    assert any("Dolo" in item["brand_name"] for item in items)


def test_medicine_details_and_substitutes(client):
    response = client.get("/api/medicines/1")
    assert response.status_code == 200
    med = response.json()
    assert med["id"] == 1
    assert "Dolo 650" in med["brand_name"]

    sub_res = client.get("/api/medicines/1/substitutes")
    assert sub_res.status_code == 200
    substitutes = sub_res.json()
    assert len(substitutes) >= 1


def test_nearby_pharmacies(client):
    # Delhi Connaught Place coordinates
    response = client.get(
        "/api/pharmacies/nearby",
        params={"lat": 28.6139, "lon": 77.2090, "radius_km": 15},
    )
    assert response.status_code == 200
    pharmacies = response.json()
    assert len(pharmacies) > 0
    assert pharmacies[0]["distance_km"] <= pharmacies[-1]["distance_km"]


def test_pharmacy_inventory(client):
    response = client.get("/api/pharmacies/1/inventory")
    assert response.status_code == 200
    inventory = response.json()
    assert len(inventory) > 0
    assert "stock_status" in inventory[0]


def test_proposal_lifecycle_and_human_in_the_loop(client):
    # 1. Unauthorized attempt (patient user 1 attempting update on pharmacy 1) -> 403
    unauth_resp = client.post(
        "/api/proposals",
        json={
            "raw_message": "Dolo 650 15 strips",
            "chemist_user_id": 1,
            "pharmacy_id": 1,
        },
    )
    assert unauth_resp.status_code == 403

    # 2. Authorized chemist (user 3 linked to pharmacy 1) -> creates proposal with status PENDING
    create_resp = client.post(
        "/api/proposals",
        json={
            "raw_message": "Dolo 650 15 strips available",
            "chemist_user_id": 3,
            "pharmacy_id": 1,
        },
    )
    assert create_resp.status_code == 200
    data = create_resp.json()
    assert data["status"] == "CREATED"
    assert len(data["proposals"]) >= 1
    proposal = data["proposals"][0]
    proposal_id = proposal["id"]
    assert proposal["status"] == "PENDING"

    # 3. Confirm proposal -> transitions to CONFIRMED and updates inventory
    confirm_resp = client.post(
        f"/api/proposals/{proposal_id}/confirm",
        json={"confirm_by_user_id": 3, "pharmacy_id": 1},
    )
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["success"] is True

    # 4. Audit history contains the change
    hist_resp = client.get("/api/pharmacies/1/history")
    assert hist_resp.status_code == 200
    history = hist_resp.json()
    assert len(history) >= 1
    latest_hist_id = history[0]["id"]

    # 5. Undo last change
    undo_resp = client.post(
        f"/api/history/{latest_hist_id}/undo",
        json={"undone_by_user_id": 3, "pharmacy_id": 1},
    )
    assert undo_resp.status_code == 200
    assert undo_resp.json()["success"] is True


def test_proposal_rejection(client):
    # Create proposal
    create_resp = client.post(
        "/api/proposals",
        json={
            "raw_message": "Crocin 650 5 strips",
            "chemist_user_id": 3,
            "pharmacy_id": 1,
        },
    )
    assert create_resp.status_code == 200
    prop_id = create_resp.json()["proposals"][0]["id"]

    # Reject proposal
    reject_resp = client.post(
        f"/api/proposals/{prop_id}/reject",
        json={
            "reject_by_user_id": 3,
            "pharmacy_id": 1,
            "rejection_note": "Count verified as inaccurate",
        },
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "REJECTED"


def test_pharmacist_substitution_approval(client):
    response = client.post(
        "/api/substitutions/approve",
        json={
            "pharmacy_id": 1,
            "requested_medicine_id": 1,
            "substitute_medicine_id": 2,
            "approving_pharmacist_id": 7,
            "status": "approved",
            "request_reference": "REQ-101",
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
