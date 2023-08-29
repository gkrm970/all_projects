from typing import Optional, List
import urllib.parse
import socket

from pydantic import BaseModel, Field, validator, root_validator
from enum import Enum
import re


class PushConfig(BaseModel):
    # pushEndpoint: str = Field(example='https://my-webhook.osc.tac.net')
    pushEndpoint: Optional[str] = Field(example='https://webhook.site/67c64204-c49f-46dd-8042-0d338fbcec72')

    @validator('pushEndpoint')
    def validate_push_endpoint(cls, value):
        parsed_url = urllib.parse.urlparse(value)
        host = parsed_url.hostname
        port = parsed_url.port
        scheme = parsed_url.scheme
        if port is None:
            if scheme == 'http':
                port = 80
            elif scheme == 'https':
                port = 443
        try:
            socket.create_connection((host, port), timeout=5)
        except:    
            raise ValueError("Push endpoint is not reachable")
        return value


def convert_time_to_days(time_str):
    """
    Converts a time string in the format of either '2678400s'
    into a number of days. Assumes that there are 30.44 days in a month and 365 days
    in a year.
    """
    time_dict = {
        's': 1 / 86400,
        # 'h': 1 / 24,
        # 'm': 1 / 1440,
        # 'd': 1
    }
    time_num = int(time_str[:-1])
    time_unit = time_str[-1]
    days = time_num * time_dict[time_unit]

    return days


class ExpirationPolicy(BaseModel):
    ttl: str = Field(description='A policy that specifies the conditions for this subscriptions '
                                 'expiration. A subscription is considered active as long as any '
                                 'connected subscriber is successfully consuming messages from the '
                                 'subscription or is issuing operations on the subscription. If '
                                 'expirationPolicy is not set, a default policy with ttl of 31 days '
                                 'will be used. The minimum allowed value for expirationPolicy.ttl '
                                 'is 1 day & maximum is 31days. If expirationPolicy is set, '
                                 'but expirationPolicy.ttl is not set, a default policy with ttl of '
                                 '31 days will be used.', example='604800s')

    @validator('ttl')
    def validate_ttl(cls, v):
        # ttl_re = re.compile(r'^\d+[s|m|h|d]$')
        ttl_re = re.compile(r'^\d+[s|]$')
        sec = v[:-1]
        if not ttl_re.match(v):
            raise ValueError('TTL must be in seconds (s)')
        else:
            if int(sec) <= 0 or int(sec) > 604800:
                raise ValueError('expirationPolicy.ttl must be between 1 and 604800 seconds.')
            return v


class RetryPolicy(BaseModel):
    minimumBackoff: str = Field(example='3.5s')
    maximumBackoff: str = Field(example='600s')

    @validator('minimumBackoff', 'maximumBackoff')
    def validate_backoff(cls, v):
        sec = v[:-1]
        backoff_re = re.compile(r'^\d+(\.\d+)?[s]$')
        if not backoff_re.match(v):
            raise ValueError('Backoff time must be in seconds with optional decimals minimumBackoff (e.g. 3.5s), '
                             'maximumBackoff (e.g: 600s)')
        return v

    @root_validator
    def validate_minimum_backoff(cls, values):
        minimum_backoff = values.get('minimumBackoff')
        if minimum_backoff:
            minimum_backoff_seconds = float(minimum_backoff[:-1])
            if minimum_backoff_seconds <= 0 or minimum_backoff_seconds > 600:
                raise ValueError('Minimum backoff value should be between 1 and 600 econds. Defaults to 10 seconds')
        return values

    @root_validator
    def validate_maximum_backoff(cls, values):
        maximum_backoff = values.get('maximumBackoff')
        if maximum_backoff:
            maximum_backoff_seconds = float(maximum_backoff[:-1])
            if maximum_backoff_seconds <= 0 or maximum_backoff_seconds > 600:
                raise ValueError('Maximum backoff value should be between 1 and 600 seconds. Defaults to 600 seconds')
        return values


class Subscription(BaseModel):
    topic: str = Field(example='sample-topic')
    pushConfig: Optional[PushConfig]
    expirationPolicy: ExpirationPolicy
    retryPolicy: RetryPolicy
    messageRetentionDuration: str = Field(example='604800s')

    @validator('topic')
    def validate_topic(cls, topic):
        if topic == "":
            raise ValueError('Topic Name is Empty Provide Topic_Name')
        return topic

    @validator('messageRetentionDuration')
    def validate_retention(cls, v):
        retention_re = re.compile(r'^\d+[s]$')
        sec = v[:-1]
        if not retention_re.match(v):
            raise ValueError('Retention duration must be in seconds (s)')
        if int(sec) <= 0 or int(sec) > 604800:
            raise ValueError('Message retention duration must be between 1 and 604800 seconds.')
        return v


def validate_subscription_name(subscription_name):
    pattern = r'^(nac|bsaf|naaf|pltf)-(develop|preprod|qa|prod)-([a-zA-Z0-9\-]+)$'
    return bool(re.match(pattern, subscription_name))


def validate_name(topic_name):
    pattern = r'^(nac|bsaf|naaf|pltf)-(develop|preprod|qa|prod)-([a-zA-Z0-9\-]+)$'
    return bool(re.match(pattern, topic_name))


class EnvironmentEnum(str, Enum):
    DEVELOP = 'develop'
    PREPORD = 'preprod'
    QA = 'qa'
    PROD = 'prod'


class TeamNameEnum(str, Enum):
    BSAF = "bsaf"
    NAAF = "naaf"
    PLTF = "pltf"
    NAC = "nac"


class State(Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    STATE_UNSPECIFIED = "STATE_UNSPECIFIED"
    RESOURCE_ERROR = "RESOURCE_ERROR"


class SubscriptionType(Enum):
    PUSH = "PUSH"
    PULL = "PULL"

class AckSchema(BaseModel):
    ackIds: List[str]

class CeleryState(Enum):
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"
    DELETED = "DELETED"


team_names = [team.value for team in TeamNameEnum]
env_names = [app.value for app in EnvironmentEnum]
