from enum import Enum

class HttpStatus(Enum):
    OK = 200
    CREATED = 201
    ACCEPTED = 202
    NO_CONTENT = 204

class CeleryState(Enum):
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"
    DELETED = "DELETED"


def http_status_codes():
    return [code.value for code in HttpStatus]


http_status = http_status_codes()
