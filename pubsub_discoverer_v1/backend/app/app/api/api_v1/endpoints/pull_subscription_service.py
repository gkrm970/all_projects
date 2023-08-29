from fastapi import APIRouter, Depends, Path, HTTPException
from app.models.models import AckIdsRecord, SubscriptionReTry, SubscriptionModel
from app.schemas.pull_subscription_schema import PullSubscriptionInput, AckIdsRecordSchema, MyResponse, \
    ReceivedMessages
from sqlalchemy.orm import Session
from app.api.deps import get_db, get_current_user
from app.schemas.token import TokenPayload
from app.schemas.subscription_schema import AckSchema
from app.core.config import settings
from starlette.responses import JSONResponse
from tinaa.logger.v1.tinaa_logger import get_app_logger
import datetime
from confluent_kafka import Consumer, KafkaException, KafkaError, TopicPartition
import requests
import sys
import base64
import hashlib
import binascii
import functools
import time
import os

# MAX_MESSAGES_TO_CONSUME = 7
MAX_MESSAGES_TO_CONSUME = int(os.getenv('MAX_MESSAGES_TO_CONSUME'))

API_NAME = "Pull Subscription API"
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

unexpected_error = {"error": {
    "code": 500,
    "message": "unexpected error",
    "status": "error",
    "details": {
        "reason": "unexpected error",
        "type": "unexpected error"
    }
}
}

bad_request_responses = {
    400: {
        "description": "bad request",
        "model": MyResponse
    }
}

unauthorized_responses = {
    401: {
        "description": "unauthorized",
        "model": MyResponse
    }
}

Unexpected_error_responses = {
    500: {
        "description": "Unexpected error",
        "model": MyResponse
    }
}

publish_success_response = {
    200: {
        "description": "Response body has the created subscription based on the following format",
        "model": ReceivedMessages
    }
}

ack_success_response = {
    200: {
        "description": "For successful response, response body is empty."
    }
}


def tinna_config(subscription=None, c_id=None):
    def _get_token(args, config_str):
        """
        Note here value of config comes from sasl.oauthbearer.config below.
        It is not used in this example but you can put arbitrary values to
        configure how you can get the token (e.g. which token URL to use)
        """
        resp = requests.post(args[1], data=args[0], verify=False)
        token = resp.json()
        print(f'token------------------ {token["access_token"]}')
        return token['access_token'], time.time() + float(token['expires_in'])

    payload = {
        'grant_type': 'client_credentials',
        'scope': 'openid',
        'client_id': os.getenv('CLIENT_ID'),
        'client_secret': os.getenv('CLIENT_SECRET')
    }

    token_payload_url_list = [payload,
                              'https://auth.ocp01.toll6.tinaa.tlabs.ca/auth/realms/tinaa/protocol/openid-connect/token']

    consumer_conf = {
        'bootstrap.servers': "pltf-msgbus.develop.ocp01.toll6.tinaa.tlabs.ca:443",
        'security.protocol': 'SASL_SSL',
        'sasl.mechanisms': 'OAUTHBEARER',
        'session.timeout.ms': 12000,
        'group.id': f'{subscription}',
        'client.id': f'consumer{c_id}',
        'enable.ssl.certificate.verification': False,
        'auto.offset.reset': 'earliest',
        'enable.auto.commit': False,
        'oauth_cb': functools.partial(_get_token, token_payload_url_list)
    }
    print(consumer_conf)
    return consumer_conf


def auto_update_the_ack_table(db):
    try:
        obj = db.query(AckIdsRecord).filter(AckIdsRecord.status == "deadLine").all()
        for o in obj:
            if o.time < (datetime.datetime.now() - datetime.timedelta(seconds=o.ack_deadline)):
                o.status = "unAck"
                db.commit()
        logger.info("AckId table updated")
    except Exception as e:
        logger.error(f"Could not update the db-{e}")
        error = {
            "error": {
                "code": 500,
                "message": "Error occured in auto updating the acknowlegement table",
                "status": "error",
                "details": {
                    "reason": str(e),
                    "type": "invalid_parameter"
                }
            }
        }
        logger.info('No Record Found In DB')
        return JSONResponse(content=error, status_code=404)


def get_subscription_detail(subscription, db):
    # to call the subscription API to get the configuration details of given subscription.
    try:
        subscription_object = db.query(SubscriptionModel).filter(SubscriptionModel.name == subscription).first()

        if not subscription_object:
            return None
        logger.info("subscription found")
        topic = subscription_object.topic
        ttl = float(subscription_object.ttl[:-1])
        min_backoff = float(subscription_object.minimumBackoff[:-1])
        max_backoff = float(subscription_object.maximumBackoff[:-1])
        ack_deadline = subscription_object.ackDeadlineSeconds + 20

        subscription_detail = [topic, ttl, min_backoff, max_backoff, ack_deadline]
        logger.info(f"Subscription detail- {subscription_detail}")
        return subscription_detail

    except Exception as e:
        logger.error(f"Could not fetch the subscription detail-{e}")
        error = {
            "error": {
                "code": 500,
                "message": 'Error occurred while fetching subscription details',
                "status": "Failed",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)


def check_for_base64(msg):
    try:
        base64.b64decode(msg, validate=True)
        b64_msg = msg.decode()
        logger.info(f'Its valid base 64 message {b64_msg}')
        return b64_msg

    except binascii.Error:
        b64_msg = base64.b64encode(msg).decode()
        logger.info(f'Converted the message to base64- {b64_msg}')
        return b64_msg


def retry_policy_check(subscription, min_backoff, max_backoff, ack_deadline, db):
    # Retry Policy :

    request_time = datetime.datetime.now()
    retry_subscription = db.query(SubscriptionReTry).where(SubscriptionReTry.subscription ==
                                                           subscription).all()

    if not retry_subscription:
        logger.info("First Pull request of subscription, adding into retry table.")
        retry_subscription = SubscriptionReTry()
        retry_subscription.subscription = subscription
        retry_subscription.first_pull_time = request_time
        retry_subscription.most_recent_pull_time = request_time
        retry_subscription.hit_backoff = 0
        db.add(retry_subscription)
        db.commit()
        return True

    logger.info("Entered into Retry Policy")
    retry_subscription = retry_subscription[0]
    retry_subscription.most_recent_pull_time = request_time
    db.add(retry_subscription)
    db.commit()

    first_pull_time = retry_subscription.first_pull_time
    hit_backoff = retry_subscription.hit_backoff
    sec_to_first_pull = (request_time - first_pull_time).total_seconds()

    logger.info(f"Seconds to first pull- {sec_to_first_pull}")

    if sec_to_first_pull <= (ack_deadline + min_backoff):
        return True

    elif sec_to_first_pull > (ack_deadline + max_backoff):
        logger.info("Subscription completed the retry cycle, updating the table as fresh")
        retry_subscription.first_pull_time = request_time
        retry_subscription.most_recent_pull_time = request_time
        retry_subscription.hit_backoff = 0
        db.add(retry_subscription)
        db.commit()
        return True

    elif sec_to_first_pull > (ack_deadline + min_backoff):
        min = min_backoff
        box = []
        while min <= max_backoff:
            min = min * 2
            if min <= max_backoff:
                box.append(min + ack_deadline)

        logger.info(f"backoff slots- {box}")

        if sec_to_first_pull > box[-1]:
            if hit_backoff <= len(box):
                logger.info(f"First pull call by the subscription in backoff slot {box[-1]}-{max_backoff}")
                retry_subscription.hit_backoff = len(box) + 1
                db.add(retry_subscription)
                db.commit()
                return True
            else:
                logger.info(f"Subscription making Pull request > 1 time in backoff slot {box[-1]}-{max_backoff}, "
                            f"so returning empty response")
                return None

        for i in range(len(box)):
            if sec_to_first_pull < box[i] and hit_backoff <= i:
                logger.info(f"First pull call by the subscription in backoff slot "
                            f"{box[i - 1] if i else min_backoff + ack_deadline}-{box[i]}")
                retry_subscription.hit_backoff = i + 1
                db.add(retry_subscription)
                db.commit()
                return True
            elif sec_to_first_pull < box[i] and hit_backoff == i + 1:
                logger.info(f"Subscription making Pull request > 1 time in backoff slot "
                            f"{box[i - 1] if i else min_backoff + ack_deadline}-{box[i]}, so returning empty response")
                return None


def consume_messages(msg_count, consumer, subscription, topic, detector, ack_deadline, min_backoff, max_backoff, db):
    try:
        un_consume_poll_limit = 5
        messages_detail = []
        while msg_count > 0 and un_consume_poll_limit:

            message_object = consumer.poll(timeout=5.0)

            if not message_object:
                logger.info("Consuming Messages")
                un_consume_poll_limit = un_consume_poll_limit - 1
                continue

            elif message_object.error():
                # End of partition event
                if message_object.error().code() == KafkaError._PARTITION_EOF:
                    sys.stderr.write('%% %s [%d] reached end at offset %d\n'
                                     % (message_object.topic(), message_object.partition(), message_object.offset()))

                else:
                    raise KafkaException(message_object.error())

            else:
                key = message_object.key().decode() if message_object.key() else "string"
                message = message_object.value()
                time_ms = message_object.timestamp()[1]
                b64_message = check_for_base64(message)
                ack_id = hashlib.sha512(f"{b64_message}:{time_ms}".encode()).hexdigest()

                # query the table whether ackId already exist or not
                db_check = db.query(AckIdsRecord).filter(AckIdsRecord.ackId == ack_id,
                                                         AckIdsRecord.status == "deadLine").all()

                if db_check:
                    logger.info("Skipping the consumed message as it is still under acknowledgement deadline")
                    continue

                ack_id_model = AckIdsRecord()
                ack_id_model.ackId = ack_id
                ack_id_model.subscription = subscription
                ack_id_model.topic = topic
                ack_id_model.partition = message_object.partition()
                ack_id_model.offset = message_object.offset()
                ack_id_model.time = detector
                ack_id_model.ack_deadline = ack_deadline
                ack_id_model.min_backoff = min_backoff
                ack_id_model.max_backoff = max_backoff
                ack_id_model.status = "deadLine"
                db.add(ack_id_model)
                db.commit()

                messages_detail.append({"ackId": ack_id, "message": b64_message, "messageId": key,
                                        "publishTime": datetime.datetime.fromtimestamp(
                                            time_ms / 1000.0, tz=datetime.timezone.utc)})

                msg_count = msg_count - 1

        return messages_detail

    except Exception as e:
        logger.error(f"Could not consume message due to error- {e}")
        error = {
            "error": {
                "code": 500,
                "message": 'Error occurred while consuming messages',
                "status": "Failed",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)


@api_router.post("/subscriptions/pull/{SubscriptionName}",
          responses = {**publish_success_response,
                       **bad_request_responses,
                       **unauthorized_responses,
                       **Unexpected_error_responses}
          )
async def pulls_message_for_the_subscription(input_data: PullSubscriptionInput, SubscriptionName: str = Path(..., description='Name of the Subscription'),
                                             db: Session = Depends(get_db),
                                             current_user: TokenPayload = Depends(get_current_user)):
    max_messages = input_data.maxMessages

    if not max_messages or max_messages > MAX_MESSAGES_TO_CONSUME:
        logger.error(f"Max message count should not be > {MAX_MESSAGES_TO_CONSUME}")
        error = {
            "error": {
                "code": 400,
                "message": f"Message range exceeded",
                "status": "error",
                "details": {
                    "reason": f"Detail description: Maximum limit set to consume messages. "
                              f"Range: 1 to {int(MAX_MESSAGES_TO_CONSUME)} messages",
                    "type": "Payload Limitation"}
            }
        }

        raise HTTPException(status_code=400, detail=error)

    # Getting topic name from subscription
    subscription_object = get_subscription_detail(SubscriptionName, db)

    # subscription_conf = db.query(SubscriptionModel).filter(SubscriptionModel.name == subscription).first()
    # subscription_object = db.query(SubscriptionModel).filter(SubscriptionModel.name == SubscriptionName).first()

    if not subscription_object:
        logger.error("Invalid subscriptionName!")
        error = {
            "error": {
                "code": 400,
                "message": "InValid SubscriptionName",
                "status": "error",
                "details": {
                    "reason": "validation error",
                    "type": "validation error"
                }
            }
        }

        raise HTTPException(status_code=400, detail=error)

    # topic = subscription_object.topic
    # ttl = float(subscription_object.ttl[:-1])
    # min_backoff = float(subscription_object.minimumBackoff[:-1])
    # max_backoff = float(subscription_object.maximumBackoff[:-1])
    # ack_deadline = subscription_object.ackDeadlineSeconds

    topic, ttl, min_backoff, max_backoff, ack_deadline = subscription_object

    # Updating the AckId Table :
    auto_update_the_ack_table(db)

    consumer = None
    try:
        # ReTry Policy Check
        retry_flag = retry_policy_check(SubscriptionName, min_backoff, max_backoff, ack_deadline, db)

        if not retry_flag:
            return {"receivedMessages": []}

        # fetching the config dict from config.py
        config = tinna_config(SubscriptionName, 1)

        # creating consumer object
        consumer = Consumer(**config)

        # subscribing the topic
        consumer.subscribe([topic])

        # calling the consume_messages to get the consumed messages (== max_messages)
        detector = datetime.datetime.now()
        messages_to_render = consume_messages(max_messages, consumer, SubscriptionName, topic, detector,
                                              ack_deadline, min_backoff, max_backoff, db)
        consumer.close()

        if not isinstance(messages_to_render, list):
            logger.error("Could not consume the messages")
            consumer.close()
            raise HTTPException(status_code=500, detail=unexpected_error)

        delivery_time = datetime.datetime.now()
        db.query(AckIdsRecord).where(AckIdsRecord.time == detector).update({"time": delivery_time})
        db.commit()

        logger.info(f"Delivering the messages to subscription-{SubscriptionName} at {delivery_time}")

        return {"receivedMessages": messages_to_render}

    except Exception as e:
        if consumer:
            consumer.close()
        logger.error(f"Error Occurred- {e}")
        error = {
            "error": {
                "code": 500,
                "message": 'Error occurred while pulling a message for subscription',
                "status": "Failed",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)


@api_router.post("/subscriptions/acknowledge/{SubscriptionName}",
          name = "Acknowledges messages associated for the subscription with ackIds",
          responses = {**ack_success_response,
                       **bad_request_responses,
                       **unauthorized_responses,
                       **Unexpected_error_responses
                       }
          )
async def acknowledges_the_ack_ids(input_data: AckSchema, SubscriptionName: str = Path(..., description="Name of the Subscription"),
                                   db: Session = Depends(get_db),
                                   current_user: TokenPayload = Depends(get_current_user)):
    ack_ids_send_by_user = input_data.ackIds
    logger.info(f"ackIds_send_by_user- {ack_ids_send_by_user}")

    # Getting topic name from subscription
    subscription_conf = get_subscription_detail(SubscriptionName, db)

    if not subscription_conf:
        logger.error("Invalid subscriptionName!")
        error = {
            "error": {
                "code": 400,
                "message": "InValid SubscriptionName",
                "status": "error",
                "details": {
                    "reason": "validation error",
                    "type": "validation error"
                }
            }
        }
        raise HTTPException(status_code=400, detail=error)

    topic, ttl, min_backoff, max_backoff, ack_deadline = subscription_conf

    # Updating the AckId table :
    auto_update_the_ack_table(db)

    matched_ack_ids = db.query(AckIdsRecord).filter(
        AckIdsRecord.ackId.in_(ack_ids_send_by_user),
        AckIdsRecord.status == "deadLine"
    ).all()

    if not matched_ack_ids:
        logger.info("No AckIds matched because of acknowledgement deadline over")
        error = {
            "error": {
                "code": 400,
                "message": "bad request",
                "status": "error",
                "details": {
                    "reason": "acknowledgement deadline over",
                    "type": "bad request"
                }
            }
        }
        raise HTTPException(status_code=400, detail=error)

    logger.info(f"matched AckIds- {matched_ack_ids}")
    unique_time = matched_ack_ids[0].time

    consumer = None

    try:
        # fetching the config dict from config.py
        config = tinna_config(SubscriptionName, 2)

        # creating consumer object
        consumer = Consumer(**config)

        # subscribing the topic
        consumer.subscribe([topic])

        # Getting the broker to commit
        un_consume_poll_limit = 5
        broker_found_to_commit = False

        while un_consume_poll_limit:

            ack_poll = consumer.poll(timeout=5.0)
            if not ack_poll:
                logger.info("Trying to get the broker for commit")
                un_consume_poll_limit = un_consume_poll_limit - 1
                continue
            else:
                broker_found_to_commit = True
                break

        if not broker_found_to_commit:
            logger.error("Could not find the broker to commit the acknowledged message")
            raise HTTPException(status_code=500, detail=unexpected_error)

        for matched_ack_id in matched_ack_ids:
            ack_id = matched_ack_id.ackId
            topic = matched_ack_id.topic
            partition = matched_ack_id.partition
            offset = matched_ack_id.offset

            com = consumer.commit(offsets=[TopicPartition(topic=topic, partition=partition, offset=offset + 1)],
                                  asynchronous=False)

            if not com:
                logger.error(f"failed to commit the messages")
                raise HTTPException(status_code=500, detail=unexpected_error)

            logger.info(f"topic-{topic}, partition-{partition}, offset-{offset} for subscription has been committed "
                        f"successfully for AckId-{ack_id}")

        consumer.close()

        # Deleting the records from ackIds table for the acknowledged message batch:-
        db.query(AckIdsRecord).where(AckIdsRecord.time == unique_time).delete()
        db.commit()
        logger.info("Acknowledged AckId batch has been deleted from AckId table")

        # Deleting the retry policy for the subscription:-
        db.query(SubscriptionReTry).where(SubscriptionReTry.subscription == SubscriptionName).delete()
        db.commit()
        logger.info("Retry Policy has been deleted for the subscription in retry table")

    except Exception as e:
        if consumer:
            consumer.close()
        logger.error(f"Error Occurred- {e}")
        error = {
            "error": {
                "code": 500,
                "message": "Error occurred while acknowledging a ack_id's",
                "status": "Failed",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)
