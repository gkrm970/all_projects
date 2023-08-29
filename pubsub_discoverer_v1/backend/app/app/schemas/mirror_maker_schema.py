from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class MirrorMakerCreate(BaseModel):
    process_id: int
    subscription_name: str = Field(description='Name of the subscription', title='Subscription name')
    source_topic_name: str = Field(description='Name of the Source_topic', title='Source topic name')
    destination_topic_name: str = Field(description='Name of the Destination_topic', title='Destination topic name')
    fed_kafka_bus_name: str = Field(description='Name of the fed_kafka_bus', title='Fed kafka bus name')
    auth_type: str = Field(description='Name of the Auth_type', title='Auth type')


class MirrorMakerCreateDisplay(BaseModel):
    # Define the fields to expose to users
    subscription_name: str = Field(description='Name of the subscription', title='Subscription name')
    source_topic_name: str = Field(description='Name of the Source_topic', title='Source topic name')
    destination_topic_name: str = Field(description='Name of the Destination_topic', title='Destination topic name')
    fed_kafka_bus_name: str = Field(description='Name of the fed_kafka_bus', title='Fed kafka bus name')
    auth_type: str = Field(description='Name of the Auth_type', title='Auth type')

    class Config:
        orm_mode = True


class MirrorMakerUpdate(BaseModel):
    process_id: int
    subscription_name: Optional[str] = Field(description='Name of the subscription', title='Subscription name')
    source_topic_name: Optional[str] = Field(description='Name of the Source_topic', title='Source topic name')
    destination_topic_name: Optional[str] = Field(description='Name of the Destination_topic',
                                                  title='Destination topic name')
    fed_kafka_bus_name: Optional[str] = Field(description='Name of the fed_kafka_bus', title='Fed kafka bus name')
    auth_type: Optional[str] = Field(description='Name of the Auth_type', title='Auth type')
