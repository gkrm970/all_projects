import base64
import logging
import signal
import ast
import functools
import os
import sys
import uuid
import subprocess
from typing import Union
from datetime import datetime
from confluent_kafka import Consumer, KafkaError, KafkaException
import json
import requests
import time
import binascii
from flask import jsonify
from utils.utils import http_status
from utils.logger import logger
import celeryconfig
from celery import Celery
from tinaa.utils.v1.auth_handler import AuthHandler, AuthenticationException
from concurrent.futures import ThreadPoolExecutor
from utils.utils import CeleryState
from confluent_kafka.schema_registry.avro import AvroSerializer, AvroDeserializer, SerializationError
from confluent_kafka.schema_registry.protobuf import ProtobufDeserializer
from confluent_kafka.schema_registry.json_schema import JSONDeserializer
from confluent_kafka.schema_registry import SchemaRegistryClient, SchemaRegistryError
from confluent_kafka.serialization import SerializationContext, MessageField

# Celery variables
TASK_DEFAULT_QUEUE = 'default'
broker_url = os.getenv('CELERY_BROKER_URL', "redis://localhost:6379/0")
backend_url = os.getenv('CELERY_RESULT_BACKEND', "redis://localhost:6379/0")
app = Celery(TASK_DEFAULT_QUEUE, broker=broker_url, backend=backend_url)
app.config_from_object(celeryconfig)
app.conf.task_default_queue = TASK_DEFAULT_QUEUE
# app.conf.worker_disable_timeouts = True

AUTH_TOKEN_URL = os.getenv('AUTH_TOKEN_URL', 'http://dummy/token')
CLIENT_ID = os.getenv('CLIENT_ID', 'test')
CLIENT_SECRET = os.getenv('CLIENT_SECRET', 'test')
auth_config = {'oauth2': {'oauth2_client_id': CLIENT_ID, 'oauth2_client_secret': CLIENT_SECRET,
                          'oauth2_token_url': AUTH_TOKEN_URL}}
auth_obj = AuthHandler(auth_config['oauth2'], logger)
port = os.getenv('SERVER_PORT', '8086')
SERVICE_PROTOCOL = os.getenv('SERVICE_PROTOCOL', 'http')
SERVICE_HOST = os.getenv('SERVICE_HOST', '10.17.36.156')
SERVICE_PORT = os.getenv('SERVICE_PORT', '8088')
SERVICE_ENDPOINT = os.getenv(
    'SERVICE_ENDPOINT', '/pubsub/v1/')
TINAA_KAFKA_LIST = os.getenv("TINAA_KAFKA_LIST", "")

service_url = f'{SERVICE_PROTOCOL}://{SERVICE_HOST}:{SERVICE_PORT}{SERVICE_ENDPOINT}'
running = True
break_flag = False
ttl_flag: Union[datetime, None] = None


# Set up signal handler for graceful shutdown
# def shutdown_workers(signum, frame):
#     logger.info('Shutting down Celery workers...')
#     with app.connection() as connection:
#         # Get all active workers
#         workers = app.control.inspect().active_queues().keys()
#         for worker in workers:
#             logger.info(f'Cleaning up any dependent resources')
#             perform_cleanup()
#             logger.info(f'Sending shutdown signal to worker: {worker}')
#             app.control.broadcast('shutdown', destination=[worker])
#     logger.info('Celery workers shut down successfully.')


# # Register the signal handler
# signal.signal(signal.SIGTERM, shutdown_workers)
# signal.signal(signal.SIGINT, shutdown_workers)


# Perform additional cleanup tasks here
# def perform_cleanup():
#     # Clean up resources, connections, caches, etc.
#     logger.info('Performing additional cleanup tasks...')


# def start_celery_worker(worker_name):
#     logger.info('start celery worker')
#     try:
#         # Start the Celery worker with the specified name and environment variable
#         #cmd = ['celery', '-A', 'app_worker', 'worker', f'--queues={worker_name}_queue', '--concurrency=1', '--loglevel=info', '-E', '-n', f'{worker_name}_worker']
#         cmd = ['celery', '-A', 'app_worker', 'worker', '--concurrency=1', '--loglevel=info', '-n', f'{worker_name}_worker']

#         # worker_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid)
#         # Open /dev/null as the output file to redirect stdout and stderr
#         #with open('/dev/null', 'w') as devnull:
#         #    worker_process = subprocess.Popen(cmd, stdout=devnull, stderr=devnull)
        
#         logger.info(f"Celery worker '{worker_name}' has been started with PID {worker_process.pid}.")

#         #subprocess.run(['ps', '-p', f'{worker_process.pid}', '-o', 'command'], check=True)
#         return True
#     except subprocess.CalledProcessError as e:
#         # Handle the exception here (e.g., log the error, perform cleanup, etc.)
#         logger.info(f"Failed to start Celery worker process: {e}")
#         raise Exception(f"Failed to start Celery worker process: {e}")


def kill_celery_worker(worker_name):
    logger.info('Kill current celery task')
    try:
        # Find and terminate the Celery worker process using subprocess
        cmd = ['pkill', '-f', f'{worker_name}']
        subprocess.run(cmd)
        logger.info(f"Celery worker '{worker_name}' has been terminated.")
        return True
    except subprocess.CalledProcessError as e:
        # Handle the exception here (e.g., log the error, perform cleanup, etc.)
        logger.info(f"Failed to start Celery worker process: {e}")
        raise Exception(f'Failed to start Celery worker process: {e}')


def do_auth() -> str:
    logger.info('inside do auth')
    config = {'oauth2': {'oauth2_client_id': CLIENT_ID, 'oauth2_client_secret': CLIENT_SECRET,
                         'oauth2_token_url': AUTH_TOKEN_URL}}
    logger.info(f'config obj is: {config["oauth2"]}')

    logger.debug(f'Auth config to get token is : {AUTH_TOKEN_URL}, {CLIENT_ID}, {CLIENT_SECRET}')
    auth = AuthHandler(config['oauth2'], logger)
    token = auth.get_access_token()
    if token:
        logger.info(f'token generated')
    else:
        logger.info(f'failed to generate token')
    return token


def _get_token(args, config_str):
    """Note here value of config comes from sasl.oauthbearer.config below.
        It is not used in this example but you can put arbitrary values to
        configure how you can get the token (e.g. which token URL to use)
        """
    resp = requests.post(args[1], data=args[0], verify=False)
    token = resp.json()
    logger.info(f'token------------------ {token["access_token"]}')
    return token['access_token'], time.time() + float(token['expires_in'])


def send_message_to_webhook(message, subscription_resp, consumer=None):
    logger.debug("Inside send_message_to_webhook")
    global ttl_flag, break_flag
    logger.info(f"Subscription response- {subscription_resp}")
    SubscriptionName = subscription_resp.get("name")
    min_backoff = ast.literal_eval(subscription_resp.get('retryPolicy').get('minimumBackoff')[:-1])
    logger.debug(f"{min_backoff}")
    max_backoff = ast.literal_eval(subscription_resp.get('retryPolicy').get('maximumBackoff')[:-1])
    logger.debug(f"{max_backoff}")
    ttl = ast.literal_eval(subscription_resp.get('expirationPolicy').get('ttl')[:-1])
    logger.debug(f"{ttl}")
    backoff = min_backoff
    deregister_url = service_url + 'subscriptions/'
    logger.debug(f"{deregister_url}")
    webhook_url = subscription_resp.get('pushConfig').get('pushEndpoint')
    logger.debug(f"{webhook_url}")
    try:
        logger.info("Inside webhook method")
        #logger.debug(f'sending message - {message}')
        # Not printing message since it is eating up space
        logger.debug(f'sending message to webhook')
        json_data = json.dumps(message)
        logger.debug(f"{json_data}")
        proper_json_data = json_data.replace("\\", "")
        logger.debug(f"{proper_json_data}")
        logger.info(f'Converted list of messages to string')
        if ttl_flag and (datetime.now() - ttl_flag).total_seconds() > ttl:
            headers = {'accept': 'application/json',
                       'Authorization': 'Bearer {}'.format(do_auth())}
            response = requests.delete(deregister_url + str(SubscriptionName), headers=headers)
            if response.status_code == 200:
                logger.info(f'subscription - {SubscriptionName} unregistered successfully')
            sys.exit()
        headers = {'accept': 'application/json', 'Content-Type': 'application/json'}
        response = requests.post(webhook_url, headers=headers, data=proper_json_data, verify=False)
        logger.info(f'Response after sending to webhook is: {response.status_code}')

        if response.status_code in http_status:
            logger.info('Message sent to webhook successfully')
            ttl_flag = None
            consumer.commit()
        #     break_flag = True
        # while break_flag:
        #     logger.info('break the loop')
        #     break
        else:
            while backoff <= max_backoff:
                logger.info(f"Failed to send webhook. Retrying in {backoff} seconds.")
                headers = {'accept': 'application/json', 'Content-Type': 'application/json'}
                response = requests.post(webhook_url, headers=headers, data=proper_json_data, verify=False)
                if response.status_code in http_status:
                    logger.info('Message successfully sent to webhook')
                    ttl_flag = None
                    consumer.commit()
                    break
                backoff = backoff ** 2
            if not ttl_flag:
                ttl_flag = datetime.now()
    except Exception as e:
        logger.exception(f"An exception occurred while sending the webhook: {e}")


def formatted_data(topic_json, binary_to_encoded_data, SubscriptionName, msg, binary_to_encoded_key):
    logger.debug("Formatting data")
    logger.debug(f"{topic_json}")
    schema_settings = topic_json.get("schemaSettings")

    if schema_settings is None:
        logger.debug("No schema settings when formatting data")
        data = {
            "message": {
                "data": binary_to_encoded_data,
                "messageId": binary_to_encoded_key,
                "publishTime": datetime.utcfromtimestamp(msg.timestamp()[1] / 1000).strftime('%Y-%m-%dT%H:%M:%SZ')
            },
            "subscription": SubscriptionName
        }

    else:
        data = {
            "message": {
                "attributes": {
                    "schemaencoding": topic_json.get('schemaSettings').get('encoding'),
                    "schemarevisionid": topic_json.get('schemaSettings').get('lastRevisionId'),
                    # "somekey": topic_json.get('labels').get('key')
                    "somekey": ""
                },
                "data": binary_to_encoded_data,
                "messageId": binary_to_encoded_key,
                "publishTime": datetime.utcfromtimestamp(msg.timestamp()[1] / 1000).strftime('%Y-%m-%dT%H:%M:%SZ')
            },
            "subscription": SubscriptionName
        }

    return data


def consuming_msg_for_ssl(kafka_conf, topic_name, response, topic_json, schema_json_response):
    logger.info(f'Inside consuming messages from SSL kafka')
    SubscriptionName = response.get("name")
    consumer = Consumer(kafka_conf)
    consumer.subscribe([topic_name])
    logger.info(f'Consumer-Instance: {consumer}')
    schema_registry_host = schema_json_response.get('schema_registry_host')
    schema_registry_port = schema_json_response.get('schema_registry_port')
    decoded_schema_registry_ssl_key = base64.b64decode(schema_json_response.get("schemaregistry_key_location"))
    decoded_schema_registry_ssl_cert = base64.b64decode(schema_json_response.get("schemaregistry_certificate_location"))
    schema_registry_ssl_key_location = "/tmp/api_created_client_schema_registry.ssl.key"
    schema_registry_ssl_certificate_location = "/tmp/api_created_client_schema_registry.ssl.crt"
    with open(schema_registry_ssl_key_location, 'wb') as file:
        file.write(decoded_schema_registry_ssl_key)
    with open(schema_registry_ssl_certificate_location, 'wb') as file:
        file.write(decoded_schema_registry_ssl_cert)

    schema_registry_conf = {'url': "https://" + str(schema_registry_host) + ':' + str(schema_registry_port),
                            "ssl.certificate.location":schema_registry_ssl_certificate_location,
                            "ssl.key.location":schema_registry_ssl_key_location}
    logger.info(f'schema_registry_conf: {schema_registry_conf}')

    try:
        schema_registry_client = SchemaRegistryClient(schema_registry_conf)
        schema = schema_registry_client.get_latest_version(subject_name=f"{topic_name}-value")
        schema_type = schema.schema.schema_type
        schema_str = schema.schema.schema_str
        logger.info(f'schema_type is {schema_type}')
        logger.info(f'schema_str : {schema_str}')
        if schema_type == 'AVRO' or schema_type == '':
            deserializer = AvroDeserializer(schema_registry_client=schema_registry_client,
                                            schema_str=schema_str)
            logger.info(f"deserialize_avro: {deserializer}")
        elif schema_type == 'JSON':
            deserializer = JSONDeserializer(schema_str=schema_str)
        elif schema_type == 'PROTOBUF':
            deserializer = ProtobufDeserializer(schema_registry_client=schema_registry_client)
    except SchemaRegistryError as e:
        logger.info(f"Schema registry/version not found for the topic- {topic_name}. Error- {e}")
        schema_str = ""

    while running:
        msg = consumer.poll(timeout=5.0)
        if msg is None:
            logger.info(f'Consuming message {msg}')
            continue
        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                sys.stderr.write('%% %s [%d] reached end at offset %d\n' %
                                 (msg.topic(), msg.partition(), msg.offset()))
            elif msg.error():
                raise KafkaException(msg.error())
        else:
            msg_key = msg.key()
            kafka_data = msg.value()
            logger.info(f'Messages are existed')
            logger.info(f'message before deserialization {kafka_data}')
            try:
                if msg_key:
                    binary_to_encoded_key = msg_key.decode('utf-8')
                else:
                    binary_to_encoded_key = str(uuid.uuid4().int)[:16]
                if schema_str:
                    deserializer_obj = deserializer(kafka_data,
                                                    SerializationContext(msg.topic(), MessageField.VALUE))
                    logger.info(f'message after deserialization {deserializer_obj}')
                    json_data = json.dumps(deserializer_obj)
                    binary_to_encoded_data = base64.b64encode(json_data.encode()).decode()
                    logger.info(f'binary_to_encoded_data: {binary_to_encoded_data}')
                else:
                    logger.info(f"Processing the message without deserialization, since no schema registry exist "
                                f"for topic- {topic_name}")
                    try:
                        binary_to_encoded_data = kafka_data.decode('utf-8')
                    except Exception as err:
                        binary_to_encoded_data = kafka_data
                        logger.info(f'Its not proper encoded data {err}')
                    try:
                        is_valid = base64.b64decode(binary_to_encoded_data, validate = True)
                        logger.info(f'messages are in base64 format - {is_valid}')

                    except binascii.Error:
                        logger.info(f'Converting received data into encoded format')
                        encoded_data = base64.b64encode(binary_to_encoded_data.encode('utf-8'))
                        binary_to_encoded_data = encoded_data.decode('utf-8')
                        logger.info(f'Messages encoded into base64 format successfully')

            except SerializationError as e:
                logger.info(f'serialization error: {str(e)}')
                continue
            except Exception as err:
                logger.info(f'Error while decoding the message{str(err)}')
                continue
            data = formatted_data(topic_json, binary_to_encoded_data, SubscriptionName, msg,
                                  binary_to_encoded_key)
            logger.debug(f"Formatted data: {data}")
            logger.info(f"Subscription response- {response}")
            send_message_to_webhook(data, response, consumer)
            logger.debug("Message sent to webhook")


def consuming_messages(conf, topic_name, response, topic_json):
    logger.info(f'Inside No SSL kafka consumer')
    SubscriptionName = response.get("name")
    consumer = Consumer(conf)
    consumer.subscribe([topic_name])
    logger.info(f'Consumer-Instance: {consumer}')
    while running:
        msg = consumer.poll(timeout=5.0)
        if msg is None:
            logger.info(f'Consuming message {msg}')
            continue
        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                sys.stderr.write('%% %s [%d] reached end at offset %d\n' %
                                 (msg.topic(), msg.partition(), msg.offset()))
                logger.info(f'inside message.error but no exception')
            elif msg.error():
                logger.info(f'Inside message.error raising exception')
                raise KafkaException(msg.error())
        else:
            msg_key = msg.key()
            kafka_data = msg.value()
            if kafka_data:
                logger.info(f'Messages are existed')
                try:
                    if msg_key:
                        binary_to_encoded_key = msg_key.decode('utf-8')
                    else:
                        binary_to_encoded_key = str(uuid.uuid4().int)[:16]
                    binary_to_encoded_data = kafka_data.decode('utf-8')
                except Exception as err:
                    binary_to_encoded_data = kafka_data
                    logger.info(f'Its not proper encoded data {err}')
                try:
                    is_valid = base64.b64decode(binary_to_encoded_data, validate=True)
                    logger.info(f'messages are in base64 format - {is_valid}')

                except binascii.Error:
                    logger.info(f'Converting received data into encoded format')
                    encoded_data = base64.b64encode(binary_to_encoded_data.encode('utf-8'))
                    binary_to_encoded_data = encoded_data.decode('utf-8')
                    logger.info(f'Messages encoded into base64 format successfully')
                data = formatted_data(topic_json, binary_to_encoded_data, SubscriptionName, msg, binary_to_encoded_key)
                logger.debug(f"Formatted data: {data}")
                logger.info(f"Subscription response- {response}")
                send_message_to_webhook(data, response, consumer)
                logger.debug("Message sent to webhook")


def topic_partions(consumer_conf, topic_name):
    consumer = Consumer(consumer_conf)
    msg = consumer.poll(1.0)
    cluster_metadata = consumer.list_topics(topic=topic_name)
    topic_metadata = cluster_metadata.topics.get(topic_name)
    topic_partition = len(topic_metadata.partitions)
    return topic_partition


@app.task(soft_time_limit=None, time_limit=None)
def basic_consume_loop(subscriptionname):
    handlers = logger.handlers
    for handler in handlers:
        handler.setFormatter(logging.Formatter(f'{subscriptionname}'+ ":: " + '%(asctime)s - %(name)s - %(levelname)s - [PID:%(process)d TID:%(thread)d] - %(module)s.%(funcName)s.%(lineno)s' f' - {subscriptionname}:: '  '%(message)s'))
    subscription_url = service_url + 'subscriptions/'
    topic_url = service_url + 'topics/'
    bus_url = service_url + 'fed_kafka_bus/get_by_name/'
    auth_url = service_url + 'auth_type/get_record_by/'

    # Register Celery Worker
    worker_data = {
        "worker_name": f'{subscriptionname}_worker',
        "task_id": '',
        "status": CeleryState.ACTIVE.value,
        "subscription_name": subscriptionname,
        "topic_name": '',
        "mirror_topic_name": '',
    }  
    
    schema_registry_url = service_url + 'schemaregistry/'

    # write worker status
    logger.info(f'write worker status in CeleryWorkerStatus table {worker_data}')
    worker_resp = create_worker_status_fields(data=worker_data)
    logger.info(f'worker_resp: {worker_resp}')
    if not worker_resp:
        logger.info('Subscription status successfuly updated in CeleryWorkerStatus table')

    try:
        logger.info(f'subscription_name:{subscriptionname}')
        subscription_url = subscription_url + str(subscriptionname)
        logger.info(f'subscription_url:{subscription_url}')
        headers = {'accept': 'application/json',
                'Authorization': 'Bearer {}'.format(do_auth())}
        logger.info(f'subscription_url:{subscription_url}')
        subscription_response = requests.get(subscription_url, headers=headers)
        logger.info(f'subscription_status_code:{subscription_response.status_code}')
        response = subscription_response.json()
        logger.info(f"Subscription response- {response}")
        topic_name = response['topic']
        if subscription_response.status_code == 200:

            logger.info(f'Topic_name:{topic_name}')
            topic_url = topic_url + str(topic_name)
            logger.info(f'topic_url:{topic_url}')
            headers = {'accept': 'application/json',
                        'Authorization': 'Bearer {}'.format(do_auth())}

            topic_response = requests.get(topic_url, headers=headers)
            logger.info(f'topic_url_status_code:{topic_response.status_code}:{topic_response}')

            if topic_response.status_code == 200:
                topic_json = topic_response.json()
                logger.info(f'topic_json:{topic_json}')
                bus_name = topic_json['kafka_bus_name']
                bus_name_txt = ast.literal_eval(bus_name)[0]

                # Update topic name in Celery Worker State table
                if TINAA_KAFKA_LIST not in bus_name:
                    logger.info(
                        f" Bus name: {bus_name} is not TINAA kafka, proceeding for mirroring"
                    )
                    worker_data['mirror_topic_name'] = f'{bus_name_txt}.{topic_name}'
                else:
                    worker_data['topic_name'] = topic_name

                worker_resp = update_worker_status_by_fields(subscription_name=subscriptionname, data=worker_data)
                if worker_resp:
                    logger.info('Topic Name successfuly updated in CeleryWorkerStatus table')

                bus_url = bus_url + str(topic_json['kafka_bus_name'])
                logger.info(f'bus_url: {bus_url}')
                headers = {'accept': 'application/json',
                            'Authorization': 'Bearer {}'.format(do_auth())}
                bus_response1 = requests.get(bus_url, headers=headers)
                logger.info(f'bus_url_status_code:{bus_response1.status_code}:{bus_response1.json()}')

                bus_response = bus_response1.json()
                logger.info(f'Bus-response:{bus_response}')
                auth_type = bus_response['kafka_auth_type']
                logger.info(f'auth_type: {auth_type}')
                auth_response = ''
                kafka_host = bus_response['kafka_host']
                kafka_port = bus_response['kafka_port']
                if auth_type is not None:
                    auth_url = f"{auth_url}{auth_type}/{json.dumps(kafka_host)}"
                    logger.info(f'auth_url: {auth_url}')
                    headers = {'accept': 'application/json',
                                'Authorization': 'Bearer {}'.format(do_auth())}
                    auth_response = requests.get(auth_url, headers=headers)
                    logger.info(f'auth_url_status_code: {auth_response.status_code}:{auth_response.json()}')
                logger.info(f'bus_response - {bus_response}')
                logger.info(f'host: {kafka_host}, port: {kafka_port} and topic_name: {topic_name}')
                bootstrap_servers = ','.join([f'{host}:{kafka_port}' for host in kafka_host])
                logger.info(f'bootstrap_servers: {bootstrap_servers}')

                # Create the Consumer object using Bus and Auth details
                conf = ''
                if auth_type is None:
                    conf = {
                        'bootstrap.servers': bootstrap_servers,
                        'group.id': str(subscriptionname),
                        'auto.offset.reset': 'earliest',
                        'session.timeout.ms': 6000
                    }

                elif auth_type == "SSL":
                    logger.info(f'Get SSL details from auth_info table')
                    auth_info_response_json = auth_response.json()
                    logger.info(f'Authentication info response is : {auth_info_response_json}')

                    decoded_ssl_key_location = base64.b64decode(auth_info_response_json.get('ssl_key_location'))
                    decoded_ssl_cert_location = base64.b64decode(auth_info_response_json.get('ssl_certificate_location'))

                    key_filename = f"api_created_client_ssl_key_{kafka_host[0]}.key"
                    logger.info(f'key_filename - {key_filename}')
                    cert_filename = f"api_created_client_ssl_cert_{kafka_host[0]}.crt"
                    logger.info(f'cert_filename - {cert_filename}')

                    key_filepath = os.path.join("/tmp", key_filename)
                    logger.info(f'key_filepath - {key_filepath}')
                    cert_filepath = os.path.join("/tmp", cert_filename)
                    logger.info(f'cert_filepath - {cert_filepath}')

                    with open(key_filepath, 'wb') as file:
                        file.write(decoded_ssl_key_location)
                    with open(cert_filepath, 'wb') as file:
                        file.write(decoded_ssl_cert_location)

                    conf = {
                        'bootstrap.servers': bootstrap_servers,
                        'security.protocol':"SSL",
                        'session.timeout.ms':6000,
                        'auto.offset.reset':'earliest',
                        #'ssl.key.location':auth_info_response_json.get('ssl_key_location'),
                        #'ssl.certificate.location':auth_info_response_json.get('ssl_certificate_location'),
                        'ssl.key.location':key_filepath,
                        'ssl.certificate.location':cert_filepath,
                        'ssl.key.password':auth_info_response_json.get('ssl_key_password'),
                        'enable.ssl.certificate.verification': False,
                        'group.id': os.getenv('CLIENT_ID')
                    }
                    schema_registry_id = bus_response.get('schema_registry_id')
                    logger.info(f"schema_registry_id- {schema_registry_id}")
                    if not schema_registry_id:
                        logger.info('No schema registry onboarded with SSL kafka bus, going to consume '
                                    'message without deserialization')
                    else:
                        schema_registry_url = f'{schema_registry_url}{schema_registry_id}'
                        schema_registry_response = requests.get(schema_registry_url, headers = headers)
                        logger.info(f"schema status code- {str(schema_registry_response.status_code)}")
                        logger.info(f"schema registry response- {str(schema_registry_response.json())}")
                        schema_registry_response = schema_registry_response.json()
                    logger.info(f"ssl kafka conf dict- {conf}")
                elif auth_type == "OAUTHBEARER":
                    logger.info(f'Get OAUTHBEARER details from auth_info table')
                    logger.info(f'kafka_host_response - {kafka_host}')
                    auth_info_response_json = auth_response.json()
                    logger.info(f'Authentication info response is : {auth_info_response_json}')

                    payload = {
                        'grant_type': 'client_credentials',
                        'client_id': auth_info_response_json.get('oauth_client_id'),
                        'client_secret': auth_info_response_json.get('oauth_client_secret')
                    }
                    token_payload_url_list = [payload, auth_info_response_json.get('oauth_URL')]
                    logger.info(f'token_payload_url_list is : {token_payload_url_list}')
                    logger.debug(f'Bootstrap servers are : {bootstrap_servers}')

                    decoded_oauth_issuer = base64.b64decode(auth_info_response_json.get('oauth_issuer'))
                    logger.info(f'decoded_oauth_issuer - {decoded_oauth_issuer}')

                    certificate_filename = f"telus_tcso_ca_{kafka_host[0]}.pem.txt"
                    logger.info(f'certificate_filename - {certificate_filename}')
                    certificate_filepath = os.path.join("/tmp", certificate_filename)
                    logger.info(f'certificate_filepath - {certificate_filepath}')

                    with open(certificate_filepath, 'wb') as file:
                        file.write(decoded_oauth_issuer)

                    conf = {
                        'bootstrap.servers': bootstrap_servers,
                        # 'debug': 'msg',
                        'security.protocol': auth_info_response_json.get('oauth_security_protocol'),
                        'session.timeout.ms': 60000,
                        'auto.offset.reset': 'earliest',
                        'enable.auto.commit': False,
                        'sasl.mechanisms': auth_info_response_json.get('auth_type'),
                        'ssl.certificate.location': certificate_filepath,
                        'ssl.ca.location': certificate_filepath,
                        'enable.ssl.certificate.verification': False,
                        'client.id': 'consumer-push-plugin',
                        # 'group.id': auth_info_response_json.get('oauth_client_id'),
                        'group.id': str(subscriptionname),
                        'oauth_cb': functools.partial(_get_token, token_payload_url_list)
                    }
                    logger.info(f'OAUTHBEARER kafka conf response - {conf}')
            else:
                logger.error(f'Faild to fetch Topic response: {topic_response.text} status-code: {topic_response.status_code}')
                raise Exception(f'Faild to fetch Topic response: {topic_response.text} status-code: {topic_response.status_code}')

            logger.info(f'Consumer Obj: {conf}')
            number_of_topic_partions = topic_partions(conf, topic_name)
            logger.info(f'number_of_topic_partions: {number_of_topic_partions}')

            max_consumers = os.getenv('MAX_CONSUMERS')
            logger.info(f'max_consumers value is -  {int(max_consumers)}')
            with ThreadPoolExecutor(max_workers = number_of_topic_partions) as executor:
                for i in range(number_of_topic_partions):
                    logger.info(f'inside thread {i}')
                    if auth_type == 'SSL' and schema_registry_id:
                        executor.submit(consuming_msg_for_ssl, kafka_conf = conf, topic_name = topic_name,
                                        response = response,
                                        topic_json = topic_json,
                                        schema_json_response = schema_registry_response)
                    else:
                        executor.submit(consuming_messages, conf = conf, topic_name = topic_name,
                                        response = response,
                                        topic_json = topic_json)

    except Exception as e:
        logger.error(f'Error while scheduling push service job - {subscriptionname}')
        logger.error(f'{str(e)}')
        worker_data['status'] = CeleryState.ERROR.value
        worker_resp = update_worker_status_by_fields(subscription_name=subscriptionname, data=worker_data)
        if worker_resp:
            logger.error('Job Failed Celery Error status updated in CeleryWorkerStatus table')


def get_worker_by_status(status):
    try:
        worker_url = service_url + 'celery_worker'

        worker_status_url = f'{worker_url}/get_by_status/{status}'
        logger.info(f'worker_status_url: {worker_status_url}')
        headers = {'accept': 'application/json',
                    'Authorization': 'Bearer {}'.format(do_auth())}
        worker_response = requests.get(worker_status_url, headers=headers, json={})
        logger.info(f'worker_status_code:{worker_response.status_code}')

        if worker_response.status_code == 200:
            inactive_db_task = worker_response.json()
            logger.info(f'inactive_db_task {status}: {inactive_db_task}')
            return inactive_db_task
        else:
            raise Exception(f'Faild to fetch Celery worker status: {worker_response.text}')
    except Exception as e:
        logger.info(f'Faild to fetch Celery worker status: {e}')


def get_worker_by_subscription(subscription_name):
    try:
        worker_url = service_url + 'celery_worker'

        worker_status_url = f'{worker_url}/get_by_subscription/{subscription_name}'
        logger.info(f'worker_status_url: {worker_status_url}')
        headers = {'accept': 'application/json',
                    'Authorization': 'Bearer {}'.format(do_auth())}
        worker_response = requests.get(worker_status_url, headers=headers, json={})
        logger.info(f'worker_status_code:{worker_response.status_code}')

        if worker_response.status_code == 200:
            worker_db_task = worker_response.json()
            logger.info(f'worker_db_task: {worker_db_task}')
            return worker_db_task
        else:
            raise Exception(f'Faild to fetch Celery worker by subscrption: {worker_response.text}')
    except Exception as e:
        logger.info(f'Faild to fetch Celery worker subscrption: {e}')


def create_worker_status_fields(data):
    try:
        worker_url = service_url + 'celery_worker'

        worker_status_url = f'{worker_url}/create'
        logger.info(f'worker_status_url : {worker_status_url}')
        headers = {'accept': 'application/json',
                    'Authorization': 'Bearer {}'.format(do_auth())}
        worker_response = requests.post(worker_status_url, headers=headers, json=data)
        logger.info(f'worker_status_code:{worker_response.status_code}')

        if worker_response.status_code == 201:
            db_task = worker_response.json()
            logger.info(f'create worker response: {db_task}')
            return db_task
        else:
            raise Exception(f'Faild to Create Celery worker status: {worker_response.text}')
    except Exception as e:
        logger.info(f'Faild to Create Celery worker status table: {e}')


def update_worker_status(subscription_name, status):
    try:
        worker_url = service_url + 'celery_worker'

        worker_status_url = f'{worker_url}/status/update/{subscription_name}'
        logger.info(f'worker_status_url: {worker_status_url}')
        headers = {'accept': 'application/json',
                    'Authorization': 'Bearer {}'.format(do_auth())}
        payload = {'status': status}
        worker_response = requests.put(worker_status_url, headers=headers, json=payload)
        logger.info(f'worker_status_code:{worker_response.status_code}')

        if worker_response.status_code == 200:
            inactive_db_task = worker_response.json()
            logger.info(f'inactive_db_task: {inactive_db_task}')
            return inactive_db_task
        else:
            raise Exception(f'Faild to update Celery worker status: {worker_response.text}')
    except Exception as e:
        logger.info(f'Faild to update Celery worker status: {e}')


def update_worker_status_by_fields(subscription_name, data):
    try:
        worker_url = service_url + 'celery_worker'

        worker_status_url = f'{worker_url}/update/{subscription_name}'
        logger.info(f'worker_status_url: {worker_status_url}')
        headers = {'accept': 'application/json',
                    'Authorization': 'Bearer {}'.format(do_auth())}
        worker_response = requests.put(worker_status_url, headers=headers, json=data)
        logger.info(f'worker_status_code:{worker_response.status_code}')

        if worker_response.status_code == 200:
            inactive_db_task = worker_response.json()
            logger.info(f'inactive_db_task: {inactive_db_task}')
            return inactive_db_task
        else:
            raise Exception(f'Faild to update Celery worker status: {worker_response.text}')
    except Exception as e:
        logger.info(f'Faild to update Celery worker status: {e}')


def delete_worker_status_by_subscription(subscription_name):
    try:
        worker_url = service_url + 'celery_worker'

        worker_status_url = f'{worker_url}/delete/{subscription_name}'
        logger.info(f'worker_status_url: {worker_status_url}')
        headers = {'accept': 'application/json',
                    'Authorization': 'Bearer {}'.format(do_auth())}
        worker_response = requests.delete(worker_status_url, headers=headers)
        logger.info(f'worker_status_code:{worker_response.status_code}')

        if worker_response.status_code == 200:
            return True
        else:
            raise Exception(f'Faild to delete Celery worker: {worker_response.text}')
    except Exception as e:
        logger.info(f'Faild to delete: {e}')


def active_worker_controller():
    logger.info('Worker controller started!')
    inactive_db_task = get_worker_by_status(CeleryState.ERROR.value)
    delete_db_task = get_worker_by_status(CeleryState.DELETED.value)
    workers = app.control.inspect().active_queues()
    if workers:
        logger.info(f'inactive_db_task: {inactive_db_task}')
        # Retry by Kill task and Create Task again
        if inactive_db_task:
            for task_obj in inactive_db_task:
                worker_name = task_obj.get('worker_name')
                subscription_name = task_obj.get('subscription_name')

                # Fetch active worker
                worker_data = get_worker_by_subscription(subscription_name)
                worker_status = worker_data.get('status')
                logger.info(f'inactive_worker: {worker_data}')
                logger.info(f'inactive_worker status: {worker_status}')
                
                if worker_status == CeleryState.ERROR.value:
                    # Kill current Invalid worker node
                    kill_worker = kill_celery_worker(worker_name)
                    if kill_worker:
                        logger.info('Subscription associated Worker thread killed')

                    # Start new worker node
                    os.environ['CUSTOM_WORKER_NAME'] = f'{subscription_name}_worker'
                    os.environ['CUSTOM_QUEUE_NAME'] = f'{subscription_name}_queue'
                    time.sleep(2)
                    process = subprocess.Popen(['sh', './start_worker.sh'])
                    logger.info(f'Start celery process: {process}')
                    if  process.returncode is None:
                        logger.info(f'Celery workers started successfully, proceeding to start a task asynchronously')
                        # non-blocking call, which means they do not wait for the task to complete
                        time.sleep(5)
                        app.control.add_consumer(f'{subscription_name}_queue')
                        result = basic_consume_loop.apply_async([subscription_name], queue=f'{subscription_name}_queue')
                        #result = basic_consume_loop.delay(subscription_name)
                        
                        if result.id:
                            logger.info("Task was sent successfully")

                        # Update state changes in DB
                        update_worker_status(subscription_name, CeleryState.ACTIVE.value)

                    logger.info(f"{worker_name} workerd started")
        else:
            logger.info('No in-active workers in DB: skipped')

        logger.info(f'delete_db_task: {delete_db_task}')
        if delete_db_task:
            for task_obj in delete_db_task:
                worker_name = task_obj.get('worker_name')
                subscription_name = task_obj.get('subscription_name')

                # Fetch active worker
                worker_data = get_worker_by_subscription(subscription_name)
                worker_status = worker_data.get('status')
                logger.info(f'inactive_worker: {worker_data}')
                logger.info(f'inactive_worker status: {worker_status}')
                
                if worker_status == CeleryState.DELETED.value:
                    # Kill current Invalid worker node
                    kill_worker = kill_celery_worker(worker_name)
                    if kill_worker:
                        logger.info('Subscription associated Worker thread killed')
                    
                    worker_resp = delete_worker_status_by_subscription(subscription_name)
                    if worker_resp:
                        logger.info('worker status record deletd from db')

    else:
        logger.info('No Active workers, force re-create all workers')
        all_active_worker_state = get_worker_by_status('all')
        logger.info(f'all_worker_state: {all_active_worker_state}')
        if all_active_worker_state:
            for task_obj in all_active_worker_state:
                worker_name = task_obj.get('worker_name')
                subscription_name = task_obj.get('subscription_name')

                # Fetch active worker
                workers = app.control.inspect().active_queues()
                active_workers_list = workers.keys() if workers else []
                logger.info(f'active workers: {active_workers_list}')

                if len(active_workers_list) == 0 or len([aw for aw in active_workers_list if worker_name in aw]) == 0:
                
                    # Start new worker node
                    os.environ['CUSTOM_WORKER_NAME'] = f'{subscription_name}_worker'
                    os.environ['CUSTOM_QUEUE_NAME'] = f'{subscription_name}_queue'
                    time.sleep(2)
                    process = subprocess.Popen(['sh', './start_worker.sh'])
                    logger.info(f'Start celery process: {process}')
                    if process.returncode is None:
                        logger.info(f'Celery workers started successfully, proceeding to start a task asynchronously')
                        # non-blocking call, which means they do not wait for the task to complete
                        time.sleep(5)
                        app.control.add_consumer(f'{subscription_name}_queue')
                        result = basic_consume_loop.apply_async([subscription_name], queue=f'{subscription_name}_queue')
                        #result = basic_consume_loop.delay(subscription_name)
                        if result.id:
                            logger.info("Task was sent successfully")

                            # Update state changes in DB
                            update_worker_status(subscription_name, CeleryState.ACTIVE.value)

                    logger.info("All workerd started")
        else:
            logger.info("No active workers in DB")

def shutdown():
    global running
    running = False

