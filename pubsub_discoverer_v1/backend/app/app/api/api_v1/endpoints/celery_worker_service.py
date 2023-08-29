import json
import ast
import logging
from app.database.session import engine
from sqlalchemy.orm import Session
from fastapi import  Depends, HTTPException, status,APIRouter
from app.schemas.celery_worker_schema import CeleryWorkerSchema
from app.api.deps import get_db, get_current_user
from app.schemas.token import TokenPayload
from app.core.config import settings
from tinaa.logger.v1.tinaa_logger import get_app_logger
from app.responses.return_api_responses import unauthorized_responses
from app.models.models import CeleryWorkerStatus
from app.schemas.subscription_schema import CeleryState

API_NAME = "Push Plugin Celery Worker Service Api"
logger_conf = [
    {
        "handler_name": settings.LOGGER_HANDLER_NAME,
        "log_level": settings.TINAA_LOG_LEVEL,
        "log_format": settings.LOGGER_FORMAT,
        "date_format": settings.LOGGER_DATE_FORMAT,
        "app_code": settings.LOGGER_APP_CODE,
        "app_name": API_NAME,
    }
]
logger = get_app_logger(log_conf=logger_conf, logger_name=API_NAME)
# from typing import List

router = APIRouter()



@router.get("/celery_worker/get_by_status/{worker_status}",  include_in_schema=False, responses={**unauthorized_responses})
async def get_celery_worker_by_status(worker_status: str, db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):
    try:
        if worker_status == 'all':
            worker_object = db.query(CeleryWorkerStatus).filter(CeleryWorkerStatus.status != CeleryState.DELETED.value).all()
        else:
            worker_object = db.query(CeleryWorkerStatus).filter(CeleryWorkerStatus.status == worker_status).all()

        worker_satet_list = []
        for worker in worker_object:
            worker_satet_list.append({
                "worker_name": worker.worker_name,
                "task_id" : worker.task_id,
                "status": worker.status,
                "subscription_name": worker.subscription_name,
                "topic_name": worker.topic_name,
                "mirror_topic_name": worker.mirror_topic_name,
                "created_at": worker.created_at
            })

        return worker_satet_list
    except Exception as e:
        logger.error(f'error occured - {e}')
        return f'{e}', status.HTTP_500_INTERNAL_SERVER_ERROR


@router.get("/celery_worker/get_by_subscription/{subscription_name}",  include_in_schema=False, responses={**unauthorized_responses})
async def get_celery_worker_by_subscription(subscription_name: str, db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):
    try:
        subscription_object = db.query(CeleryWorkerStatus).filter(CeleryWorkerStatus.subscription_name == subscription_name).first()

        if subscription_object:
            subscription = {
                    "worker_name": subscription_object.worker_name,
                    "task_id" : subscription_object.task_id,
                    "status": subscription_object.status,
                    "subscription_name": subscription_object.subscription_name,
                    "topic_name": subscription_object.topic_name,
                    "mirror_topic_name": subscription_object.mirror_topic_name,
                    "created_at": subscription_object.created_at
                }
        else:
            subscription = {}
        return subscription
    except Exception as e:
        logger.error(f'error occured - {e}')
        return f'{e}', status.HTTP_500_INTERNAL_SERVER_ERROR


@router.post("/celery_worker/create", status_code=201, include_in_schema=False, responses={**unauthorized_responses})
async def create_celery_worker(schema_info: CeleryWorkerSchema, db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):

    """
          {
          "worker_name": "string",
          "task_id": int,
          "status": "string",
          "subscription_name": "string",
          "topic_name": "string",
          "mirror_topic_name": "string",
        }
    """
    try:
        # schemas_fields = json.dumps(schema_info.schemas_fields)
        logger.info(f'/celery_worker/create service: {schema_info}')
        ex_worker_model = db.query(CeleryWorkerStatus).filter(CeleryWorkerStatus.subscription_name == schema_info.subscription_name).first()
        if ex_worker_model:
            ex_worker_model.worker_name = schema_info.worker_name
            ex_worker_model.task_id = schema_info.task_id
            ex_worker_model.status = schema_info.status
            ex_worker_model.subscription_name = schema_info.subscription_name
            ex_worker_model.topic_name = schema_info.topic_name
            ex_worker_model.mirror_topic_name = schema_info.mirror_topic_name
            db.add(ex_worker_model)
            db.commit()
        else:
            worker_model = CeleryWorkerStatus()
            worker_model.worker_name = schema_info.worker_name
            worker_model.task_id = schema_info.task_id
            worker_model.status = schema_info.status
            worker_model.subscription_name = schema_info.subscription_name
            worker_model.topic_name = schema_info.topic_name
            worker_model.mirror_topic_name = schema_info.mirror_topic_name
            db.add(worker_model)
            db.commit()

        worker_dict = {
          "worker_name": schema_info.worker_name,
          "task_id": schema_info.task_id,
          "status": schema_info.status,
          "subscription_name": schema_info.subscription_name,
          "topic_name": schema_info.topic_name,
          "mirror_topic_name": schema_info.mirror_topic_name,
        }

        logger.info(f'celery worker status: {worker_dict}')
        return worker_dict, successful_response(201)
    except Exception as e:
        logger.error(f'error occured - {str(e)}')


@router.put("/celery_worker/update/{SubscriptionName}", include_in_schema=False, responses={**unauthorized_responses})
async def updates_celery_worker(SubscriptionName: str,schema_info: CeleryWorkerSchema,db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):
    try:
        logger.info(f'/celery_worker/update service: {SubscriptionName}')
        worker_model = db.query(CeleryWorkerStatus).filter(CeleryWorkerStatus.subscription_name == SubscriptionName).first()

        if worker_model is None:
            raise http_exception()

        worker_dict = {
          "worker_name": schema_info.worker_name,
          "task_id": schema_info.task_id,
          "status": schema_info.status,
          "subscription_name": schema_info.subscription_name,
          "topic_name": schema_info.topic_name,
          "mirror_topic_name": schema_info.mirror_topic_name,
        }
        logger.info(worker_dict)

        worker_model.worker_name = schema_info.worker_name
        worker_model.task_id = schema_info.task_id
        worker_model.status = schema_info.status
        worker_model.subscription_name = schema_info.subscription_name
        worker_model.topic_name = schema_info.topic_name
        worker_model.mirror_topic_name = schema_info.mirror_topic_name
        db.add(worker_model)
        db.commit()
        return worker_dict, successful_response(200)
    except Exception as e:
        logger.error(f'error occured - {str(e)}')


@router.put("/celery_worker/status/update/{SubscriptionName}", include_in_schema=False, responses={**unauthorized_responses})
async def updates_celery_worker(SubscriptionName: str,schema_info: CeleryWorkerSchema,db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):
    try:
        logger.info(f'/celery_worker/status/update service: {SubscriptionName}')
        worker_model = db.query(CeleryWorkerStatus).filter(CeleryWorkerStatus.subscription_name == SubscriptionName).first()

        if worker_model is None:
            raise http_exception()

        worker_model.status = schema_info.status
        db.add(worker_model)
        db.commit()

        worker_dict = {
          "worker_name": worker_model.worker_name,
          "task_id": worker_model.task_id,
          "status": worker_model.status,
          "subscription_name": worker_model.subscription_name,
          "topic_name": worker_model.topic_name,
          "mirror_topic_name": worker_model.mirror_topic_name
        }
        logger.info(worker_dict)

        return worker_dict, successful_response(200)
    except Exception as e:
        logger.error(f'error occured - {str(e)}')


@router.delete("/celery_worker/delete/{SubscriptionName}", include_in_schema=False, responses={**unauthorized_responses})
async def deletes_celery_worker(SubscriptionName: str,db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):
    try:
        logger.info(f'/celery_worker/delete service: {SubscriptionName}')
        worker_model = db.query(CeleryWorkerStatus).filter(CeleryWorkerStatus.subscription_name == SubscriptionName).delete()
        if worker_model is None:
            raise http_exception()

        db.commit()

        return successful_response(204)
    except Exception as e:
        logger.error(f'error occured - {str(e)}')


def successful_response(status_code: int):
    return {
        'status': status_code,
        'transaction': 'Successful'
    }


def http_exception():
    return HTTPException(status_code=404, detail="Todo not found")
