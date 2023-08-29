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


class TestMirrorMakerDeleteByTopic:
    # Positive test case - Delete an existing topic
    def test_delete_topic_positive(self):
        response = client.delete("/by-topic/test_topic", headers=current_user)
        assert response.status_code == status.HTTP_200_OK

    # Negative test case - Topic not found
    def test_delete_topic_not_found(self):
        response = client.delete("/by-topic/non_existing_topic", headers=current_user)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    # Negative test case - Internal server error during database operation
    def test_delete_topic_internal_server_error(self, monkeypatch):
        def mock_db_delete(*args, **kwargs):
            raise Exception("Internal server error", status.HTTP_500_INTERNAL_SERVER_ERROR)

        monkeypatch.setattr("app.api.api_v1.endpoints.mirrormaker.py", mock_db_delete)
        response = client.delete("/by-topic/test_topic", headers=current_user)
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
