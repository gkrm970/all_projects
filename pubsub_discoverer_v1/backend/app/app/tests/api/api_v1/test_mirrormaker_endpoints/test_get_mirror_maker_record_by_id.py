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


class TestMirrorMakerGetById:
    # Positive test case - Retrieve mirror maker record by ID
    def test_get_mirror_maker_record_by_id_positive(self):
        response = client.get("/mirror-maker/known_id")
        assert response.status_code == status.HTTP_200_OK
        assert "Retried mirror_maker_record successfully" in response.json()

    # Negative test case - Mirror maker record not found for the given ID
    def test_get_mirror_maker_record_by_id_not_found(self, monkeypatch):
        response = client.get("/mirror-maker/999")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    # Negative test case - Internal server error during database operation
    def test_get_mirror_maker_record_by_id_internal_server_error(self, monkeypatch, db):
        def mock_db_query(*args, **kwargs):
            raise Exception("Internal server error")

        monkeypatch.setattr(db, "app.api.api_v1.endpoints.mirrormaker.py", mock_db_query)  # Corrected monkeypatch
        response = client.get("/mirror-maker/1", headers=current_user)
        assert response.status_code == 500  # status.HTTP_500_INTERNAL_SERVER_ERROR is not needed here
