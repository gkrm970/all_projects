# In built

# External libraries.
import pytest
from fastapi.testclient import TestClient
from starlette import status

# Local imports
from app.api.deps import get_db
from app.main import app
from app.tests.conftest import current_user
from app.tests.utils import payload
from app.tests.utils.database import override_get_db

# we create the dependency override and add it to the overrides for our app
app.dependency_overrides[get_db] = override_get_db

# Creating fastAPI client for Pytest-testing.
client = TestClient(app)


# Positive test case - Update mirror maker record by ID
def test_update_mirror_maker_record_by_id_positive(self):
    data = {
        "subscription_name": "updated_subscription",
        "source_topic_name": "updated_source_topic",
        "destination_topic_name": "updated_destination_topic",
        "fed_kafka_bus_name": "updated_fed_kafka_bus",
        "auth_type": "updated_auth_type"
    }
    response = client.put("/mirror-maker/1", json=data, headers=current_user)
    assert response.status_code == status.HTTP_200_OK
    assert "Updated mirror_maker_record successfully" in response.json()
    assert response.json()["Updated mirror_maker_record successfully"]["subscription_name"] == "updated_subscription"


# Negative test case - Mirror maker record not found for the given ID
def test_update_mirror_maker_record_by_id_not_found(self, monkeypatch):
    def mock_db_query(*args, **kwargs):
        return None

    monkeypatch.setattr("query", mock_db_query)
    data = {
        "subscription_name": "updated_subscription",
        "source_topic_name": "updated_source_topic",
        "destination_topic_name": "updated_destination_topic",
        "fed_kafka_bus_name": "updated_fed_kafka_bus",
        "auth_type": "updated_auth_type"
    }
    response = client.put("/mirror-maker/999", json=data)
    assert response.status_code == status.HTTP_404_NOT_FOUND


# Negative test case - Internal server error during database operation
def test_update_mirror_maker_record_by_id_internal_server_error(self, monkeypatch):
    def mock_db_query(*args, **kwargs):
        raise Exception("Internal server error")

    monkeypatch.setattr("app.api.api_v1.endpoints.mirrormaker.py", mock_db_query)
    data = {
        "subscription_name": "updated_subscription",
        "source_topic_name": "updated_source_topic",
        "destination_topic_name": "updated_destination_topic",
        "fed_kafka_bus_name": "updated_fed_kafka_bus",
        "auth_type": "updated_auth_type"
    }
    response = client.put("/mirror-maker/1", json=data, headers=current_user)
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
