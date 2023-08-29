# In built

# External libraries.
import pytest
from fastapi.testclient import TestClient

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


class TestStartMirrorMaker:
    # Create a record
    @pytest.mark.parametrize()
    def test_start_mirror_maker_success(self):
        response = client.post("/mirror-maker/", json=payload, headers=current_user)
        assert response.status_code == 201
        assert response.json()["message"] == "Successfully created : ..."  # Adjust the expected message
        assert "status_code" in response.json()
        assert "mirror_maker_details" in response.json()  # Adjust the expected key
        assert response["key"] == "value1"

    # Un-authorize the end point.
    def test_start_mirror_maker_unauthorized(self):
        response = client.post("/mirror-maker/", json=payload, headers={"Authorization": "Bearer invalid_token"})
        assert response.status_code == 401
        # Add more checks for unauthorized response content if required gk

    def test_start_mirror_maker_internal_server_error(self):
        # Mock a database or function exception to force an internal server error
        response = client.post("/mirror-maker/", json=payload, headers=current_user)
        assert response.status_code == 500
        assert "detail" in response.json()
