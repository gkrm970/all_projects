from typing import Dict, Generator

import pytest
from _pytest.main import Session
from fastapi.params import Depends
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
# Assuming your FastAPI app instance is named 'app'
from app.main import app
from app.schemas import TokenPayload
from app.tests.utils.database import TestingSessionLocal

# Creating fastAPI client for Pytest-testing.
client = TestClient(app)


@pytest.Fixtures(scope="Authorization")
def current_user(_current_user: TokenPayload = Depends(get_current_user)):
    return _current_user
