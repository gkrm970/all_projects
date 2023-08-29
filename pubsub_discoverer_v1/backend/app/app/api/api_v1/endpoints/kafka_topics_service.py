import logging
import jwt
import os
import concurrent.futures
#from app.database.session import engine,Base
#Base.metadata.create_all(bind=engine)
from fastapi import APIRouter, Depends, HTTPException, Path, Header
from app.models.models import FedKafkaBuses,SchemasInfo
from app.models.models import TopicSchemaAssociation as SchemaTopicAssociation
from app.models.models import FedKafkaTopics
from sqlalchemy.orm import Session
from sqlalchemy import exc
from app.api.deps import get_db, get_current_user
from app.schemas.token import TokenPayload
from app.schemas.topic_schema import KafkaTopicRequestSchema
from app.schemas.entity import DiscoveryModuleInternalTopicSchemaAssociation as TopicSchemaAssociationPydantic

from app.responses.return_api_responses import unauthorized_responses, Unexpected_error_responses,bad_request_responses, publish_success_Response, Resource_conflict_responses
from app.responses.schema_topic_return_response import Topic_Put_Successful_Response, topic_put_create_response, get_all_topic_response, Get_all_topic_Successful_Response
from app.schemas.publish_message_schema import Messages, MyResponse
from app.schemas.subscription_schema import team_names, env_names, validate_name
from app.crud.publish_message_service import PublishMessage
from app.api.utils.utils import is_user_admin
from starlette.responses import JSONResponse
from app.core.config import settings
from tinaa.logger.v1.tinaa_logger import get_app_logger
from confluent_kafka.admin import AdminClient, ConfigResource
from confluent_kafka.admin import NewTopic
import functools
import time
import requests
import jwt
from confluent_kafka import Consumer


def tinaa_config():
    def _get_token(config_str):
        """Note here value of config comes from sasl.oauthbearer.config below.
        It is not used in this example but you can put arbitrary values to
        configure how you can get the token (e.g. which token URL to use)
        """
        payload = {
            'grant_type':'client_credentials',
            'scope':'openid',
            'client_id':os.getenv('CLIENT_ID'),
            'client_secret':os.getenv('CLIENT_SECRET')
        }
        resp = requests.post("https://auth.ocp01.toll6.tinaa.tlabs.ca/auth/realms/tinaa/protocol/openid-connect/token",
                             data = payload, verify = False)
        token = resp.json()
        return token['access_token'], time.time() + float(token['expires_in'])

    conf = {
        'bootstrap.servers':'pltf-msgbus.develop.ocp01.toll6.tinaa.tlabs.ca:443',
        'security.protocol':'SASL_SSL',
        'sasl.mechanisms':'OAUTHBEARER',
        'enable.ssl.certificate.verification':False,
        'oauth_cb':functools.partial(_get_token),
        'group.id':os.getenv('CLIENT_ID'),   # line added on 22 by prakash
        'debug': 'broker,admin,protocol',
    }

    return conf


API_NAME = "Topic Service"
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
MAX_MESSAGES_TO_PUBLISH = os.getenv('MAX_MESSAGES_TO_PUBLISH')


DEFAULT_MESSAGE_RETENTION_DURATION = "604800.0s"


# Kafka Topics CRUD Starts
@api_router.put("/topics/{TopicName}",
                responses={**Topic_Put_Successful_Response, **bad_request_responses, **unauthorized_responses,
                           **Resource_conflict_responses,
                           **Unexpected_error_responses})
async def Registers_a_topic_under_TINAA_pubsub(kafka_topic_object: KafkaTopicRequestSchema, TopicName: str = Path(...,
                                                                                                           description="Name of the topic. Follow the naming convention <app-team-name>-<environment-name>-<event-name>. Allowed environment names are develop, preprod, qa & prod. App team name examples -> bsaf, naaf & pltf. The three examples are for application teams TINAA Business Services Automation Framework, TINAA Network Applications Automation Framework & TINAA Platform team. If you do no belong to any of the three within TINAA, Please contact dlSDNPlatformSupportDistributionList@telus.com."),
                                               db: Session = Depends(get_db), authorization: str = Header(None), current_user: TokenPayload = Depends(get_current_user)):

    is_admin = is_user_admin(authorization)

    if not is_admin and not validate_name(TopicName):
        error = {
            "error": {
                "code": 422,
                "message": "Topic name must follow the naming convention: "
                           "<app-team-name>-<environment-name>-<topic-name>",
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
        logger.info(f'invalid Topic-Name format')
        return JSONResponse(content=error, status_code=422)

    kafka_topics_model = db.query(FedKafkaTopics).filter(FedKafkaTopics.kafka_topic_name == TopicName).first()
    if kafka_topics_model:
        error = {
            "error": {
                "code": 409,
                "message": "Given Kafka Topic Name Already exist in Tinaa kafka",
                "status": "error",
                "details": {
                    "reason": "Given Kafka Topic Name Already exist in Tinaa kafka",
                    "type": "invalid_parameter"
                }
            }
        }
        logger.info('Given Kafka Topic Name Already exist in Tinaa kafka')
        return JSONResponse(content=error, status_code=409)

    kafka_topic = kafka_topic_object.__dict__

    schema_settings = kafka_topic.get("schemaSettings")

    if schema_settings:
        existing_schema = db.query(SchemasInfo).filter(
            SchemasInfo.schema_name == schema_settings.schema_
        ).first()

        if existing_schema is None:
            logger.info('Given schema does not exist in database, please create it first')
            error = {
                "error": {
                    "code": 404,
                    "message": "Given schema does not exist in database, please create it first",
                    "status": "error",
                    "details": {
                        "reason": "Given schema does not exist in database, please create it first",
                        "type": ""
                    }
                }
            }
            return JSONResponse(content=error, status_code=404)

        elif not is_admin and existing_schema.created_by == "admin":
            logger.info(f'Given schema- {existing_schema.schema_name} belongs to admin category, '
                        f'can not be assigned')
            error = {
                "error":{
                    "code":400,
                    "message":"Given schema belongs to admin category, can not be assigned",
                    "status":"bad request",
                    "details":{
                        "reason":"Given schema belongs to admin category, can not be assigned",
                        "type":"error"
                    }
                }
            }
            return JSONResponse(content = error, status_code = 400)
    else:
        logger.info("No schema settings defined in the payload")

    if is_admin:
        try:
            logger.info(
                f"Topic {TopicName} found in TINAA Kafka, adding the topic in database in admin role")

            kafka_topics_model = FedKafkaTopics()

            if schema_settings is not None:
                kafka_topics_model.schema = schema_settings.schema_
                kafka_topics_model.encoding = schema_settings.encoding

            kafka_topics_model.messageRetentionDuration = kafka_topic.get(
                "messageRetentionDuration"
            ) or DEFAULT_MESSAGE_RETENTION_DURATION
            kafka_topics_model.kafka_topic_name = TopicName
            kafka_topics_model.kafka_bus_name = kafka_topic.get("kafka_bus_name")

            db.add(kafka_topics_model)
            db.commit()
            db.refresh(kafka_topics_model)
            logger.info('Created Record as admin Successfully')

            response = topic_put_create_response(kafka_topics_model, is_admin)

            return response
        except Exception as e:
            logger.info("Failed to create topic {}: {}".format(TopicName, e))
            error = {
                "error": {
                    "code": 500,
                    "message": "Failed to create topic {}: {}".format(TopicName, e),
                    "status": "string",
                    "details": {
                        "reason": str(e),
                        "type": "Unexpected error"
                    }
                }
            }
            return JSONResponse(content=error, status_code=500)

    try:
        # logic to check whether topic exists in tinaa kafka (125-165)

        consumer = Consumer(tinaa_config())
        consumer.poll(timeout = 10)
        topic_data = consumer.list_topics().topics
        topics = []
        for top in topic_data:
            topics.append(top)

        logger.info(f"Topic List fetched from Tinaa Kafka- {topics}")

        if TopicName in topics:
            try:
                logger.info(f"topic- {TopicName} found in Tinaa Kafka, adding the topic in database")

                kafka_topics_model = FedKafkaTopics()

                if schema_settings is not None:
                    kafka_topics_model.schema = schema_settings.schema_
                    kafka_topics_model.encoding = schema_settings.encoding

                kafka_topics_model.messageRetentionDuration = kafka_topic.get(
                    "messageRetentionDuration"
                ) or DEFAULT_MESSAGE_RETENTION_DURATION
                kafka_topics_model.kafka_topic_name = TopicName

                db.add(kafka_topics_model)
                db.commit()
                logger.info('Created Record Successfully')

                if schema_settings is not None:
                    topic_id = kafka_topics_model.id
                    schemas_name = kafka_topics_model.schema
                    print(kafka_topics_model.id)
                    schema_model = db.query(SchemasInfo).filter(SchemasInfo.schema_name == schemas_name).first()
                    schema_id = schema_model.id

                    schema_topic_db_object = SchemaTopicAssociation()
                    schema_topic_db_object.schemas_info_id = schema_id
                    schema_topic_db_object.kafka_topics_id = topic_id
                    db.add(schema_topic_db_object)
                    db.commit()

                return topic_put_create_response(kafka_topics_model, is_admin)
            except Exception as e:
                logger.info("Failed to create topic {}: {}".format(TopicName, e))
                error = {
                    "error": {
                        "code": 500,
                        "message": "Failed to create topic {}: {}".format(TopicName, e),
                        "status": "string",
                        "details": {
                            "reason": str(e),
                            "type": "Unexpected error"
                        }
                    }
                }
                return JSONResponse(content=error, status_code=500)

        admin_client = AdminClient(tinaa_config())

        logger.info(f"Defining new Kafka topic config for: {kafka_topic}")

        message_retention_duration = kafka_topic.get("messageRetentionDuration") or DEFAULT_MESSAGE_RETENTION_DURATION

        config = {
            "retention.ms": int(float(
                message_retention_duration.strip("s")
            ) * 1000)
        }

        logger.info(f"Config: {config}")

        new_topics = [NewTopic(
            topic,
            num_partitions=3,
            replication_factor=1,
            config=config
        ) for topic in [TopicName]]

        logger.info(f'new topic {TopicName}')
        admin_poll = admin_client.poll(10.0)
        fs = admin_client.create_topics(new_topics)
        for topic, f in fs.items():
            try:
                f.result()
                logger.info("Topic {} created".format(topic))

                kafka_topics_model = FedKafkaTopics()

                if schema_settings is not None:
                    kafka_topics_model.schema = schema_settings.schema_
                    kafka_topics_model.encoding = schema_settings.encoding

                kafka_topics_model.messageRetentionDuration = kafka_topic.get(
                    "messageRetentionDuration"
                ) or DEFAULT_MESSAGE_RETENTION_DURATION
                kafka_topics_model.kafka_topic_name = TopicName

                print("before adding to table")

                db.add(kafka_topics_model)
                db.commit()
                logger.info('Created Record Successfully')

                if schema_settings is not None:
                    topic_id = kafka_topics_model.id
                    schemas_name = kafka_topics_model.schema
                    print(kafka_topics_model.id)
                    schema_model = db.query(SchemasInfo).filter(SchemasInfo.schema_name == schemas_name).first()
                    schema_id = schema_model.id

                    schema_topic_db_object = SchemaTopicAssociation()
                    schema_topic_db_object.schemas_info_id = schema_id
                    schema_topic_db_object.kafka_topics_id = topic_id
                    db.add(schema_topic_db_object)
                    db.commit()

                return topic_put_create_response(kafka_topics_model, is_admin)
            except Exception as e:
                logger.info("Failed to create topic {}: {}".format(topic, e))
                error = {
                    "error": {
                        "code": 409,
                        "message": "Failed to create topic {}: {}".format(topic, e),
                        "status": "string",
                        "details": {
                            "reason": str(e),
                            "type": "Unexpected error"
                        }
                    }
                }
                return JSONResponse(content=error, status_code=500)

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
                "message": "Error occurred while registering a topic under TINAA pubsub ",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)


@api_router.get("/topics/{TopicName}", status_code=200,
                responses={**Topic_Put_Successful_Response, **unauthorized_responses, **Unexpected_error_responses})
async def Returns_a_topic(TopicName: str = Path(...,
                                                description="Name of the topic. Follow the naming convention "
                                                            "<app-team-name>-<environment-name>-<event-name>. Allowed "
                                                            "environment names are develop, preprod, qa & prod. App "
                                                            "team name examples -> bsaf, naaf & pltf. The three "
                                                            "examples are for application teams TINAA Business "
                                                            "Services Automation Framework, TINAA Network "
                                                            "Applications Automation Framework & TINAA Platform team. "
                                                            "If you do no belong to any of the three within TINAA, "
                                                            "Please contact "
                                                            "dlSDNPlatformSupportDistributionList@telus.com."),
                          db: Session = Depends(get_db),authorization: str = Header(None),current_user: TokenPayload = Depends(get_current_user)):
    logger.info(f' authorization {authorization}')
    is_admin = is_user_admin(authorization)
    logger.info(f'is_admin {is_admin}')

    kafka_topics_model = db.query(FedKafkaTopics).filter(FedKafkaTopics.kafka_topic_name == TopicName).first()

    try:
        if kafka_topics_model:
            if is_admin:
                logger.info('Response for external API')

            else:
                logger.info('Response for internal API')

            logger.info(f"kafka_topics_models_list {kafka_topics_model.id}")
            topic_response = {
                # "id": kafka_topic.id,
                #"name":kafka_topics_model.kafka_topic_name,
                "messageRetentionDuration":kafka_topics_model.messageRetentionDuration,
            }
            topic_schema_association = db.query(SchemaTopicAssociation).filter(
                SchemaTopicAssociation.kafka_topics_id == kafka_topics_model.id).first()
            if topic_schema_association:
                logger.info("inside topic_schema_association")
                schema_info_id = topic_schema_association.schemas_info_id
                logger.info(f"schema_info_id {schema_info_id}")
                schema_info = db.query(SchemasInfo).filter(SchemasInfo.id == schema_info_id).first()
                if schema_info:
                    logger.info("inside schema_info")
                    schema_definition = schema_info.definition
                    if schema_definition:
                        logger.info("inside schema_definition")
                        topic_response["schemaSettings"] = {
                            "schema":kafka_topics_model.schema,
                            "encoding":kafka_topics_model.encoding,
                        }
            if is_admin:
                topic_response["id"] = kafka_topics_model.id
                topic_response["kafka_bus_name"] = kafka_topics_model.kafka_bus_name

            return topic_response

        else:
            error = {
                "error": {
                    "code": 404,
                    "message": "Topic not Found.",
                    "status": "error",
                    "details": {
                        "reason": "Topic not Found.",
                        "type": "invalid_parameter"
                    }
                }
            }
            logger.info('No Record Found In DB')
            return JSONResponse(content=error, status_code=404)

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
                "message": "Error occurred while returning topic",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)



@api_router.get("/topics/get_record_by_bus_and_topic_name/{kafka_topic_name}/{kafka_bus_name}",
                responses={**unauthorized_responses, **Unexpected_error_responses}, include_in_schema=False)
async def get_topic_with_bus_name(kafka_topic_name: str, kafka_bus_name: str, db: Session = Depends(get_db)):
    try:
        kafkatopic_model = db.query(FedKafkaTopics).filter(
            FedKafkaTopics.kafka_topic_name == kafka_topic_name,
            FedKafkaTopics.kafka_bus_name == kafka_bus_name,
        ).first()

        if kafkatopic_model is not None:
            kafka_topic_id = kafkatopic_model.id
        else:
            kafka_topic_id = ''
        return kafka_topic_id
    except Exception as e:
        error = {
            "error": {
                "code": 500,
                "message": "Error occurred while fetching topic with busname",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)


@api_router.get("/topics",
                responses={**Get_all_topic_Successful_Response, **unauthorized_responses, **Unexpected_error_responses})
async def Lists_all_the_topics_available_for_subscription_via_TINAA_pubsub(db: Session = Depends(get_db),
                                                                           authorization: str = Header(None),
                                                                           current_user: TokenPayload = Depends(get_current_user)):
    all_topics = []
    try:
        is_admin = is_user_admin(authorization)
        logger.info(f'is_admin {is_admin}')

        logger.info("inside try")
        kafka_topics_models_list = db.query(FedKafkaTopics, SchemaTopicAssociation, SchemasInfo). \
            select_from(FedKafkaTopics). \
            join(SchemasInfo, FedKafkaTopics.schema == SchemasInfo.schema_name, isouter=True). \
            join(SchemaTopicAssociation, SchemaTopicAssociation.kafka_topics_id == FedKafkaTopics.id, isouter=True).all()

        for kafka_topic, topic_schema_relation, schema_info in kafka_topics_models_list:
            logger.info(f"topic- {kafka_topic.kafka_topic_name}")
            topic_name = kafka_topic.kafka_topic_name
            if topic_name.startswith("upubsub."):
                logger.info(f"topic_name :{topic_name} is part of mirrored topic hence excluding the topic" )
                continue
            elif topic_name.startswith("_"):
                logger.info(f"topic_name :{topic_name} is part of internal topic hence excluding the topic")
                continue
            topic_response = {
                    # "id": kafka_topic.id,
                    "name": kafka_topic.kafka_topic_name,
                    "messageRetentionDuration": kafka_topic.messageRetentionDuration,
                }
            if topic_schema_relation:
                logger.info(f"topic schema relation existed for topic- {kafka_topic.kafka_topic_name}")
                if schema_info.definition:
                    logger.info(f"Schema definition available for schema- {schema_info.schema_name}")
                    topic_response["schemaSettings"] = {
                        "schema":kafka_topic.schema,
                        "encoding":kafka_topic.encoding,
                    }

            if is_admin:
                topic_response["id"] = kafka_topic.id
                topic_response["kafka_bus_name"] = kafka_topic.kafka_bus_name

            all_topics.append(topic_response)

        response = {"topics": all_topics}
        return response

        # for kafka_topic in kafka_topics_models_list:
        #     #resp = topic_put_create_response(kafka_topic)
        #     #resp["name"] = kafka_topic.kafka_topic_name
        #     #all_topics.append(resp)
        #
        #     logger.info(f"kafka_topics_models_list {kafka_topic.id}")
        #     topic_response = {
        #         # "id": kafka_topic.id,
        #         "name": kafka_topic.kafka_topic_name,
        #         "messageRetentionDuration": kafka_topic.messageRetentionDuration,
        #     }
        #     topic_schema_association = db.query(SchemaTopicAssociation).filter(
        #         SchemaTopicAssociation.kafka_topics_id == kafka_topic.id).first()
        #     if topic_schema_association:
        #         logger.info("inside topic_schema_association")
        #         schema_info_id = topic_schema_association.schemas_info_id
        #         logger.info(f"schema_info_id {schema_info_id}")
        #         schema_info = db.query(SchemasInfo).filter(SchemasInfo.id == schema_info_id).first()
        #         if schema_info:
        #             logger.info("inside schema_info")
        #             schema_definition = schema_info.definition
        #             if schema_definition:
        #                 logger.info("inside schema_definition")
        #                 topic_response["schemaSettings"] = {
        #                     "schema": kafka_topic.schema,
        #                     "encoding": kafka_topic.encoding,
        #                 }
        #     if is_admin:
        #         topic_response["id"] = kafka_topic.id
        #         topic_response["kafka_bus_name"] = kafka_topic.kafka_bus_name
        #
        #     all_topics.append(topic_response)
        #
        # response = {"topics": all_topics}
        # return response
    except Exception as e:
        logger.error(f"Error occurred while Listing all the topics available for subscription via TINAA pubsub:{e}")
        error = {
            "error": {
                "code": 500,
                "message": "Error occurred while Listing all the topics available for subscription via TINAA pubsub",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)



@api_router.patch("/topics/{TopicName}",
                  responses={**Topic_Put_Successful_Response, **bad_request_responses, **unauthorized_responses,
                             **Unexpected_error_responses})
async def Registers_a_topic_under_TINAA_pubsub(kafka_topic_object: KafkaTopicRequestSchema, TopicName: str = Path(...,
                                                                                                                  description="Name of the topic. Follow the naming convention <app-team-name>-<environment-name>-<event-name>. Allowed environment names are develop, preprod, qa & prod. App team name examples -> bsaf, naaf & pltf. The three examples are for application teams TINAA Business Services Automation Framework, TINAA Network Applications Automation Framework & TINAA Platform team. If you do no belong to any of the three within TINAA, Please contact dlSDNPlatformSupportDistributionList@telus.com."),
                                               db: Session = Depends(get_db), authorization: str = Header(None), current_user: TokenPayload = Depends(get_current_user)):
    is_admin = is_user_admin(authorization)

    kafka_topics_model = db.query(FedKafkaTopics).filter(FedKafkaTopics.kafka_topic_name == TopicName).first()

    relation_to_be_updated = False
    relation_to_be_created = False
    relation_to_be_deleted = False

    try:
        if kafka_topics_model:
            if not is_admin and not validate_name(TopicName):
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

            kafka_request_payload = kafka_topic_object.__dict__
        
            schema_settings = kafka_request_payload.get("schemaSettings")

            if schema_settings:
                existing_schema = db.query(SchemasInfo).filter(
                    SchemasInfo.schema_name == schema_settings.schema_
                ).first()

                if existing_schema is None:
                    logger.info('Given schema does not exist in database, please create it first')
                    error = {
                        "error": {
                            "code": 404,
                            "message": "Given schema does not exist in database, please create it first",
                            "status": "error",
                            "details": {
                                "reason": "Given schema does not exist in database, please create it first",
                                "type": ""
                            }
                        }
                    }
                    return JSONResponse(content=error, status_code=404)

                elif not is_admin and existing_schema.created_by == "admin":
                    logger.info(f'Given schema- {existing_schema.schema_name} belongs to admin category, '
                                f'can not be assigned')
                    error = {
                        "error":{
                            "code":400,
                            "message":"Given schema belongs to admin category, can not be assigned",
                            "status":"bad request",
                            "details":{
                                "reason":"Given schema belongs to admin category, can not be assigned",
                                "type":"error"
                            }
                        }
                    }
                    return JSONResponse(content = error, status_code = 400)
            else:
                logger.info("No schema settings defined in the payload")

            if kafka_request_payload.get("messageRetentionDuration") is None:
                kafka_request_payload["messageRetentionDuration"] = DEFAULT_MESSAGE_RETENTION_DURATION

            if not is_admin:
                consumer = Consumer(tinaa_config())
                consumer.poll(timeout=10)

                topics = [topic for topic in consumer.list_topics().topics]

                logger.info(f"Topics fetched from TINAA Kafka: {topics}")

                if TopicName not in topics:
                    logger.info(f"Topic {TopicName} in not found in TINAA Kafka")
                    error = {
                        "error": {
                            "code": 422,
                            "message": f"Topic {TopicName} in not found in TINAA Kafka",
                            "status": "Failure",
                            "details": {
                                "reason": "Error",
                                "type": "validation_error"
                            }
                        }
                    }
                    return JSONResponse(content=error, status_code=422)

                try:
                    logger.info(f"Updating the topic configuration of {TopicName} in TINAA Kafka")

                    admin_client = AdminClient(tinaa_config())

                    admin_client.poll(10.0)

                    retention_in_ms = int(float(
                        kafka_request_payload.get("messageRetentionDuration").strip("s")
                    ) * 1000)

                    resources = [(
                        ConfigResource(
                            restype='TOPIC',
                            name=TopicName,
                            set_config={
                                "retention.ms": retention_in_ms
                            },
                        )
                    )]

                    admin_client.alter_configs(resources=resources)

                    resource_futures = admin_client.describe_configs(
                        [ConfigResource(restype=2, name=TopicName)],
                        request_timeout=10
                    )

                    for j in concurrent.futures.as_completed(
                        iter(resource_futures.values())
                    ):
                        config_response = j.result(timeout=1)
                        logger.info(f'Kafka topic config change response is : {config_response}')
                        retention_config_entry = config_response.get('retention.ms')

                    retention_in_ms_response = (
                        str(retention_config_entry).split('=')[-1]
                    ).replace('"', "")

                    # The config was changed in TINAA Kafka, but the response was 650s
                    # instead of the actual 620s, needs to be checked later
                    #
                    # if float(retention_in_ms) != float(retention_in_ms_response):
                    #     raise Exception(
                    #         f"The intended retention value: {retention_in_ms}, and the "
                    #         f"response value after altering the configuration "
                    #         f"{retention_in_ms_response} are not the same"
                    #     )

                except Exception as e:
                    logger.info(f"Failed to update topic {TopicName} in TINAA Kafka: {e}")
                    error = {
                        "error": {
                            "code": 500,
                            "message": f"Failed to update topic {TopicName} in TINAA Kafka: {e}",
                            "status": "Failure",
                            "details": {
                                "reason": str(e),
                                "type": "Unexpected error"
                            }
                        }
                    }
                    return JSONResponse(content=error, status_code=500)

            try:
                logger.info(f"Updating topic {TopicName} in DB")

                schema_settings = kafka_request_payload.get("schemaSettings")

                if not schema_settings:
                    if kafka_topics_model.schema:
                        kafka_topics_model.schema = ""
                        kafka_topics_model.encoding = ""
                        relation_to_be_deleted = True
                        logger.info("relation_to_be_deleted")
                else:
                    if not kafka_topics_model.schema:
                        kafka_topics_model.schema = schema_settings.schema_
                        kafka_topics_model.encoding = schema_settings.encoding
                        relation_to_be_created = True
                        logger.info("relation_to_be_created")
                    elif kafka_topics_model.schema != schema_settings.schema_:
                        kafka_topics_model.schema = schema_settings.schema_
                        kafka_topics_model.encoding = schema_settings.encoding
                        relation_to_be_updated = True
                        logger.info("relation_to_be_updated")

                kafka_topics_model.messageRetentionDuration = kafka_request_payload.get(
                    "messageRetentionDuration"
                )

                db.commit()
                logger.info('Updated Kafka topic record in DB successfully')

                if relation_to_be_updated:
                    schema_topic_relation_model = db.query(SchemaTopicAssociation). \
                        filter(SchemaTopicAssociation.kafka_topics_id == kafka_topics_model.id).first()
                    schema_topic_relation_model.schemas_info_id = existing_schema.id
                    db.commit()
                    logger.info(f"schema topic relation table updated with new schema- {schema_settings.schema_} "
                                f"received from request & topic- {TopicName}")
                elif relation_to_be_created:
                    schema_topic_relation_model = SchemaTopicAssociation()
                    schema_topic_relation_model.schemas_info_id = existing_schema.id
                    schema_topic_relation_model.kafka_topics_id = kafka_topics_model.id
                    db.add(schema_topic_relation_model)
                    db.commit()
                    logger.info(f"schema topic relation created for schema- {schema_settings.schema_} & topic- "
                                f"{TopicName}, since no schema was mapped with topic")
                elif relation_to_be_deleted:
                    schema_topic_relation_model = db.query(SchemaTopicAssociation). \
                        filter(SchemaTopicAssociation.kafka_topics_id == kafka_topics_model.id).first()
                    db.delete(schema_topic_relation_model)
                    db.commit()
                    logger.info(f"schema topic relation entry deleted for topic- {TopicName} as no schema input "
                                f"received in request")

                return topic_put_create_response(kafka_topics_model, is_admin)

            except Exception as e:
                logger.info(f"Failed to update topic {TopicName} in DB: {e}")
                error = {
                    "error": {
                        "code": 500,
                        "message": f"Failed to update topic {TopicName} in DB: {e}",
                        "status": "Failure",
                        "details": {
                            "reason": str(e),
                            "type": "Unexpected error"
                        }
                    }
                }
                return JSONResponse(content=error, status_code=500)

        else:
            error = {
                "error": {
                    "code": 404,
                    "message": "Given topic is not found in TINAA Kafka.",
                    "status": "error",
                    "details": {
                        "reason": "Given topic is not found in TINAA Kafka.",
                        "type": "invalid_parameter"
                    }
                }
            }
            logger.info('No Record Found In DB')
            return JSONResponse(content=error, status_code=404)

    except exc.SQLAlchemyError as e:
        logger.info("Data Base Connection Error")
        logger.debug(e)
        error = {
            "error": {
                "code": 500,
                "message": "Error occurred while connecting to Database",
                "status": "error",
                "details": {
                    "reason": str(e),
                    "type": "invalid_parameter"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)

    except Exception as e:
        error = {
            "error": {
                "code": 500,
                "message": "Error occurred while registering a topic under TINAA pubsub",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)


@api_router.delete("/topics/{TopicName}",
                   responses={**bad_request_responses, **unauthorized_responses, **Unexpected_error_responses})
async def Deregisters_a_topic_under_TINAA_pubsub(TopicName: str,
                                                 db: Session = Depends(get_db),
                                                 authorization: str = Header(None),
                                                 current_user: TokenPayload = Depends(get_current_user)):
    try:
        logger.info("API call for delete topic")

        is_admin = is_user_admin(authorization)

        topic_object = db.query(FedKafkaTopics).filter(FedKafkaTopics.kafka_topic_name == TopicName).first()

        if not topic_object:
            error = {
                "error":{
                    "code":400,
                    "message":"Topic does not exist",
                    "status":"error",
                    "details":{
                        "reason":"Topic does not exist",
                        "type":"invalid_parameter"
                    }
                }
            }
            logger.info('Topic not found In DB')
            return JSONResponse(content = error, status_code = 400)

        schema_topic_association_object = db.query(SchemaTopicAssociation).filter(
            SchemaTopicAssociation.kafka_topics_id == topic_object.id).first()

        if schema_topic_association_object:
            schema_object = db.query(SchemasInfo).filter(
                SchemasInfo.id == schema_topic_association_object.schemas_info_id).first()

        if not is_admin:
            logger.info("non-admin request for topic deletion, deleting the topic in Tinaa Kafka")

            consumer = Consumer(tinaa_config())
            consumer.poll(timeout = 10)

            topics = [topic for topic in consumer.list_topics().topics]

            logger.info(f"Topics fetched from TINAA Kafka: {topics}")

            if TopicName not in topics:
                logger.info(f"Topic {TopicName} in not found in TINAA Kafka")
                error = {
                    "error":{
                        "code":400,
                        "message":f"Topic {TopicName} in not found in TINAA Kafka",
                        "status":"Failure",
                        "details":{
                            "reason":"Error",
                            "type":"validation_error"
                        }
                    }
                }
                return JSONResponse(content = error, status_code = 400)

            admin_client = AdminClient(tinaa_config())
            admin_client.poll(10)
            fs = admin_client.delete_topics([TopicName], request_timeout = 20)

            # Wait for operation to finish.
            for topic, f in fs.items():
                try:
                    f.result()
                    logger.info(f"Deleted the topic- {TopicName} in Tinaa Kafka successfully")

                except Exception as e:
                    logger.info(f"Failed to delete topic- {TopicName} in Tinaa Kafka")
                    error = {
                        "error": {
                            "code": 500,
                            "message": "Failed to delete the topic in Tinaa Kafka",
                            "status": "error",
                            "details": {
                                "reason": str(e),
                                "type": "unexpected_error"
                            }
                        }
                    }
                    return JSONResponse(content = error, status_code = 500)

        if schema_topic_association_object:
            db.delete(schema_topic_association_object)
            db.commit()
            logger.info(f"Deleted schema topic association for topic- {TopicName} successfully")
            if schema_object:
                db.delete(schema_object)
                db.commit()
                logger.info(f"Deleted schema for topic- {TopicName} successfully")
            else:
                logger.debug(f"No schema found to delete for topic- {TopicName}")
        else:
            logger.debug(f"No schema topic association found to delete for topic- {TopicName}")

        logger.info("Deleting the topic in DB")
        db.delete(topic_object)
        db.commit()
        logger.info(f"Deleted topic- {TopicName} successfully in DB")

        return ""

    except exc.SQLAlchemyError as e:
        logger.info("Data Base Connection Error")
        error = {
            "error": {
                "code": 500,
                "message": "Error occurred while connecting to Database",
                "status": "error",
                "details": {
                    "reason": str(e),
                    "type": "invalid_parameter"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)

    except Exception as e:
        error = {
                "error": {
                    "code": 500,
                    "message": "Error occurred while Deregistering a topic under TINAA pubsub",
                    "status": "string",
                    "details": {
                        "reason": str(e),
                        "type": "Unexpected error"
                    }
                }
            }
        return JSONResponse(content=error, status_code=500)


# Kafka Topics CRUD Ends

def successful_response(status_code: int):
    return {
        'status': status_code,
        'transaction': 'Successful'
    }


def http_exception():
    return HTTPException(status_code=404, detail="Todo not found")


@api_router.get("/topics/get_schema_topic_association_details/{relation_id}", include_in_schema=False,
                responses={**unauthorized_responses, **Unexpected_error_responses})
async def get_schema_topic_association_details(relation_id: int, db: Session = Depends(get_db)):
    try:
        each_schema_topic = db.query(SchemaTopicAssociation).filter(SchemaTopicAssociation.id == relation_id).first()
        schema_model = db.query(SchemasInfo).filter(SchemasInfo.id == each_schema_topic.schemas_info_id).first()
        topics_model = db.query(FedKafkaTopics).filter(FedKafkaTopics.id == each_schema_topic.kafka_topics_id).first()
        schema_topic_dict = {}
        schema_topic_dict['topic_name'] = topics_model.kafka_topic_name
        schema_topic_dict['messageRetentionDuration'] = topics_model.messageRetentionDuration
        schema_topic_dict['bus_name'] = topics_model.kafka_bus_name
        schema_topic_dict['topic_status'] = topics_model.topic_status
        schema_topic_dict['schema_name'] = schema_model.schema_name
        schema_topic_dict['schema_type'] = schema_model.schema_type
        schema_topic_dict['schema_definition'] = schema_model.definition
        schema_topic_dict['schema_revision_id'] = schema_model.revisionId
        return schema_topic_dict
    except Exception as e:
        error = {
                "error": {
                    "code": 500,
                    "message": "Error Occurred while fetching schema topic association details",
                    "status": "string",
                    "details": {
                        "reason": str(e),
                        "type": "Unexpected error"
                    }
                }
            }
        return JSONResponse(content=error, status_code=500)


@api_router.put(
    "/topics/update_latest_schema_in_topic_schema_association/{old_schemas_info_id}/{latest_schemas_info_id}",
    include_in_schema=False)
async def update_schema_topic_association_to_latest_schema_version(old_schemas_info_id: int,
                                                                   latest_schemas_info_id: int,
                                                                   db: Session = Depends(get_db)):
    try:
        schema_topic_model = db.query(SchemaTopicAssociation).filter(
            SchemaTopicAssociation.schemas_info_id == old_schemas_info_id,
        )
        updated_ids = []
        if schema_topic_model is None:
            schema_topic_model = {}
        else:
            for each_association in schema_topic_model:
                each_association.schemas_info_id = latest_schemas_info_id
                db.add(each_association)
                db.commit()
                updated_ids.append(each_association)

        return updated_ids
    except Exception as e:
        error = {
                "error": {
                    "code": 500,
                    "message": "Error occurred while updating schema topic association to latest schema version",
                    "status": "string",
                    "details": {
                        "reason": str(e),
                        "type": "Unexpected error"
                    }
                }
            }
        return JSONResponse(content=error, status_code=500)


@api_router.get("/topics/topic_schema_associations/all", responses={**unauthorized_responses, **Unexpected_error_responses})
async def Lists_all_topic_schema_associations(db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):
    try:
        logger.info('Fetching all topic-schema associations')
        schema_topic_objects = db.query(SchemaTopicAssociation).all()
        #logger.info(f'schema_topic_obj is - {schema_topic_objects}')
        all_schema_topic_object = []
        if schema_topic_objects is not None:
            for each_schema_topic in schema_topic_objects:
                schema_model = db.query(SchemasInfo).filter(SchemasInfo.id == each_schema_topic.schemas_info_id).first()
                topics_model = db.query(FedKafkaTopics).filter(FedKafkaTopics.id == each_schema_topic.kafka_topics_id).first()
                schema_topic_dict = {}
                schema_topic_dict['topic_name'] = topics_model.kafka_topic_name
                schema_topic_dict['messageRetentionDuration'] = topics_model.messageRetentionDuration
                schema_topic_dict['bus_name'] = topics_model.kafka_bus_name
                # schema_topic_dict['topic_status'] = topics_model.topic_status
                schema_topic_dict['schema_name'] = schema_model.schema_name
                schema_topic_dict['schema_type'] = schema_model.schema_type
                schema_topic_dict['schema_definition'] = schema_model.definition
                schema_topic_dict['schema_revision_id'] = schema_model.revisionId
                all_schema_topic_object.append(schema_topic_dict)
        return all_schema_topic_object
    except Exception as e:
        error = {
                "error": {
                    "code": 500,
                    "message": "Error Occurred while listing all the topic with schema associations",
                    "status": "string",
                    "details": {
                        "reason": str(e),
                        "type": "Unexpected error"
                    }
                }
            }
        return JSONResponse(content=error, status_code=500)


@api_router.post("/topics/create/topic_schema_association", include_in_schema=False)
async def create_schema_topic_association_post_call_method(schema_topic_object: TopicSchemaAssociationPydantic, db: Session = Depends(get_db)):
    try:
        logger.info(f'schema_topic_object is - {schema_topic_object}')
        schema_topic_db_object = SchemaTopicAssociation()
        schema_topic_db_object.schemas_info_id = schema_topic_object.schemas_info_id
        schema_topic_db_object.kafka_topics_id = schema_topic_object.kafka_topics_id
        db.add(schema_topic_db_object)
        db.commit()
        return successful_response(201), schema_topic_db_object.id
    except Exception as e:
        error = {
                "error": {
                    "code": 500,
                    "message": "Error Occurred while creating a schema topic association with post call method",
                    "status": "string",
                    "details": {
                        "reason": str(e),
                        "type": "Unexpected error"
                    }
                }
            }
        return JSONResponse(content=error, status_code=500)


@api_router.post("/topics/{TopicName}:publish", response_model=None, responses={**publish_success_Response,
    **bad_request_responses,
    **unauthorized_responses,
    **Unexpected_error_responses})
def Publishes_a_message(Info: Messages,
                        TopicName: str = Path(..., description="Name of the topic"),
                        db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):
    try:
        TopicName = TopicName.split(':publish')[0]
        logger.info(f'Inside publish message to kafka topic - {TopicName}')
        kafkatopic_model = db.query(FedKafkaTopics).filter(FedKafkaTopics.kafka_topic_name == TopicName).first()
        if kafkatopic_model:
            logger.info(f'Topic - {TopicName} exist, proceeding with payload validation')
            kafka_topic_id = kafkatopic_model.id
            bus_name = kafkatopic_model.kafka_bus_name
            topic_associated_schema = kafkatopic_model.schema
            publish_messge_length = len(Info.messages)
            logger.debug(f'publish_messge_length - {publish_messge_length}, MAX_MESSAGES_TO_PUBLISH - {MAX_MESSAGES_TO_PUBLISH}')
            logger.debug(f'{type(publish_messge_length)}, {type(MAX_MESSAGES_TO_PUBLISH)}')
            if publish_messge_length > int(MAX_MESSAGES_TO_PUBLISH):
                error = {
                    "error": {
                        "code": 400,
                        "message": f"Message range exceeded",
                        "status": "error",
                        "details": {
                            "reason": f"Detail description: Maximum limit set to publish messages. Range: 1 to {int(MAX_MESSAGES_TO_PUBLISH)} messages",
                            "type": "Payload Limitation"}
                    }
                }
                return JSONResponse(content=error, status_code=400)
            else:
                logger.info('Payload validation successful')
                logger.info(f'Verifying schema association for topic - {TopicName}')
                topic_schema_association = topic_associated_schema
                if topic_schema_association:
                    logger.info(f'Getting schema associated for topic - {TopicName}')
                    schema_info = db.query(SchemasInfo).filter(SchemasInfo.schema_name == topic_schema_association).first()
                    if schema_info and schema_info.created_by != "admin":
                        logger.info(f'schema definition is - {schema_info.definition}')
                        schema_definition = schema_info.definition
                    else:
                        schema_definition = ""
                else:
                    logger.info(f'Topic - {TopicName} does not have associated schema')
                    schema_definition = ""
                create_info = PublishMessage.create(TopicName=TopicName, info_schema=Info,db=db, schema_definition=schema_definition, bus_name=bus_name)
                return create_info
        else:
            error = {
                "error": {
                    "code": 400,
                    "message": f"Topic '{TopicName}' not found in the Kafka topic table",
                    "status": "error",
                    "details": {
                        "reason": f"Detail description: Topic '{TopicName}' is not exist to publish messages, Create one to proceed",
                        "type": "Topic Not Found"}
                }
            }
            return JSONResponse(content=error, status_code=400)
    except Exception as e:
        error = {
                "error": {
                    "code": 500,
                    "message": "Error occurred while publishing a message",
                    "status": "string",
                    "details": {
                        "reason": str(e),
                        "type": "Unexpected error"
                    }
                }
            }
        return JSONResponse(content=error, status_code=500)
