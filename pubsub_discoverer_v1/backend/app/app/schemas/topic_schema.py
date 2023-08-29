import re
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field, validator


class Labels(BaseModel):
    key: Optional[str]
    value: Optional[str]


class Encoding(str, Enum):
    JSON : str = "JSON"
    BINARY: str = "BINARY"
    EMPTY: None = ''


class SchemaName(BaseModel):
    #__root__: str = Field(..., description='Name of the schema', example='test')
    schema_name: str = Field(..., description='Name of the schema', example='test')


class SchemaSettings(BaseModel):
    schema_: str = Field(example='test',alias ='schema', description='Name of the schema', title='SchemaName')
    encoding: Encoding = Field(example='JSON')

    @validator('schema_')
    def validate_schema(cls, v):
        if not v:
            raise ValueError("Please provide the valid schema")
        return v


class SchemaSettingsResponse(BaseModel):
    schema_: Optional[str] = Field(example='test',alias ='schema', description='Name of the schema', title='SchemaName')
    encoding: Optional[Encoding] = Field(example='JSON')
    firstRevisionId: Optional[str]
    lastRevisionId: Optional[str]


class RetentionSettings(BaseModel):
    liveDateRetentionDuration: Optional[str] = Field(description='Reserved for future use')
    historicalDataRetentionDuration: Optional[str] = Field(description='Reserved for future use')


class SupportedSubscriptionTypes(str, Enum):
    LIVE: str = "LIVE"
    HISTORICAL: str = "HISTORICAL"
    EMPTY: None = ''


# class KafkaTopicSchema(BaseModel):
#     labels: Optional[Labels]
#     schemaSettings: SchemaSettings
#     messageRetentionDuration: Optional[str] = Field(example='10.5s')
#     retentionSettings: Optional[RetentionSettings]
#     supportedSubscriptionTypes: Optional[SupportedSubscriptionTypes] = Field(description='Reserved for future use')
#
#     class Config:
#         use_enum_values = True  # <--


class KafkaTopicResponseSchema(BaseModel):
    schemaSettings: Optional[SchemaSettingsResponse]
    messageRetentionDuration: Optional[str] = Field(example='10.5s')

    class Config:
        schema_extra = {
            "example": {
                "messageRetentionDuration": '600s',
               }
           }

        exclude = {"schemaSettings"}


class TopicList(BaseModel):
    topics: Optional[List[KafkaTopicResponseSchema]]


class KafkaTopicRequestSchema(BaseModel):
    kafka_bus_name: Optional[str] = Field(example='pltf-msgbus.develop.ocp01.toll6.tinaa.tlabs.ca')
    schemaSettings: Optional[SchemaSettings]
    messageRetentionDuration: Optional[str] = Field(example='600s')

    class Config:
        schema_extra = {
            "example": {
                "schemaSettings": {
                    "schema": "test",
                    "encoding": "JSON"
                },
                "messageRetentionDuration": '600s',
               }
           }

        exclude = {"kafka_bus_name"}

    @validator('messageRetentionDuration')
    def validate_retention(cls, v):
        # Match for either digits only and a 's' unit at the end,
        # or a number of digits, with any number of decimal places plus 's' unit
        retention_re = re.compile(r'^(\d+|\d+\.\d+)s$')
        sec = v[:-1]
        if not retention_re.match(v):
            raise ValueError('Retention duration must be in seconds (s)')
        if float(sec) > 604800 or float(sec) <= 0:
            raise ValueError('Message retention duration more than 7 or less than 0 days and duration in seconds its not allowed')

        return v
