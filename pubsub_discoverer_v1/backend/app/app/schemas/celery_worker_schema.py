from pydantic import BaseModel, Field, validator
from typing import Any, Dict, List, Optional

class CeleryWorkerSchema(BaseModel):
    worker_name: str = Field(
        None, description='Celery worker name'
    )
    task_id: Optional[str] = Field(
        None, description='Celery worker taskid'
    )
    status: str = Field(None, description='Celery worker status ex: (ACTIVE|STOPPED|DELETED)')
    subscription_name: str = Field(
        None, description='Topic Subscription name'
    )
    topic_name: Optional[str] = Field(
        None, description='topic name'
    )
    mirror_topic_name: Optional[str] = Field(
        None, description='mirror topic name'
    )


class CeleryWorkerStatusSchema(BaseModel):
    status: str = Field(None, description='Celery worker status ex: (ACTIVE|STOPPED|DELETED)')
