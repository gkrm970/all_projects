from urllib.parse import urljoin

import requests
import os
from fastapi import APIRouter, Depends, Path, HTTPException, Header
from fastapi.responses import JSONResponse
from app.models.models import SubscriptionModel, FedKafkaTopics, CeleryWorkerStatus
from app.schemas.post_schema import SubscriptionPull
from app.schemas.subscription_patch_schema import Subscription as Patch_Subscription
from app.schemas.subscription_schema import Subscription, validate_subscription_name, \
    env_names, team_names, \
    SubscriptionType, CeleryState
from app.responses.return_api_responses import create_response, update_response, get_response, get_all_response, \
    Successful_Response, unauthorized_responses, Unexpected_error_responses, Subscription_Response, \
    bad_request_responses, Resource_conflict_responses, Return_Successful_Response, no_subscription_found, \
    delete_Response, delete_no_subscription_found, Subscription_Post_Response

from sqlalchemy.orm import Session
from sqlalchemy import exc
from app.api.deps import get_db, get_current_user
from app.schemas.token import TokenPayload
from app.core.config import settings
from tinaa.logger.v1.tinaa_logger import get_app_logger

API_NAME = "Subscription API"
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

api_router = APIRouter()
TINAA_KAFKA_LIST = os.getenv("TINAA_KAFKA_LIST", "")


@api_router.get("/subscriptions",
                responses={**Successful_Response, **unauthorized_responses, **Unexpected_error_responses})
def Lists_all_the_subscriptions(db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):
    subscription_list = db.query(SubscriptionModel).all()
    try:
        if subscription_list:
            res = get_all_response(subscription_list)

        else:
            error = {
                "error": {
                    "code": 404,
                    "message": "Item not found",
                    "status": "error",
                    "details": {
                        "reason": "Item not found",
                        "type": "invalid_parameter"
                    }
                }
            }
            return JSONResponse(content=error, status_code=404)
        return res

    except exc.SQLAlchemyError as e:
        logger.info(f"Data Base Connection Error:{e}")
        logger.debug(e)
        error = {
            "error": {
                "code": 500,
                "message": "Error occurred while connecting to database",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)
    except Exception as e:
        error = {
                "error": {
                    "code": 500,
                    "message": "Error Occurred while connecting to Database",
                    "status": "string",
                    "details": {
                        "reason": str(e),
                        "type": "Unexpected error"
                    }
                }
            }
        return JSONResponse(content=error, status_code=500)


@api_router.put("/subscriptions/{SubscriptionName}",
                responses={**Subscription_Response, **bad_request_responses, **unauthorized_responses,
                           **Resource_conflict_responses,
                           **Unexpected_error_responses})
def Creates_a_subscription(input_data: Subscription, SubscriptionName: str = Path(..., description="Name of the "
                                                                                                   "subscription. "
                                                                                                   "Follow the "
                                                                                                   "naming "
                                                                                                   "convention "
                                                                                                   "<app-team-name"
                                                                                                   ">-<environment"
                                                                                                   "-name>-<event"
                                                                                                   "-name>. "
                                                                                                   "Allowed "
                                                                                                   "environment "
                                                                                                   "names "
                                                                                                   "are develop, "
                                                                                                   "preprod, "
                                                                                                   "qa & prod. "
                                                                                                   "App "
                                                                                                   "team name "
                                                                                                   "examples -> "
                                                                                                   "bsaf, "
                                                                                                   "naaf & pltf. "
                                                                                                   "The "
                                                                                                   "three "
                                                                                                   "examples "
                                                                                                   "are for "
                                                                                                   "application teams "
                                                                                                   "TINAA Business "
                                                                                                   "Services "
                                                                                                   "Automation "
                                                                                                   "Framework, "
                                                                                                   "TINAA Network "
                                                                                                   "Applications "
                                                                                                   "Automation "
                                                                                                   "Framework & TINAA "
                                                                                                   "Platform team. If "
                                                                                                   "you do no belong "
                                                                                                   "to any of the "
                                                                                                   "three within "
                                                                                                   "TINAA, "
                                                                                                   "Please contact "
                                                                                                   "dlSDNPlatformSupportDistributionList@telus.com."),

                           db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):
    res = validate_subscription_name(SubscriptionName)
    dict_data = input_data.__dict__
    topic_name = dict_data.get('topic')
    topic_obj = db.query(FedKafkaTopics).filter(FedKafkaTopics.kafka_topic_name == topic_name).first()
    sub_obj = db.query(SubscriptionModel).filter(SubscriptionModel.name == SubscriptionName).first()
    bus_name = topic_obj.kafka_bus_name
    logger.info(f'Checking SubscriptionName Object Existed in DB {sub_obj}')
    try:
        if res:
            logger.info(f'Valid SubscriptionName follow the naming convention: {res}')
            if sub_obj:
                logger.info(f'Valid SubscriptionName Object {sub_obj} is not None')
                error = {
                    "error": {
                        "code": 409,
                        "message": f"SubscriptionName: {SubscriptionName} Already Existed In DB",
                        "status": "Can you Give Another SubscriptionName",
                        "details": {
                            "reason": "SubscriptionName Already Existed In DB",
                            "type": "invalid_parameter"
                        }
                    }
                }
                logger.info(f'SubscriptionName: {SubscriptionName} Already Existed In DB')
                return JSONResponse(content=error, status_code=409)
            if not topic_obj:
                logger.info(f'TopicName Object is not exist in Topics Table')
                error = {
                    "error": {
                        "code": 404,
                        "message": f"TopicName {topic_name} is not exist in Topics Table",
                        "status": "Give TopicName from Topics table",
                        "details": {
                            "reason": f"TopicName {topic_name} is not exist in Topics Table",
                            "type": "invalid_parameter"
                        }
                    }
                }
                return JSONResponse(content=error, status_code=404)

            else:
                logger.info(f'Sub_obj Is None So Checking Topic Name Association Already Existed Same Values in DB')
                sub_obj = db.query(SubscriptionModel).filter(SubscriptionModel.topic == dict_data.get('topic')).first()
                logger.info(f'sub_obj_topic: {sub_obj}')
                if sub_obj:
                    logger.info(f'Checked sub_obj is not None:{sub_obj}')
                    logger.info(f'Start the Checking Webhook Url Same or Not ')
                    if sub_obj.pushEndpoint == dict_data.get(
                            'pushConfig').pushEndpoint:
                        logger.info(f'Webhook Url already Existed Raising Error Url Already Existed In DB')
                        error = {
                            "error": {
                                "code": 404,
                                "message": f"This {dict_data.get('pushConfig').pushEndpoint} Webhook Url Already Existed"
                                           f"in DB",
                                "status": "Can you Use Another Webhook Url",
                                "details": {
                                    "reason": "Webhook Url Already Existed In DB",
                                    "type": "invalid_parameter"
                                }
                            }
                        }
                        logger.info(f'sub_obj_topic: {sub_obj}')
                        return JSONResponse(content=error, status_code=404)
                    else:
                        logger.info(f'Webhook Url Not Same DB Operations Start')
                        subscribe_model = SubscriptionModel()
                        subscribe_model.name = SubscriptionName
                        subscribe_model.topic = dict_data['topic']
                        if dict_data.get('pushConfig'):
                            if dict_data.get('pushConfig').pushEndpoint:
                                logger.info(dict_data.get('pushConfig').pushEndpoint)
                                subscribe_model.subscription_type = SubscriptionType.PUSH
                                subscribe_model.pushEndpoint = dict_data.get('pushConfig').pushEndpoint
                            else:
                                subscribe_model.subscription_type = SubscriptionType.PULL
                        else:
                            subscribe_model.subscription_type = SubscriptionType.PULL
                        subscribe_model.ttl = dict_data['expirationPolicy'].ttl
                        subscribe_model.minimumBackoff = dict_data['retryPolicy'].minimumBackoff
                        subscribe_model.maximumBackoff = dict_data['retryPolicy'].maximumBackoff
                        subscribe_model.messageRetentionDuration = dict_data['messageRetentionDuration']
                        db.add(subscribe_model)
                        db.commit()

                        if TINAA_KAFKA_LIST not in bus_name:
                            logger.info(
                                f" Bus name: {bus_name} is not TINAA kafka, proceeding for mirroring"
                            )
                            mirror_kafka_url = os.getenv('MIRROR_MAKER_URL') + "/start/mirror-maker/" + str(
                                SubscriptionName)
                            headers = {
                                'Content-Type': 'application/json'
                            }
                            logger.info(f'mirror_kafka_url.........{mirror_kafka_url}')
                            mirror_maker_response = requests.post(mirror_kafka_url, headers=headers, json={})
                            logger.info(f'response_status :{mirror_maker_response.status_code}')
                            logger.info(f'mirror_kafka_url.........{mirror_kafka_url}')
                            if mirror_maker_response.status_code == 200:
                                logger.info(f'Topic mirroring is created for topic - {dict_data["topic"]} successfully')
                            else:
                                logger.info(f'Error while create mirroring for topic - {dict_data["topic"]}')
                                logger.info('proceeding to delete the subscription record from db ')
                                db.delete(subscribe_model)
                                db.commit()
                                logger.info("subscription record has been deleted from db ,hence there is no mirroring")
                                error = {
                                    "error": {
                                        "code": 500,
                                        "message": f'Error while create mirroring for topic - {dict_data["topic"]}',
                                        "status": "Failed",
                                        "details": {
                                            "reason": "Refer pubsub_discoverer-mirrormaker_endpoints-1 container logs",
                                            "type": "Unexpected error"
                                        }
                                    }
                                }
                                return JSONResponse(content=error, status_code=500)
                        if dict_data.get('pushConfig'):
                            logger.info('Subscription Type is : PUSH, Invoking push plugin service')
                            push_service_url = os.getenv('PUSH_SERVICE_URL') + '/start/push-service/' + str(
                                SubscriptionName)
                            headers = {
                                'Content-Type': 'application/json'
                            }
                            logger.info(f'push_service_url - {push_service_url}')
                            push_scheduler_response = requests.post(push_service_url, headers=headers, json={})
                            logger.info(f'response_status :{push_scheduler_response.status_code}')
                            if push_scheduler_response.status_code == 200:
                                logger.info(f'{SubscriptionName} creation in DB, Topic Mirroring and push service '
                                            f'schedule is successful')
                                response = create_response(subscribe_model)
                            else:
                                if TINAA_KAFKA_LIST not in bus_name:
                                    logger.info(
                                        f'topic :{topic_name} is not part of tinna kafka hence proceeding further to delete the mirrored topic')
                                    mirror_kafka_url = os.getenv('MIRROR_MAKER_URL') + "/delete/mirror-maker/" + str(
                                        topic_name)
                                    mirror_maker_response = requests.post(mirror_kafka_url, headers=headers, json={})
                                    if mirror_maker_response.status_code == 200:
                                        logger.info("mirrored topic has been deleted")
                                    else:
                                        logger.error("Error occurred while deleting mirrored topic ")
                                logger.info("Procceding further to delete the subscription record from db entry")
                                db.delete(subscribe_model)
                                db.commit()
                                logger.info("subscription record has been deleted from db ")
                                error = {
                                    "error": {
                                        "code": 500,
                                        "message": f'Error while creating push plugin service,for topic - {dict_data["topic"]}',
                                        "status": "Failed",
                                        "details": {
                                            "reason": "Refer pubsub_discoverer-pubsub_discoverer-push-plugin-1 container logs",
                                            "type": "Unexpected error"
                                        }
                                    }
                                }
                                return JSONResponse(content=error, status_code=500)

                        else:
                            logger.info("Subscription Type is : PULL, Hence skipping Invoking push plugin service")


                        # logger.info("Data-Stored-In-DataBase-Successfully")
                        # response = create_response(subscribe_model)
                else:
                    logger.info(f'sub_obj {sub_obj} is None So DB Operations Start')
                    subscribe_model = SubscriptionModel()
                    subscribe_model.name = SubscriptionName
                    subscribe_model.topic = dict_data['topic']
                    if dict_data.get('pushConfig'):
                        if dict_data.get('pushConfig').pushEndpoint:
                            subscribe_model.subscription_type = SubscriptionType.PUSH
                            subscribe_model.pushEndpoint = dict_data.get('pushConfig').pushEndpoint
                        else:
                            subscribe_model.subscription_type = SubscriptionType.PULL
                    else:
                        subscribe_model.subscription_type = SubscriptionType.PULL
                    subscribe_model.ttl = dict_data['expirationPolicy'].ttl
                    subscribe_model.minimumBackoff = dict_data['retryPolicy'].minimumBackoff
                    subscribe_model.maximumBackoff = dict_data['retryPolicy'].maximumBackoff
                    subscribe_model.messageRetentionDuration = dict_data['messageRetentionDuration']
                    db.add(subscribe_model)
                    db.commit()

                    if TINAA_KAFKA_LIST not in bus_name:
                        logger.info(
                            f" Bus name: {bus_name} is not TINAA kafka, proceeding for mirroring"
                        )
                        mirror_kafka_url = os.getenv('MIRROR_MAKER_URL') + "/start/mirror-maker/" + str(
                            SubscriptionName)
                        headers = {
                            'Content-Type': 'application/json'
                        }
                        logger.info(f'mirror_kafka_url.........{mirror_kafka_url}')
                        mirror_maker_response = requests.post(mirror_kafka_url, headers=headers, json={})
                        logger.info(f'response_status :{mirror_maker_response.status_code}')
                        logger.info(f'mirror_kafka_url.........{mirror_kafka_url}')
                        if mirror_maker_response.status_code == 200:
                            logger.info(f'Topic mirroring is created for topic - {dict_data["topic"]} successfully')
                        else:
                            logger.error(f'Error while create mirroring for topic - {dict_data["topic"]}')
                            logger.info('proceeding to delete the subscription record from db ')
                            db.delete(subscribe_model)
                            db.commit()
                            logger.info("subscription record has been deleted from db ,hence there is no mirroring")
                            error = {
                                "error": {
                                    "code": 500,
                                    "message": f'Error while create mirroring for topic - {dict_data["topic"]}',
                                    "status": "Failed",
                                    "details": {
                                        "reason": "Refer pubsub_discoverer-mirrormaker_endpoints-1 container logs",
                                        "type": "Unexpected error"
                                    }
                                }
                            }
                            return JSONResponse(content=error, status_code=500)
                    if dict_data.get('pushConfig'):
                        logger.info(f'Subscription Type is : PUSH, Invoking push plugin service')
                        push_service_url = os.getenv('PUSH_SERVICE_URL') + '/start/push-service/' + str(
                            SubscriptionName)
                        headers = {
                            'Content-Type': 'application/json'
                        }
                        logger.info(f'push_service_url - {push_service_url}')
                        push_scheduler_response = requests.post(push_service_url, headers=headers, json={})
                        logger.info(f'response_status :{push_scheduler_response.status_code}')
                        if push_scheduler_response.status_code == 200:
                            logger.info(f'{SubscriptionName} creation in DB, Topic Mirroring and push service'
                                        f'schedule is successful')
                            response = create_response(subscribe_model)
                        else:
                            if TINAA_KAFKA_LIST not in bus_name:
                                logger.info(f'topic :{topic_name} is not part of tinna kafka hence proceeding further to delete db entry')
                                mirror_kafka_url = os.getenv('MIRROR_MAKER_URL') + "/delete/mirror-maker/" + str(
                                    topic_name)
                                mirror_maker_response = requests.post(mirror_kafka_url, headers=headers, json={})
                                if mirror_maker_response.status_code == 200:
                                    logger.info("mirrored topic has been deleted successfully")
                                else:
                                    logger.error("Error occurred while deleting mirrored topic ")
                            logger.info(
                                "Proceeding further to delete the subscription record from db entry")
                            db.delete(subscribe_model)
                            db.commit()
                            logger.info("subscription record has been deleted from db ")
                            error = {
                                "error": {
                                    "code": 500,
                                    "message": f'Error while creating push plugin service,for topic - {dict_data["topic"]}',
                                    "status": "Failed",
                                    "details": {
                                        "reason": "Refer pubsub_discoverer-pubsub_discoverer-push-plugin-1 container logs",
                                        "type": "Unexpected error"
                                    }
                                }
                            }
                            return JSONResponse(content=error, status_code=500)
                    else:
                        logger.info("Subscription Type is : PULL, Hence skipping Invoking push plugin service")
        else:
            error = {
                "error": {
                    "code": 422,
                    "message": "Subscription name must follow the naming convention: "
                               "<app-team-name>-<environment-name>-<subscription-name>",
                    "example": 'naaf-develop-MEM-NOKIA-Telemetry',
                    "status": "error",
                    "details": {
                        "reason": "Name of the topic. Follow the naming convention "
                                  "<app-team-name>-<environment-name>-<event-name>. Allowed "
                                  f'App-Team-Names Ex: {team_names}, Environment-Names Ex: {env_names}',
                        "type": "invalid_parameter"
                    }
                }
            }
            return JSONResponse(content=error, status_code=422)
        return response
    except Exception as e:
        error = {
            "error": {
                "code": 500,
                "message": "Error Occurred While creating a subscription",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)


# End the post method

@api_router.patch('/subscriptions/{SubscriptionName}',
                  responses={**Subscription_Response, **bad_request_responses, **unauthorized_responses,
                             **Unexpected_error_responses})
def Updates_a_subscription(input_data: Patch_Subscription, SubscriptionName: str = Path(..., description='Name of the '
                                                                                                         'subscription. Follow the naming convention <app-team-name>-<environment-name>-<subscription-name>. '
                                                                                                         'Allowed '
                                                                                                         'environment '
                                                                                                         'names are '
                                                                                                         'develop, '
                                                                                                         'preprod, '
                                                                                                         'qa & prod. '
                                                                                                         'App team '
                                                                                                         'name '
                                                                                                         'examples -> '
                                                                                                         'bsaf, '
                                                                                                         'naaf & '
                                                                                                         'pltf. The '
                                                                                                         'three '
                                                                                                         'examples '
                                                                                                         'are for '
                                                                                                         'application '
                                                                                                         'teams TINAA '
                                                                                                         'Business '
                                                                                                         'Services '
                                                                                                         'Automation '
                                                                                                         'Framework, '
                                                                                                         'TINAA '
                                                                                                         'Network '
                                                                                                         'Applications Automation Framework & TINAA Platform team.'
                                                                                                         ' If you do no belong to any of the three within TINAA, '
                                                                                                         'Please contact dlSDNPlatformSupportDistributionList@telus.com.'),
                           db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):
    sub_obj_model = db.query(SubscriptionModel).filter(SubscriptionModel.name == SubscriptionName).first()

    dict_data = input_data.__dict__
    topic_name = dict_data.get('topic')
    topic_obj = db.query(FedKafkaTopics).filter(FedKafkaTopics.kafka_topic_name == topic_name).first()
    sub_obj = db.query(SubscriptionModel).filter(SubscriptionModel.name == SubscriptionName).first()
    if topic_obj is None:
        logger.info(f'This topic {topic_name} name  does not existed in topics table')
        raise HTTPException(status_code=409, detail=f'This {topic_name} name does not exist in Topics DB')
    try:
        res = validate_subscription_name(SubscriptionName)
        if res:
            if sub_obj_model:
                dict_data = input_data.__dict__
                logger.info("Inside if Condition")
                data = dict_data.get('pushConfig').__dict__
                d = data.get('oidcToken').__dict__
                a = dict_data.get('labels').__dict__
                key = a.get('key')
                value = a.get('value')
                service_email = d.get('serviceAccountEmail')
                audience = d.get('audience')
                sub_obj_model.name = SubscriptionName
                sub_obj_model.topic = dict_data['topic']
                sub_obj_model.subscriptionType = dict_data['subscriptionType']
                sub_obj_model.filter = dict_data['filter']
                sub_obj_model.timeAggregation = dict_data['aggregationSettings'].timeAggregation
                sub_obj_model.spaceAggregation = dict_data['aggregationSettings'].spaceAggregation
                sub_obj_model.method = dict_data['deliverySettings'].method
                sub_obj_model.format = dict_data['deliverySettings'].format
                sub_obj_model.url = dict_data['deliverySettings'].url
                sub_obj_model.fieldNameChanges = dict_data['dataTranformationSettings'].fieldNameChanges
                sub_obj_model.fieldsReorder = dict_data['dataTranformationSettings'].fieldsReorder
                sub_obj_model.fieldsToRemove = dict_data['dataTranformationSettings'].fieldsToRemove
                # sub_obj_model.pushEndpoint = dict_data['pushConfig'].pushEndpoint
                if dict_data.get('pushConfig'):
                    if dict_data.get('pushConfig').pushEndpoint:
                        logger.info(dict_data.get('pushConfig').pushEndpoint)
                        sub_obj_model.subscription_type = SubscriptionType.PUSH
                        sub_obj_model.pushEndpoint = dict_data.get('pushConfig').pushEndpoint
                    else:
                        sub_obj_model.subscription_type = SubscriptionType.PULL
                else:
                    sub_obj_model.subscription_type = SubscriptionType.PULL
                sub_obj_model.serviceAccountEmail = service_email
                sub_obj_model.audience = audience
                sub_obj_model.ackDeadlineSeconds = dict_data['ackDeadlineSeconds']
                sub_obj_model.retainAckedMessages = dict_data['retainAckedMessages']
                sub_obj_model.messageRetentionDuration = dict_data['messageRetentionDuration']
                sub_obj_model.key = key
                sub_obj_model.value = value
                sub_obj_model.enableMessageOrdering = dict_data['enableMessageOrdering']
                sub_obj_model.ttl = dict_data['expirationPolicy'].ttl
                sub_obj_model.deadLetterTopic = dict_data['deadLetterPolicy'].deadLetterTopic
                sub_obj_model.maxDeliveryAttempts = dict_data['deadLetterPolicy'].maxDeliveryAttempts
                sub_obj_model.minimumBackoff = dict_data['retryPolicy'].minimumBackoff
                sub_obj_model.maximumBackoff = dict_data['retryPolicy'].maximumBackoff
                sub_obj_model.detached = dict_data['detached']
                sub_obj_model.enableExactlyOnceDelivery = dict_data['enableExactlyOnceDelivery']
                db.add(sub_obj_model)
                db.commit()
                res = update_response(sub_obj_model)

            else:
                error = {
                    "error": {
                        "code": 404,
                        "message": "Item not found",
                        "status": "error",
                        "details": {
                            "reason": "Item not found",
                            "type": "invalid_parameter"
                        }
                    }
                }
                logger.info('No Record Found In DB')
                return JSONResponse(content=error, status_code=404)
        else:
            error = {
                "error": {
                    "code": 422,
                    "message": "Subscription name must follow the naming convention: "
                               "<app-team-name>-<environment-name>-<subscription-name>",
                    "example": 'naaf-develop-MEM-NOKIA-Telemetry',
                    "status": "error",
                    "details": {
                        "reason": "Name of the topic. Follow the naming convention "
                                  "<app-team-name>-<environment-name>-<event-name>. Allowed "
                                  f'App-Team-Names Ex: {team_names}, Environment-Names Ex: {env_names}',
                        "type": "invalid_parameter"
                    }
                }
            }
            return JSONResponse(content=error, status_code=422)

        return res
    except exc.SQLAlchemyError as e:
        logger.info(f"Data Base Connection Error:{e}")
        logger.debug(e)
        error = {
            "error": {
                "code": 500,
                "message": "Error occurred while connecting to database",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)

    except Exception as e:
        error = {
            "error": {
                "code": 500,
                "message": "Error Occurred while updating a subscription",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)

@api_router.get('/subscriptions/{SubscriptionName}',
                responses={**Return_Successful_Response, **unauthorized_responses, **no_subscription_found,
                           **Unexpected_error_responses})
def Returns_a_subscription(SubscriptionName: str = Path(..., description='Name of the subscription. Follow the naming '
                                                                         'convention '
                                                                         '<app-team-name>-<environment-name'
                                                                         '>-<subscription-name>. Allowed environment '
                                                                         'names are develop, preprod, qa & prod. App '
                                                                         'team name examples -> bsaf, naaf & pltf. '
                                                                         'The three examples are for application '
                                                                         'teams TINAA Business Services Automation '
                                                                         'Framework, TINAA Network Applications '
                                                                         'Automation Framework & TINAA Platform team. '
                                                                         'If you do no belong to any of the three '
                                                                         'within TINAA, Please contact '
                                                                         'dlSDNPlatformSupportDistributionList@telus'
                                                                         '.com.'), db: Session = Depends(get_db),
                           current_user: TokenPayload = Depends(get_current_user)):
    sub_model_obj = db.query(SubscriptionModel).filter(SubscriptionModel.name == SubscriptionName).first()
    res = validate_subscription_name(SubscriptionName)

    try:
        if res:
            if sub_model_obj:
                logger.info('Checking The DB object Existed or Not')
                response = get_response(sub_model_obj)
            else:

                error = {
                    "error": {
                        "code": 404,
                        "message": "Item not found",
                        "status": "error",
                        "details": {
                            "reason": "Item not found",
                            "type": "invalid_parameter"
                        }
                    }
                }
                logger.info('No Object Existed in DB')
                return JSONResponse(content=error, status_code=404)
        else:
            error = {
                "error": {
                    "code": 422,
                    "message": "Subscription name must follow the naming convention: "
                               "<app-team-name>-<environment-name>-<subscription-name>",
                    "example": 'naaf-develop-MEM-NOKIA-Telemetry',
                    "status": "error",
                    "details": {
                        "reason": "Name of the topic. Follow the naming convention "
                                  "<app-team-name>-<environment-name>-<event-name>. Allowed "
                                  f'App-Team-Names Ex: {team_names}, Environment-Names Ex: {env_names}',
                        "type": "invalid_parameter"
                    }
                }
            }
            return JSONResponse(content=error, status_code=422)
        return response
    except exc.SQLAlchemyError as e:
        logger.info(f"Data Base Connection Error:{e}")
        logger.debug(e)
        error = {
            "error": {
                "code": 500,
                "message": "Error occurred while connecting to database",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)
    except Exception as e:
        error = {
                "error": {
                    "code": 500,
                    "message": f"Error Occurred in {str(e)}",
                    "status": "string",
                    "details": {
                        "reason": "Error",
                        "type": "Unexpected error"
                    }
                }
            }
        return JSONResponse(content=error, status_code=500)

@api_router.delete('/subscriptions/{SubscriptionName}',
                   responses={**delete_Response, **delete_no_subscription_found, **unauthorized_responses,
                              **Unexpected_error_responses})
def Deregisters_a_subscription(SubscriptionName: str = Path(..., description='Name of the subscription. Follow the '
                                                                             'naming convention '
                                                                             '<app-team-name>-<environment-name'
                                                                             '>-<subscription-name>. Allowed '
                                                                             'environment names are develop, preprod, '
                                                                             'qa & prod. App team name examples -> '
                                                                             'bsaf, naaf & pltf. The three examples are '
                                                                             'for application teams TINAA Business '
                                                                             'Services Automation Framework, '
                                                                             'TINAA Network Applications Automation '
                                                                             'Framework & TINAA Platform team. If you '
                                                                             'do no belong to any of the three within '
                                                                             'TINAA, Please contact '
                                                                             'dlSDNPlatformSupportDistributionList'
                                                                             '@telus.com.'),
                               db: Session = Depends(get_db),
                               current_user: TokenPayload = Depends(get_current_user),
                               authorization: str = Header(None),
    ):

    if not validate_subscription_name(SubscriptionName):
        error = {
            "error": {
                "code": 422,
                "message": "Subscription name must follow the naming convention: "
                           "<api-team-name>-<environment-name>-<subscription-name>",
                "example": 'naaf-develop-MEM-NOKIA-Telemetry',
                "status": "error",
                "details": {
                    "reason": "Name of the topic. Follow the naming convention "
                              "<api-team-name>-<environment-name>-<event-name>. Allowed "
                              f'App-Team-Names Ex: {team_names}, Environment-Names Ex: {env_names}',
                    "type": "invalid_parameter"
                }
            }
        }
        return JSONResponse(content=error, status_code=422)

    sub_model = db.query(SubscriptionModel).filter(SubscriptionModel.name == SubscriptionName).first()
    try:
        if not sub_model:
            error = {
                "error": {
                    "code": 404,
                    "message": f'{SubscriptionName} is in an active state or is not a push subscription or '
                               f'SubscriptionName is not found.',
                    "status": "error",
                    "details": {
                        "reason": f'{SubscriptionName} is in an active state or is not a push subscription or '
                                  f'SubscriptionName is not found.',
                        "type": "Invalid State"
                    }
                }
            }
            return JSONResponse(content=error, status_code=404)

        worker = db.query(CeleryWorkerStatus).filter(
            CeleryWorkerStatus.subscription_name == SubscriptionName
        ).first()
        logger.info(f'worker status object: {worker}')
        if worker:
            worker.status = CeleryState.DELETED.value

            db.add(worker)
            db.commit()

            worker = db.query(CeleryWorkerStatus).filter(
                CeleryWorkerStatus.subscription_name == SubscriptionName, CeleryWorkerStatus.status == CeleryState.DELETED.value
            ).first()

            if worker:
                logger.info(
                    f"Worker state for subscription {SubscriptionName} "
                    f"set to {CeleryState.DELETED.value}"
                )
            else:
                logger.error(
                    f"Worker state for subscription {SubscriptionName} "
                    f"un able set to {CeleryState.DELETED.value}"
                )
        else:
            logger.info(
                f"No worker associated with subscription "
                f"{SubscriptionName} was found in the DB"
            )

        kafka_topic = db.query(FedKafkaTopics).filter(
            FedKafkaTopics.kafka_topic_name == sub_model.topic
        ).first()

        if kafka_topic is None:
            raise Exception(
                f"Kafka topic {sub_model.topic} does not exist"
            )

        bus_name = kafka_topic.kafka_bus_name

        if TINAA_KAFKA_LIST not in bus_name:
            logger.info(
                f" Bus name: {bus_name} is not TINAA kafka, proceeding for mirroring topic deletion"
            )
            mirror_maker_delete_url = urljoin(
                os.getenv('MIRROR_MAKER_URL'),
                f"/delete/mirror-maker/{kafka_topic.kafka_topic_name}"
            )

            headers = {
                'Content-Type': 'application/json'
            }

            logger.info(f'Mirror maker delete URL: {mirror_maker_delete_url}')
            mirror_maker_response = requests.delete(
                mirror_maker_delete_url,
                headers=headers
            )

            logger.info(
                f'Response_status: {mirror_maker_response.status_code}'
            )

            if mirror_maker_response.status_code == 200:
                logger.info('Mirrored topic is successfully deleted')

                logger.info(
                    'Proceeding to delete the subscription record from DB'
                )

                db.delete(sub_model)
                db.commit()

            else:
                logger.info(
                    f'Error while deleting mirrored topic: '
                    f'{kafka_topic.kafka_topic_name}'
                )

                raise Exception(
                    "The mirror maker topic deletion was not successsful, "
                    "the subscription could not be deleted"
                )
        else:

            logger.info(
                    'Proceeding to delete the subscription record from DB'
                )

            db.delete(sub_model)
            db.commit()

            logger.info('Deleted Record Successfully')

        return {}

    except exc.SQLAlchemyError as e:
        logger.info(f"Data Base Connection Error:{e}")
        logger.debug(e)
        error = {
            "error": {
                "code": 500,
                "message": "Error occurred while connecting to database",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)

    except Exception as e:
        logger.info("Failed to delete subscription {}: {}".format(SubscriptionName, e))
        error = {
                "error": {
                    "code": 500,
                    "message": "Failed to delete subscription {}: {}".format(SubscriptionName, e),
                    "status": "string",
                    "details": {
                        "reason": "Error",
                        "type": "Unexpected error"
                    }
                }
            }
        return JSONResponse(content=error, status_code=500)
