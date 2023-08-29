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


class TestMirrorMakerGetAllRecords:
    # Positive test case - Retrieve all mirror maker records
    def test_get_all_mirror_maker_records_positive(self):
        response = client.get("/mirror-maker/", headers=current_user)
        assert response.status_code == status.HTTP_200_OK
        assert "Retrieved mirror maker records successfully" in response.json()
