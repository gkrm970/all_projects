from fastapi import status
from fastapi.exceptions import HTTPException
from fastapi.param_functions import Depends
from fastapi.security.oauth2 import OAuth2AuthorizationCodeBearer
from jose import jwt
from jose.exceptions import ExpiredSignatureError
from pydantic import ValidationError

from app import schemas
from app.core.config import settings
import logging
import requests

ALGORITHM = "RS256"
API_NAME = "TELUS-Mirrormaker-Cookiecutter"
TINAA_LOGGER = logging.getLogger(API_NAME)

reusable_oauth2 = OAuth2AuthorizationCodeBearer(
    authorizationUrl = settings.AUTH_AUTHORIZATION_URL,
    tokenUrl = settings.AUTH_TOKEN_URL,
    refreshUrl = settings.AUTH_TOKEN_URL,
)


def get_jwk():
    try:
        url = settings.AUTH_CERTS_URL
        saved_jwk = settings.JWK
        if not saved_jwk:
            headers = {
                'Content-Type':'application/json'
            }
            response = requests.request("GET", url, headers = headers, verify = settings.SSL_VERIFY)
            json_response = response.json()
            jwk = json_response['keys'][0]
            settings.JWK = jwk
            return jwk
        else:
            return saved_jwk
    except Exception as e:
        # logger.error(f"get_jwk error: {e}")
        raise e


def get_current_user(
        token: str = Depends(reusable_oauth2)
) -> schemas.TokenPayload:
    try:
        payload = jwt.decode(
            token,
            get_jwk(),
            algorithms = [ALGORITHM],
            audience = "account",
        )
        token_data = schemas.TokenPayload(**payload)

    except ExpiredSignatureError:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = {
                "error":{
                    "code":401,
                    "message":"Request had invalid authentication credentials",
                    "status":"UNAUTHENTICATED",
                    "details":{
                        "type":"Auth error",
                        "reason":"ACCESS_TOKEN_TYPE_UNSUPPORTED"
                    }
                }
            }
        )

    except Exception as e:
        raise e

    return token_data
