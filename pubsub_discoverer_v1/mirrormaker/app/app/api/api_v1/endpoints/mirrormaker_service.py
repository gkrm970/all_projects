# Built in libraries
import ast
import base64
import configparser
import functools
import os
import signal
import subprocess
from pathlib import Path

import time
import urllib.parse
from typing import Any

# External in libraries
import requests
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic
from fastapi.routing import APIRouter
from pydantic import Field
from tinaa.utils.v1.auth_handler import AuthHandler
from tinaa.logger.v1.tinaa_logger import get_app_logger
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException
from starlette import status

from app.api.utils.loggers import logger
from app.core.config import settings

router = APIRouter()

AUTH_TOKEN_URL = os.getenv("AUTH_TOKEN_URL")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
auth_config = {
    "oauth2": {
        "oauth2_client_id": CLIENT_ID,
        "oauth2_client_secret": CLIENT_SECRET,
        "oauth2_token_url": AUTH_TOKEN_URL,
    }
}
AUTH_HANDLER_OBJ = AuthHandler(auth_config["oauth2"], logger)
MM_SERVICE_PORT = os.getenv("MM_SERVICE_PORT", 8086)
SERVICE_PROTOCOL = os.getenv("SERVICE_PROTOCOL", "http")
SERVICE_HOST = os.getenv("SERVICE_HOST", "10.17.36.156")
SERVICE_PORT = os.getenv("SERVICE_PORT", "8088")
SERVICE_ENDPOINT = os.getenv("SERVICE_ENDPOINT", "/pubsub/v1/")
SERVICE_URL = urllib.parse.urljoin(
    f"{SERVICE_PROTOCOL}://{SERVICE_HOST}:{SERVICE_PORT}", SERVICE_ENDPOINT
)

TINAA_KAFKA_LIST = os.getenv("TINAA_KAFKA_LIST", "")
DESTINATION_BUS_NAME = os.getenv("DESTINATION_BUS_NAME", "")


def tinaa_config():
    def _get_token(config_str):
        """Note here value of config comes from sasl.oauth bearer.config below.
        It is not used in this example but you can put arbitrary values to
        configure how you can get the token (e.g. which token URL to use)
        """
        payload = {
            'grant_type': 'client_credentials',
            'scope': 'openid',
            'client_id': os.getenv('CLIENT_ID'),
            'client_secret': os.getenv('CLIENT_SECRET')
        }
        resp = requests.post("https://auth.ocp01.toll6.tinaa.tlabs.ca/auth/realms/tinaa/protocol/openid-connect/token",
                             data=payload, verify=False)
        token = resp.json()
        return token['access_token'], time.time() + float(token['expires_in'])

    conf = {
        'bootstrap.servers': 'pltf-msgbus.develop.ocp01.toll6.tinaa.tlabs.ca:443',
        'security.protocol': 'SASL_SSL',
        'sasl.mechanisms': 'OAUTHBEARER',
        'enable.ssl.certificate.verification': False,
        'oauth_cb': functools.partial(_get_token),
        'group.id': os.getenv('CLIENT_ID'),  # line added on 22 by prakash
        'debug': 'broker,admin,protocol',
    }

    return conf


def do_auth() -> str:
    logger.info("inside do auth")
    logger.info(f'config obj is: {auth_config["oauth2"]}')
    logger.debug(
        f"Auth config to get token is : {AUTH_TOKEN_URL}, {CLIENT_ID}, {CLIENT_SECRET}"
    )
    token = AUTH_HANDLER_OBJ.get_access_token()
    logger.info(f"token is : {token}")

    return token


def _get_token():
    """Note here value of config comes from sasl.oauthbearer.config below.
    It is not used in this example, but you can put arbitrary values to
    configure how you can get the token (e.g. which token URL to use)
    """
    payload = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    logger.info("getting token")
    resp = requests.post(AUTH_TOKEN_URL, data=payload, verify=False)
    logger.info(f"response is : {resp}")
    token = resp.json()
    logger.info(f"token response is - {token}")
    logger.info(token["access_token"])
    return token["access_token"], time.time() + float(token["expires_in"])


oauth_configuration = tinaa_config()


# Find process id for Run mirror_maker.sh
def find_mirror_maker_process():
    process = subprocess.Popen(
        ["pgrep", "-f", "runMirrorMaker.sh"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True
    )
    stdout, _ = process.communicate()
    process_id = None
    if stdout:
        process_ids = stdout.strip().split("\n")
        if len(process_ids) > 0:
            process_id = int(process_ids[0])
            logger.info(f'process_id:{process_id}')
    return process_id


def create_topic_and_start_mirror_maker(
        source_topic_name, consumer_filename, connect_filename, bus_name
):
    logger.info("inside configurations")
    logger.info(f"functools_token:{functools.partial(_get_token)}")
    admin_client = AdminClient(oauth_configuration)
    logger.info("-------------------Trying to poll-------------------")
    logger.info(f"admin_polling:{admin_client.poll(timeout=10)}")
    logger.info("------------------Polling done-------------------")

    new_topic = [
        NewTopic(
            f"upubsub.{ast.literal_eval(bus_name)[0]}.{str(source_topic_name)}", num_partitions=3, replication_factor=3
        )
    ]
    logger.info(f"new_topic_name:{new_topic}")
    futures = admin_client.create_topics(new_topic)  # it's dict
    logger.info(f"futures:{futures}")
    for destination_topic_name, future in futures.items():
        try:
            future.result()
            logger.info(f"Topic created {destination_topic_name} ")
            logger.info(f"new_topic_name is :{destination_topic_name}")
            path = Path(__file__)
            ROOT_DIR = path.parent.absolute()
            producer_path = os.path.join(ROOT_DIR, "config/producer.properties")
            child = subprocess.Popen(
                [
                    "sh",
                    "./runMirrorMaker.sh",
                    connect_filename,
                    consumer_filename,
                    producer_path,
                    source_topic_name,
                    destination_topic_name,
                ]
            )
            return_code = child.poll()
            logger.info("MirrorMaker process started with PID: {}".format(child.pid))
            logger.info(f"returnCode - {return_code}")
            return destination_topic_name
        except Exception as e:
            logger.error(f"Failed to create topic {destination_topic_name} ,{e}")

    # admin_client.poll(timeout=10)


def _configure_consumer_properties(bootstrap_servers: str, kafka_host: str, auth_type, auth_response) -> str:
    kafka_host = str(kafka_host)
    if auth_type is None:
        config = configparser.ConfigParser()
        path = Path(__file__)
        ROOT_DIR = path.parent.absolute()
        config_path = os.path.join(ROOT_DIR, "config/consumer.properties")
        config.read(config_path)
        # config.read("config/consumer.properties")
        logger.info("inside the consumer properties")
        # set the consumer properties file
        config.set("consumer", "bootstrap.servers", bootstrap_servers)
        config.set("consumer", "exclude.internal.topics", "true")
        config.set("consumer", "group.id", "mirror_maker_consumer")
        consumer_filename = "consumer_" + ast.literal_eval(kafka_host)[0] + ".properties"
        with open(consumer_filename, "w") as configfile:
            config.write(configfile)

        return consumer_filename
    elif auth_type == "OAUTHBEARER":
        config = configparser.ConfigParser()
        config.read("config/consumer.properties")
        logger.info("inside the OAUTH consumer properties")
        # set the consumer properties file
        config.set("consumer", "bootstrap.servers", bootstrap_servers)
        config.set("consumer", "exclude.internal.topics", "true")
        config.set("consumer", "group.id", "mirror_maker_consumer")
        config.set("consumer", "client_id", CLIENT_ID)
        config.set("consumer", "client_secret", CLIENT_SECRET)
        config.set("consumer", "oauth_cb", str(oauth_configuration['oauth_cb']))  # Modified here   -->(str)

        consumer_oauth_bearer_filename = "OAUTH_BEARER_consumer_" + ast.literal_eval(kafka_host)[0] + ".properties"
        with open(consumer_oauth_bearer_filename, "w") as configfile:
            config.write(configfile)
        logger.info("completed OAUTH bearer consumer file")
        return consumer_oauth_bearer_filename

    elif auth_type == "SSL":
        auth_response = auth_response.json()
        decoded_ssl_keystore_location = base64.b64decode(auth_response["ssl_keystore_location"])
        decoded_ssl_truststore_location = base64.b64decode(auth_response["ssl_truststore_location"])

        keystore_filename = f"ssl_keystore_{ast.literal_eval(kafka_host)[0]}.jks"
        logger.info(f"{keystore_filename=}")
        truststore_filename = f"ssl_truststore_{ast.literal_eval(kafka_host)[0]}.jks"
        logger.info(f"{truststore_filename=}")

        keystore_filepath = os.path.join("/tmp", keystore_filename)
        logger.info(f"{keystore_filepath=}")
        truststore_filepath = os.path.join("/tmp", truststore_filename)
        logger.info(f"{truststore_filepath=}")

        with open(keystore_filepath, "wb") as keystore_file:
            keystore_file.write(decoded_ssl_keystore_location)

        with open(truststore_filepath, "wb") as truststore_file:
            truststore_file.write(decoded_ssl_truststore_location)

        config = configparser.ConfigParser()
        config.read("config/consumer.properties")
        logger.info("inside the consumer properties")

        # set the consumer properties file
        config.set("consumer", "bootstrap.servers", bootstrap_servers)
        config.set("consumer", "exclude.internal.topics", "true")
        config.set("consumer", "group.id", "mirror_maker_consumer")
        config.set("consumer", "security.protocol", "SSL")

        config.set("consumer", 'ssl.keystore.location', keystore_filepath)
        config.set("consumer", 'ssl.keystore.password', auth_response['ssl_keystore_password'])
        config.set("consumer", "ssl.truststore.location", truststore_filepath)
        config.set("consumer", "ssl.truststore.password", auth_response['ssl_truststore_password'])

        consumer_ssl_filename = "SSL_consumer_" + ast.literal_eval(kafka_host)[0] + ".properties"
        with open(consumer_ssl_filename, "w") as configfile:
            config.write(configfile)
        logger.info("completed SSL consumer file")
        return consumer_ssl_filename


def _configure_connect_mirror_maker_properties(
        bootstrap_servers: str, source_topic_name: str, kafka_host: str, auth_type: str, auth_response) -> str:
    kafka_host = str(kafka_host)
    admin_client = AdminClient({'bootstrap.servers': bootstrap_servers})

    topic_metadata = admin_client.list_topics(timeout=10)
    num_partitions = topic_metadata.topics[source_topic_name].partitions
    if auth_type is None:
        clusters = "source, destination"
        config = configparser.ConfigParser()
        path = Path(__file__)
        ROOT_DIR = path.parent.absolute()
        config_path = os.path.join(ROOT_DIR, "config/mm2.properties")
        config.read(config_path)
        # config.read("config/mm2.properties")
        logger.info("inside connect mirror maker property file")
        config.set("connect-mirrormaker", "clusters", clusters)
        config.set("connect-mirrormaker", "source.bootstrap.servers", bootstrap_servers)
        config.set(
            "connect-mirrormaker",
            "destination.bootstrap.servers",
            # "pltf-msgbus-sasl.develop.ocp01.toll6.tinaa.tlabs.ca:443")
            DESTINATION_BUS_NAME)

        # config.set("connect-mirrormaker", "security.protocol", auth_response["oauth_security_protocol"])
        # config.set("connect-mirrormaker", "sasl.mechanism", auth_response["oauth_security_mechanism"])
        # config.set("connect-mirrormaker", "ssl.certificate.location", auth_response["oauth_issuer"])
        # config.set("connect-mirrormaker", "ssl.ca.location", auth_response["oauth_issuer"])
        config.set("connect-mirrormaker", "destination.security.protocol", "SASL_SSL")
        config.set("connect-mirrormaker", "destination.sasl.mechanism", "OAUTHBEARER")
        # config.set("connect-mirrormaker", "ssl.certificate.location", auth_response["oauth_issuer"])
        # config.set("connect-mirrormaker", "ssl.ca.location", auth_response["oauth_issuer"])
        config.set("connect-mirrormaker","destination.sasl.jaas.config","org.apache.kafka.common.security.oauthbearer.OAuthBearerLoginModule required\n")
        config.set("connect-mirrormaker","destination.token.endpoint","https://auth.ocp01.toll6.tinaa.tlabs.ca/auth/realms/tinaa/protocol/openid-connect/token\n")
        config.set("connect-mirrormaker", "destination.client.id", CLIENT_ID)
        config.set("connect-mirrormaker", "destination.client.secret", CLIENT_SECRET)
        config.set("connect-mirrormaker", "destination.ssl.truststore.location", "ca.p12\n")
        config.set("connect-mirrormaker", "destination.ssl.truststore.password", "STOREPASSW0RD\n")
        config.set("connect-mirrormaker", "oauth_cb", str(oauth_configuration['oauth_cb']))


        config.set(
            "connect-mirrormaker", "destination.config.storage.replication.factor", "3"
        )
        config.set(
            "connect-mirrormaker", "destination.offset.storage.replication.factor", "3"
        )
        config.set(
            "connect-mirrormaker", "destination.status.storage.replication.factor", "3"
        )
        config.set("connect-mirrormaker", "source->destination.enabled", "true")
        config.set("connect-mirrormaker", "destination->source.enabled", "false")

        config.set("connect-mirrormaker", "source->destination.topics", source_topic_name)
        connect_filename = "connect-mirror-maker_" + ast.literal_eval(kafka_host)[0] + ".properties"
        logger.info(f"config:{config}")
        with open(connect_filename, "w") as configfile:
            config.write(configfile)

        return connect_filename

    elif auth_type == "OAUTHBEARER":
        clusters = "source, destination"
        config = configparser.ConfigParser()
        config.read("config/mm2.properties")
        logger.info("inside OAUTH BEARER connect mirror maker property file")
        config.set("connect-mirrormaker", "clusters", clusters)
        config.set("connect-mirrormaker", "source.bootstrap.servers", bootstrap_servers)
        config.set(
            "connect-mirrormaker",
            "destination.bootstrap.servers",
            # "pltf-msgbus-sasl.develop.ocp01.toll6.tinaa.tlabs.ca:443",
            DESTINATION_BUS_NAME)

        config.set(
            "connect-mirrormaker", "destination.config.storage.replication.factor", "3"
        )
        config.set(
            "connect-mirrormaker", "destination.offset.storage.replication.factor", "3"
        )
        config.set(
            "connect-mirrormaker", "destination.status.storage.replication.factor", "3"
        )
        config.set("connect-mirrormaker", "source->destination.enabled", "true")
        config.set("connect-mirrormaker", "destination->source.enabled", "false")

        config.set("connect-mirrormaker", "source->destination.topics", source_topic_name)
        config.set("connect-mirrormaker", "client_id", CLIENT_ID)
        config.set("connect-mirrormaker", "client_secret", CLIENT_SECRET)
        config.set("connect-mirrormaker", "security.protocol", auth_response["oauth_security_protocol"])
        config.set("connect-mirrormaker", "sasl.mechanism", auth_response["oauth_security_mechanism"])
        config.set("connect-mirrormaker", "ssl.certificate.location", auth_response["oauth_issuer"])
        config.set("connect-mirrormaker", "ssl.ca.location", auth_response["oauth_issuer"])
        config.set("connect-mirrormaker", "oauth_cb",
                   str(oauth_configuration['oauth_cb']))  # Modified ----str()
        config.set("connect-mirrormaker", "offset-syncs.topic.replication.factor", "3")
        config.set("connect-mirrormaker", "heartbeats.topic.replication.factor", "3")
        config.set("connect-mirrormaker", "checkpoints.topic.replication.factor", "3")
        config.set("connect-mirrormaker", "tasks.max", num_partitions)
        config.set("connect-mirrormaker", "replication.factor", "3")
        config.set("connect-mirrormaker", "refresh.topics.enabled", "true")
        config.set("connect-mirrormaker", "sync.topic.configs.enabled", "true")

        # Enable heartbeats and checkpoints.
        config.set(
            "connect-mirrormaker", "source->destination.emit.heartbeats.enabled", "true"
        )
        config.set(
            "connect-mirrormaker", "source->destination.emit.checkpoints.enabled", "true"
        )
        connect_oauth_bearer_filename = "OAUTH_BEARER_connect-mirror-maker_" + ast.literal_eval(kafka_host)[
            0] + ".properties"
        logger.info(f"config:{config}")
        with open(connect_oauth_bearer_filename, "w") as configfile:
            config.write(configfile)

        return connect_oauth_bearer_filename

    elif auth_type == "SSL":
        auth_response = auth_response.json()
        decoded_ssl_keystore_location = base64.b64decode(auth_response['ssl_keystore_location'])
        decoded_ssl_truststore_location = base64.b64decode(
            auth_response['ssl_truststore_location'])

        keystore_filename = f"ssl_keystore_{ast.literal_eval(kafka_host)[0]}.jks"
        logger.info(f'{keystore_filename=}')
        truststore_filename = f"ssl_truststore_{ast.literal_eval(kafka_host)[0]}.jks"
        logger.info(f'{truststore_filename=}')

        keystore_filepath = os.path.join("/tmp", keystore_filename)
        logger.info(f'{keystore_filepath=}')
        truststore_filepath = os.path.join("/tmp", truststore_filename)
        logger.info(f'{truststore_filepath=}')

        with open(keystore_filepath, 'wb') as keystore_file:
            keystore_file.write(decoded_ssl_keystore_location)
        with open(truststore_filepath, 'wb') as truststore_file:
            truststore_file.write(decoded_ssl_truststore_location)

        clusters = "source ,destination"
        config = configparser.ConfigParser()
        config.read("config/mm2.properties")
        logger.info("inside SSL connect mirror maker property file")
        config.set("connect-mirrormaker", "clusters", clusters)
        config.set("connect-mirrormaker", "source.bootstrap.servers", bootstrap_servers)
        config.set(
            "connect-mirrormaker",
            "destination.bootstrap.servers",
            # "pltf-msgbus-sasl.develop.ocp01.toll6.tinaa.tlabs.ca:443",
            DESTINATION_BUS_NAME)

        config.set(
            "connect-mirrormaker", "destination.config.storage.replication.factor", "3"
        )
        config.set(
            "connect-mirrormaker", "destination.offset.storage.replication.factor", "3"
        )
        config.set(
            "connect-mirrormaker", "destination.status.storage.replication.factor", "3"
        )
        config.set("connect-mirrormaker", "source->destination.enabled", "true")
        config.set("connect-mirrormaker", "destination->source.enabled", "false")

        config.set("connect-mirrormaker", "source->destination.topics", source_topic_name)
        config.set("connect-mirrormaker", "security.protocol", "SSL")
        config.set("connect-mirrormaker", "sasl.mechanism", 'SSL')

        # Enable heartbeats and checkpoints.
        config.set(
            "connect-mirrormaker", "source->destination.emit.heartbeats.enabled", "true"
        )
        config.set(
            "connect-mirrormaker", "source->destination.emit.checkpoints.enabled", "true"
        )

        config.set("connect-mirrormaker", "ssl.truststore.location", truststore_filepath)
        config.set("connect-mirrormaker", "ssl.truststore.password",
                   auth_response['ssl_truststore_password'])
        config.set("connect-mirrormaker", "ssl.keystore.location", keystore_filepath)
        config.set("connect-mirrormaker", "ssl.keystore.password", auth_response['ssl_keystore_password'])
        config.set("connect-mirrormaker", "offset-syncs.topic.replication.factor", "3")
        config.set("connect-mirrormaker", "heartbeats.topic.replication.factor", "3")
        config.set("connect-mirrormaker", "checkpoints.topic.replication.factor", "3")
        config.set("connect-mirrormaker", "tasks.max", num_partitions)
        config.set("connect-mirrormaker", "replication.factor", "3")
        config.set("connect-mirrormaker", "refresh.topics.enabled", "true")
        config.set("connect-mirrormaker", "sync.topic.configs.enabled", "true")

        connect_ssl_filename = "SSL_connect-mirror-maker_" + ast.literal_eval(kafka_host)[0] + ".properties"

        logger.info(f"{config=}")
        with open(connect_ssl_filename, "w") as configfile:
            config.write(configfile)

        return connect_ssl_filename


@router.delete("/by-topic/{topic_name}", status_code=200)
def delete_topic(topic_name: str):
    try:
        # Expecting to create delete topic end point
        topic_url = urllib.parse.urljoin(SERVICE_URL, "topics/")
        topic_url = topic_url + str(topic_name)
        logger.info(f"topic_url:{topic_url}")
        topic_response = perform_get_request(topic_url)
        logger.info(
            f"topic_url and status_code:{topic_response.status_code}:{topic_response}"
        )
        if topic_response.status_code == 200:
            topic_json = topic_response.json()
            logger.info(f"topic_json:{topic_json}")
            bus_name = topic_json["kafka_bus_name"]
            bus_name_txt = ast.literal_eval(bus_name)[0]
            # Converting into mirror-maker topic
            mirrored_topic = f'upubsub.{bus_name_txt}.{topic_name}'
            delete_topic_url = urllib.parse.urljoin(SERVICE_URL, "topics/")
            delete_topic_url = delete_topic_url + str(mirrored_topic)
            logger.info(f'delete_topic_url:{delete_topic_url}')
            delete_response = perform_get_request(delete_topic_url)
            logger.info(
                f"delete response and its status_code:{delete_response}:{delete_response.status_code}"
            )
            if delete_response.status_code == 200:
                delete_json = delete_response.json()
                logger.info(f"delete_json:{delete_json}")
                logger.info(f"topic_name:{mirrored_topic} is deleted successfully")
                process_id = find_mirror_maker_process()
                if process_id is not None:
                    # Terminate the MirrorMaker process using SIGTERM signal
                    os.kill(process_id, signal.SIGTERM)
                    logger.info(f"MirrorMaker process_id :{process_id} is terminated successfully ")
                    return HTTPException(status_code=status.HTTP_200_OK,
                                         detail=f"Mirror maker topic :{mirrored_topic} is deleted successfully.")
                else:
                    logger.info(f"Process_id is None ")
                    return HTTPException(status_code=status.HTTP_200_OK,
                                         detail=f"Process_id:{process_id} Unable to terminate the process.")
            else:
                logger.error(
                    f"Error while deleting the topic is- {mirrored_topic}"
                )
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                    detail=f"Error while deleting for topic - {mirrored_topic}")
        elif topic_response.status_code == 401:
            logger.error("Authorization issue")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization issue")
        elif topic_response.status_code == 500:
            logger.error("Internal server error")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")
        else:
            logger.error("unable to fetch topic_name")
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail=f"Resource Conflict,{topic_name} is not exist")

    except Exception as e:
        logger.exception(f'Error occurred while connecting to delete endpoint :{e}')
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


# Helper function to perform HTTP GET requests with headers
def perform_get_request(url: str) -> any:
    headers = {"accept": "application/json", "Authorization": f"Bearer {do_auth()}"}
    response = requests.get(url, headers=headers)
    response.raise_for_status()  # Raise an exception for non-2xx status codes
    return response


@router.post("/start/mirror-maker/{subscription_name}", status_code=200)
def start_mirror_maker_service(subscription_name: str) -> Any:
    subscription_url = urllib.parse.urljoin(SERVICE_URL, f"subscriptions/{subscription_name}")
    # process_id = find_mirror_maker_process()

    logger.info(f"subscription_url:{subscription_url}")
    topic_url = urllib.parse.urljoin(SERVICE_URL, "topics/")
    bus_url = urllib.parse.urljoin(SERVICE_URL, "fed_kafka_bus/get_by_name/")
    auth_url = urllib.parse.urljoin(SERVICE_URL, "auth_type/get_record_by/")

    logger.info(f"subscription_url:{subscription_url}")
    subscription_response = perform_get_request(subscription_url)
    logger.info(f"subscription status and response {subscription_response.status_code}:{subscription_response}")
    response = subscription_response.json()
    source_topic_name = response["topic"]
    logger.info(f"source_topic_name:{source_topic_name}")
    if subscription_response.status_code == 200:
        try:
            logger.info(f"Topic_name:{source_topic_name}")
            topic_url = topic_url + str(source_topic_name)
            logger.info(f"topic_url:{topic_url}")
            topic_response = perform_get_request(topic_url)
            logger.info(f"topic_url_status_code:{topic_response.status_code}:{topic_response}")

            if topic_response.status_code == 200:
                topic_json = topic_response.json()
                logger.info(f"topic_json:{topic_json}")
                bus_name = topic_json["kafka_bus_name"]
                if bus_name not in TINAA_KAFKA_LIST:
                    logger.info(f" Bus name: {bus_name} is not TINAA kafka, proceeding for mirroring")
                    bus_url = bus_url + topic_json["kafka_bus_name"]
                    logger.info(f"bus_url: {bus_url}")
                    bus_response = perform_get_request(bus_url)
                    logger.info(
                        f"bus_response :{bus_response.json()}:{bus_response.status_code}")
                    bus_response = bus_response.json()
                    auth_type = bus_response["kafka_auth_type"]
                    logger.info(f"auth_type is: {auth_type}")
                    if auth_type is not None:
                        auth_url = auth_url + str(auth_type) + "/" + str(bus_name)
                        logger.info(f"auth_url: {auth_url}")
                        auth_response = perform_get_request(auth_url)
                        if auth_response.status_code == 200:
                            logger.info(
                                f"auth_response: {auth_response.status_code}:{auth_response.json()}"
                            )
                        elif auth_response.status_code == 404:
                            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

                        elif auth_response.status_code == 500:
                            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                                detail="Internal server error")
                        else:
                            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

                    else:
                        auth_response = None

                    logger.info(f"bus_response - {bus_response}")
                    kafka_host = bus_response["kafka_host"]
                    kafka_port = bus_response["kafka_port"]

                    logger.info(
                        f"host: {kafka_host}, port: {kafka_port} and topic_name: {source_topic_name}"
                    )
                    bootstrap_servers = ",".join(
                        [f"{host}:{kafka_port}" for host in kafka_host]
                    )
                    logger.info(f"bootstrap_servers: {bootstrap_servers}")

                    consumer_filename = _configure_consumer_properties(
                        bootstrap_servers, kafka_host, auth_type, str(auth_response)
                    )
                    connect_filename = _configure_connect_mirror_maker_properties(
                        bootstrap_servers, source_topic_name, kafka_host, auth_type, auth_response
                    )
                    destination_topic_name = create_topic_and_start_mirror_maker(
                        source_topic_name, consumer_filename, connect_filename, bus_name
                    )

                    if not destination_topic_name:
                        return JSONResponse(status_code=500, content={"Error": "Internal server error"})

                    # Posting data into table for that we are creating post end point.

                    data = {
                        "process_id": 1,
                        "subscription_name": subscription_name,
                        "source_topic_name": source_topic_name,
                        "destination_topic_name": destination_topic_name,
                        "fed_kafka_bus_name": bus_name,
                        "auth_type": auth_type
                    }
                    headers = {'accept': 'application/json', 'Authorization': f'Bearer {do_auth}'}
                    create_mirror_maker_url = urllib.parse.urljoin(
                        SERVICE_URL, f"mirror_maker/create")

                    mirror_maker_response = requests.post(create_mirror_maker_url, headers=headers, json=data)
                    logger.info(f'mirror_maker_status_code:{mirror_maker_response.status_code}')

                    return {"result": f"Mirror maker functionality enabled for topic - {source_topic_name}"}
                else:
                    logger.info("Topic is part of tinaa Kafka, hence skip mirroring")
                    return {"result": "Topic is part of tinaa Kafka, hence skip mirroring"}

            elif topic_response.status_code == 401:
                logger.error("Authorization issue")
                return JSONResponse(
                    content={"status_code": 401, "message": "Authorization issue"},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )
            elif topic_response.status_code == 500:
                logger.error("Internal server error")
                return JSONResponse(content={'status-code': 500, "message": "Internal server error"},
                                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, )
            else:
                logger.error("unable to fetch topic_name")
                return JSONResponse(content={'status-code': 409,
                                             "message": f"Resource Conflict, topic - {source_topic_name} is not exist"},
                                    status_code=status.HTTP_409_CONFLICT, )

        except Exception as err:
            logger.exception(
                f"Error while creating kafka connect for topic - {source_topic_name}:{str(err)}"
            )
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail=f"Error while creating kafka connect for topic - {source_topic_name}")

    elif subscription_response.status_code == 401:
        logger.error("Authorization issue")
        return JSONResponse(
            content={"status_code": 401, "message": "Authorization issue"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    elif subscription_response.status_code == 404:
        logger.error("No subscription found for the provided subscription_name")
        return JSONResponse(
            content={
                "status_code": 404,
                "message": f"No subscription found for the provided SubscriptionName - {subscription_name}",
            },
            status_code=status.HTTP_404_NOT_FOUND,
        )
    elif subscription_response.status_code == 500:
        logger.error("Internal server error")
        return JSONResponse(
            content={"status_code": 500, "message": "Internal server error"},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    else:
        logger.error("Unexpected subscription API response")
        return JSONResponse(
            content={
                "status_code": 500,
                "message": "Unexpected subscription API response",
            },
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/", status_code=200)
def get_all_mirror_maker_records():
    try:
        # Expected to create Mirror_maker url to fetch all mirror_maker_details
        mirror_maker_url = urllib.parse.urljoin(SERVICE_URL, f"mirror-maker/")
        logger.info(f'mirror_maker_url: {mirror_maker_url}')
        mirror_maker_response = perform_get_request(mirror_maker_url)
        logger.info(f'mirror_maker_response:{mirror_maker_response.status_code}:{mirror_maker_response}')

        mirrored_topics = []
        if mirror_maker_response:
            for mirror_maker_topic in mirror_maker_response:
                mirrored_topics.append(mirror_maker_topic)
                return {"Retrieved mirror maker records successfully": mirrored_topics}
        else:
            logger.error("Records not found")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mirror maker record not found")
    except Exception as e:
        logger.exception(f"Error occurred while fetching mirror maker records from db {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


@router.get("/by-subscription/{subscription_name}", status_code=200)
def get_mirror_maker_record_by_subscription_name(subscription_name=Field(alias="SubscriptionName")):
    try:
        # Expected to create Mirror_maker url
        mirror_maker_url = urllib.parse.urljoin(SERVICE_URL,
                                                f"mirror-maker/by_subscription_name/{subscription_name}")
        logger.info(f'mirror_maker_url: {mirror_maker_url}')
        mirror_maker_response = perform_get_request(mirror_maker_url)
        logger.info(f'worker_status_code:{mirror_maker_response.status_code}')

        if mirror_maker_response.status_code == 200:
            mirror_maker_db_task = mirror_maker_response.json()
            logger.info(f'mirror_maker_db_task: {mirror_maker_db_task}')
            return {f"Mirror maker details successfully fetched by providing subscription name ": mirror_maker_db_task}
        else:
            raise Exception(
                f'Failed to fetch mirror_maker_details by providing subscription name : {mirror_maker_response.text}')
    except Exception as e:
        logger.info(f'Failed to fetch mirror_maker_details by providing subscription name {str(e)}')


@router.get("/by-id/{id}", status_code=200)
def get_mirror_maker_record_by_id(id: int):
    try:
        # Expected to create Mirror_maker url
        mirror_maker_url = urllib.parse.urljoin(SERVICE_URL, f"mirror-maker/by_id/{id}")
        logger.info(f'mirror_maker_url: {mirror_maker_url}')
        mirror_maker_response = perform_get_request(mirror_maker_url)
        logger.info(f'worker_status_code:{mirror_maker_response.status_code}')

        if mirror_maker_response.status_code == 200:
            mirror_maker_db_task = mirror_maker_response.json()
            logger.info(f'mirror_maker_db_task: {mirror_maker_db_task}')
            return {f"Mirror maker details successfully fetched by providing id ": mirror_maker_db_task}
        else:
            raise Exception(f'Failed to fetch mirror_maker_details by providing id : {mirror_maker_response.text}')
    except Exception as e:
        logger.info(f'Failed to fetch mirror_maker_details by providing id {e}')


@router.put("/by-id/{id}", status_code=201)
def update_mirror_maker(id: int):
    try:
        mirror_maker_update_url = urllib.parse.urljoin(SERVICE_URL,
                                                       f"mirror-maker/by_id/{id}")
        logger.info(f'mirror_maker_update_url: {mirror_maker_update_url}')
        headers = {'accept': 'application/json',
                   'Authorization': 'Bearer {}'.format(do_auth())}
        payload = {'status': status}
        worker_response = requests.put(mirror_maker_update_url, headers=headers, json=payload)
        logger.info(f'worker_status_code:{worker_response.status_code}')

        if worker_response.status_code == 200:
            inactive_db_task = worker_response.json()
            logger.info(f'inactive_db_task: {inactive_db_task}')
            return inactive_db_task
        else:
            raise Exception(f'Failed to update mirror_maker: {worker_response.text}')
    except Exception as e:
        logger.info(f'Failed to update Celery mirror_maker: {e}')


@router.delete("/by-id/{id}", status_code=200)
def delete_mirror_maker_record_by_id(id: int):
    try:
        # Expected to create Mirror_maker url
        delete_mirror_maker_url = urllib.parse.urljoin(SERVICE_URL, f"mirror-maker/by-id/{id}")
        logger.info(f'mirror_maker_url: {delete_mirror_maker_url}')
        headers = {'accept': 'application/json',
                   'Authorization': 'Bearer {}'.format(do_auth())}
        mirror_maker_response = requests.delete(delete_mirror_maker_url, headers=headers, json={})
        logger.info(f'worker_status_code:{mirror_maker_response.status_code}')

        if mirror_maker_response.status_code == 200:
            mirror_maker_db_task = mirror_maker_response.json()
            logger.info(f'mirror_maker_db_task: {mirror_maker_db_task}')
            return {f"Mirror maker details successfully fetched by providing ID ": mirror_maker_db_task}
        else:
            raise Exception(f'Failed to fetch mirror_maker_details by providing ID : {mirror_maker_response.text}')
    except Exception as e:
        logger.info(f'Failed to fetch mirror_maker_details by providing ID {e}')

# if __name__ == "__main__":
#      uvicorn.run(router, host="0.0.0.0", port=MM_SERVICE_PORT)
