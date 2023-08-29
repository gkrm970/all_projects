import configparser
import functools
import os
import subprocess
import time
import urllib.parse
import ast

import requests
from confluent_kafka.admin import AdminClient, NewTopic
from flask import Flask, jsonify
from tinaa.utils.v1.auth_handler import AuthHandler

from logger import logger

app = Flask(__name__)
app.config["VERIFY_CERTIFICATES"] = False

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
# SERVICE_URL = f"{SERVICE_PROTOCOL}://{SERVICE_HOST}:{SERVICE_PORT}{SERVICE_ENDPOINT}"
SERVICE_URL = urllib.parse.urljoin(
    f"{SERVICE_PROTOCOL}://{SERVICE_HOST}:{SERVICE_PORT}", SERVICE_ENDPOINT
)

TINAA_KAFKA_LIST = os.getenv("TINAA_KAFKA_LIST", "")


def do_auth() -> str:
    logger.info("inside do auth")
    logger.info(f'config obj is: {auth_config["oauth2"]}')
    logger.debug(
        f"Auth config to get token is : {AUTH_TOKEN_URL}, {CLIENT_ID}, {CLIENT_SECRET}"
    )
    token = AUTH_HANDLER_OBJ.get_access_token()
    logger.info(f"token is : {token}")

    return token


def _get_token(config_str):
    """Note here value of config comes from sasl.oauthbearer.config below.
    It is not used in this example but you can put arbitrary values to
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


oauth_configuration = {
    "bootstrap.servers": "pltf-msgbus.develop.ocp01.toll6.tinaa.tlabs.ca:443",
    "security.protocol": "SASL_SSL",
    "sasl.mechanisms": "OAUTHBEARER",
    "enable.ssl.certificate.verification": False,
    "group.id": CLIENT_ID,
    "oauth_cb": functools.partial(_get_token)
}


def create_topic_and_start_mirrormaker(
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
            f"{ast.literal_eval(bus_name)[0]}.{str(source_topic_name)}", num_partitions=3, replication_factor=3
        )
    ]
    logger.info(f"new_topic_name:{new_topic}")
    futures = admin_client.create_topics(new_topic)
    logger.info(f"futures:{futures}")
    for destination_topic_name, future in futures.items():
        try:
            future.result()  # The result itself is None
            logger.info("Topic {} created".format(destination_topic_name))
            logger.info(f"new_topic_name:{destination_topic_name}")
            child = subprocess.Popen(
                [
                    "sh",
                    "./runMirrorMaker.sh",
                    connect_filename,
                    consumer_filename,
                    source_topic_name,
                    destination_topic_name,
                ]
            )
            returnCode = child.poll()
            logger.info(f"returnCode - {returnCode}")
        except Exception as e:
            logger.error(
                "Failed to create topic {}: {}".format(destination_topic_name, e)
            )
        return destination_topic_name
    admin_client.poll(timeout=10)


def _configure_consumer_properties(bootstrap_servers: str, kafka_host: str, auth_type, auth_response) -> str:
    if auth_type is None:
        config = configparser.ConfigParser()
        config.read("config/consumer.properties")
        logger.info("inside the consumer proprties")
        # set the consumer properties file
        config.set("consumer", "bootstrap.servers", bootstrap_servers)
        config.set("consumer", "exclude.internal.topics", "true")
        config.set("consumer", "group.id", "mirror_maker_consumer")
        consumer_filename = "consumer" + str(kafka_host) + ".properties"
        with open(consumer_filename, "w") as configfile:
            config.write(configfile)

        return consumer_filename
    elif auth_type == "OAUTHBEARER":
        config = configparser.ConfigParser()
        config.read("config/consumer.properties")
        logger.info("inside the consumer proprties")
        # set the consumer properties file
        config.set("consumer", "bootstrap.servers", bootstrap_servers)
        config.set("consumer", "exclude.internal.topics", "true")
        config.set("consumer", "group.id", "mirror_maker_consumer")
        config.set("consumer", "client_id", CLIENT_ID)
        config.set("consumer", "client_secret", CLIENT_SECRET)
        config.set("consumer", "oauth_cb", oauth_configuration['oauth_cb'])

        consumer_filename = "OAUTH_BEARER_consumer" + str(kafka_host) + ".properties"
        with open(consumer_filename, "w") as configfile:
            config.write(configfile)
    elif auth_type == "SSL":
        config = configparser.ConfigParser()
        config.read("config/consumer.properties")
        logger.info("inside the consumer proprties")
        # set the consumer properties file
        config.set("consumer", "bootstrap.servers", bootstrap_servers)
        config.set("consumer", "exclude.internal.topics", "true")
        config.set("consumer", "group.id", "mirror_maker_consumer")
        config.set("consumer", "ssl.truststore.location", auth_response['ssl_truststore_location'])
        config.set("consumer", "ssl.truststore.password", auth_response['ssl_truststore_password'])

        consumer_filename = "SSL_consumer" + str(kafka_host) + ".properties"
        with open(consumer_filename, "w") as configfile:
            config.write(configfile)


def _configure_connect_mirror_maker_properties(
        bootstrap_servers: str, source_topic_name: str, kafka_host: str, auth_type: str, auth_response
) -> str:
    if auth_type is None:
        clusters = "source", "destination"
        config = configparser.ConfigParser()
        config.read("config/connect-mirror-maker.properties")
        logger.info("inside connecet mirror maker property file")
        config.set("connect-mirrormaker_endpoints", "clusters", str(clusters))
        config.set("connect-mirrormaker_endpoints", "source.bootstrap.servers", bootstrap_servers)
        config.set(
            "connect-mirrormaker_endpoints",
            "destination.bootstrap.servers",
            "pltf-msgbus-sasl.develop.ocp01.toll6.tinaa.tlabs.ca:443",
        )

        config.set(
            "connect-mirrormaker_endpoints", "destination.config.storage.replication.factor", "3"
        )
        config.set(
            "connect-mirrormaker_endpoints", "destination.offset.storage.replication.factor", "3"
        )
        config.set(
            "connect-mirrormaker_endpoints", "destination.status.storage.replication.factor", "3"
        )
        config.set("connect-mirrormaker_endpoints", "source->destination.enabled", "true")
        config.set("connect-mirrormaker_endpoints", "destination->source.enabled", "false")

        config.set("connect-mirrormaker_endpoints", "source->destination.topics", source_topic_name)
        connect_filename = "connect-mirror-maker" + str(kafka_host) + ".properties"
        logger.info(f"config:{config}")
        with open(connect_filename, "w") as configfile:
            config.write(configfile)

        return connect_filename

    elif auth_type == "OAUTHBEARER":
        clusters = "source", "destination"
        config = configparser.ConfigParser()
        config.read("config/connect-mirror-maker.properties")
        logger.info("inside connect mirror maker property file")
        config.set("connect-mirrormaker_endpoints", "clusters", str(clusters))
        config.set("connect-mirrormaker_endpoints", "source.bootstrap.servers", bootstrap_servers)
        config.set(
            "connect-mirrormaker_endpoints",
            "destination.bootstrap.servers",
            "pltf-msgbus-sasl.develop.ocp01.toll6.tinaa.tlabs.ca:443",
        )

        config.set(
            "connect-mirrormaker_endpoints", "destination.config.storage.replication.factor", "3"
        )
        config.set(
            "connect-mirrormaker_endpoints", "destination.offset.storage.replication.factor", "3"
        )
        config.set(
            "connect-mirrormaker_endpoints", "destination.status.storage.replication.factor", "3"
        )
        config.set("connect-mirrormaker_endpoints", "source->destination.enabled", "true")
        config.set("connect-mirrormaker_endpoints", "destination->source.enabled", "false")

        config.set("connect-mirrormaker_endpoints", "source->destination.topics", source_topic_name)
        config.set("connect-mirrormaker_endpoints", "client_id", CLIENT_ID)
        config.set("connect-mirrormaker_endpoints", "client_secret", CLIENT_SECRET)
        config.set("connect-mirrormaker_endpoints", "security.protocol", auth_response["oauth_security_protocol"])
        config.set("connect-mirrormaker_endpoints", "sasl.mechanism", auth_response["oauth_security_mechanism"])
        config.set("connect-mirrormaker_endpoints", "ssl.certificate.location", auth_response["oauth_issuer"])
        config.set("connect-mirrormaker_endpoints", "ssl.ca.location", auth_response["oauth_issuer"])
        config.set("connect-mirrormaker_endpoints", "oauth_cb", oauth_configuration['oauth_cb'])
        config.set("connect-mirrormaker_endpoints", "offset-syncs.topic.replication.factor", "3")
        config.set("connect-mirrormaker_endpoints", "heartbeats.topic.replication.factor", "3")
        config.set("connect-mirrormaker_endpoints", "checkpoints.topic.replication.factor", "3")
        config.set("connect-mirrormaker_endpoints", "tasks.max", "1")
        config.set("connect-mirrormaker_endpoints", "replication.factor", "3")
        config.set("connect-mirrormaker_endpoints", "refresh.topics.enabled", "true")
        config.set("connect-mirrormaker_endpoints", "sync.topic.configs.enabled", "true")

        # Enable heartbeats and checkpoints.
        config.set(
            "connect-mirrormaker_endpoints", "source->destination.emit.heartbeats.enabled", "true"
        )
        config.set(
            "connect-mirrormaker_endpoints", "source->destination.emit.checkpoints.enabled", "true"
        )
        connect_oauth_bearer_filename = "OAUTH_BEARER_connect-mirror-maker" + str(kafka_host) + ".properties"
        logger.info(f"config:{config}")
        with open(connect_oauth_bearer_filename, "w") as configfile:
            config.write(configfile)

        return connect_oauth_bearer_filename

    elif auth_type == "SSL":
        clusters = "source", "destination"
        config = configparser.ConfigParser()
        config.read("config/connect-mirror-maker.properties")
        logger.info("inside connecet mirror maker property file")
        config.set("connect-mirrormaker_endpoints", "clusters", str(clusters))
        config.set("connect-mirrormaker_endpoints", "source.bootstrap.servers", bootstrap_servers)
        config.set(
            "connect-mirrormaker_endpoints",
            "destination.bootstrap.servers",
            "pltf-msgbus-sasl.develop.ocp01.toll6.tinaa.tlabs.ca:443",
        )

        config.set(
            "connect-mirrormaker_endpoints", "destination.config.storage.replication.factor", "3"
        )
        config.set(
            "connect-mirrormaker_endpoints", "destination.offset.storage.replication.factor", "3"
        )
        config.set(
            "connect-mirrormaker_endpoints", "destination.status.storage.replication.factor", "3"
        )
        config.set("connect-mirrormaker_endpoints", "source->destination.enabled", "true")
        config.set("connect-mirrormaker_endpoints", "destination->source.enabled", "false")

        config.set("connect-mirrormaker_endpoints", "source->destination.topics", source_topic_name)
        config.set("connect-mirrormaker_endpoints", "client_id", CLIENT_ID)
        config.set("connect-mirrormaker_endpoints", "client_secret", CLIENT_SECRET)
        config.set("connect-mirrormaker_endpoints", "security.protocol", "SSL")
        config.set("connect-mirrormaker_endpoints", "sasl.mechanism", 'SSL')

        # Enable heartbeats and checkpoints.
        config.set(
            "connect-mirrormaker_endpoints", "source->destination.emit.heartbeats.enabled", "true"
        )
        config.set(
            "connect-mirrormaker_endpoints", "source->destination.emit.checkpoints.enabled", "true"
        )

        config.set("connect-mirrormaker_endpoints", "ssl.truststore.location", auth_response['ssl_truststore_location'])
        config.set("connect-mirrormaker_endpoints", "ssl.truststore.password", auth_response['ssl_truststore_password'])
        config.set("connect-mirrormaker_endpoints", "offset-syncs.topic.replication.factor", "3")
        config.set("connect-mirrormaker_endpoints", "heartbeats.topic.replication.factor", "3")
        config.set("connect-mirrormaker_endpoints", "checkpoints.topic.replication.factor", "3")
        config.set("connect-mirrormaker_endpoints", "tasks.max", "1")
        config.set("connect-mirrormaker_endpoints", "replication.factor", "3")
        config.set("connect-mirrormaker_endpoints", "refresh.topics.enabled", "true")
        config.set("connect-mirrormaker_endpoints", "sync.topic.configs.enabled", "true")

        # Enable heartbeats and checkpoints.
        config.set(
            "connect-mirrormaker_endpoints", "source->destination.emit.heartbeats.enabled", "true"
        )
        config.set(
            "connect-mirrormaker_endpoints", "source->destination.emit.checkpoints.enabled", "true"
        )
        connect_filename = "SSL_connect-mirror-maker" + str(kafka_host) + ".properties"

        logger.info(f"config:{config}")
        with open(connect_filename, "w") as configfile:
            config.write(configfile)

        return connect_filename


@app.route("/delete/mirror-maker/<topic_name>", methods=["DELETE"])
def delete_topic(topic_name):
    topic_url = urllib.parse.urljoin(SERVICE_URL, "topics/")
    topic_url = topic_url + str(topic_name)
    logger.info(f"topic_url:{topic_url}")
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {do_auth()}",
    }

    topic_response = requests.get(topic_url, headers=headers)
    logger.info(
        f"topic_url_status_code:{topic_response.status_code}:{topic_response}"
    )

    if topic_response.status_code == 200:
        topic_json = topic_response.json()
        logger.info(f"topic_json:{topic_json}")
        bus_name = topic_json["kafka_bus_name"]
        bus_name_txt = ast.literal_eval(bus_name)[0]
        mirror_topic = f'{bus_name_txt}.{topic_name}'
        delete_topic_url = urllib.parse.urljoin(SERVICE_URL, "topics/")
        delete_topic_url = delete_topic_url + str(mirror_topic)
        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {do_auth()}",
        }
        delete_response = requests.delete(delete_topic_url, headers=headers)
        if delete_response == 200:
            logger.info(f"topic_name:{mirror_topic} deleted successfully")
        else:
            logger.info(f"topic_name:{mirror_topic} is not deleted")


@app.route("/start/mirror-maker/<SubscriptionName>", methods=["POST"])
def start_mirror_maker(SubscriptionName):
    logger.info(f"subscription_name:{SubscriptionName}")
    subscription_url = urllib.parse.urljoin(
        SERVICE_URL, f"subscriptions/{SubscriptionName}"
    )

    logger.info(f"subscription_url:{subscription_url}")
    topic_url = urllib.parse.urljoin(SERVICE_URL, "topics/")
    bus_url = urllib.parse.urljoin(SERVICE_URL, "fed_kafka_bus/get_by_name/")
    auth_url = urllib.parse.urljoin(SERVICE_URL, "auth_type/get_record_by/")

    headers = {"accept": "application/json", "Authorization": f"Bearer {do_auth()}"}
    logger.info(f"subscription_url:{subscription_url}")
    subscription_response = requests.get(subscription_url, headers=headers)
    logger.info(f"subscription_status_code:{subscription_response.status_code}")
    response = subscription_response.json()
    source_topic_name = response["topic"]
    logger.info(f"source_topic_namee:{source_topic_name}")
    if subscription_response.status_code == 200:
        try:
            logger.info(f"Topic_name:{source_topic_name}")
            topic_url = topic_url + str(source_topic_name)
            logger.info(f"topic_url:{topic_url}")
            headers = {
                "accept": "application/json",
                "Authorization": f"Bearer {do_auth()}",
            }

            topic_response = requests.get(topic_url, headers=headers)
            logger.info(
                f"topic_url_status_code:{topic_response.status_code}:{topic_response}"
            )

            if topic_response.status_code == 200:
                topic_json = topic_response.json()
                logger.info(f"topic_json:{topic_json}")
                bus_name = topic_json["kafka_bus_name"]
                if TINAA_KAFKA_LIST not in bus_name:
                    logger.info(
                        f" Bus name: {bus_name} is not TINAA kafka, proceeding for mirroring"
                    )
                    bus_url = bus_url + str(topic_json["kafka_bus_name"])
                    logger.info(f"bus_url: {bus_url}")
                    headers = {
                        "accept": "application/json",
                        "Authorization": f"Bearer {do_auth()}",
                    }
                    bus_response = requests.get(bus_url, headers=headers)
                    logger.info(
                        f"bus_url_status_code:{bus_response.status_code}:{bus_response.json()}"
                    )

                    bus_response = bus_response.json()
                    auth_type = bus_response["kafka_auth_type"]
                    logger.info(f"auth_type: {auth_type}")
                    if auth_type is not None:
                        auth_url = auth_url + str(auth_type) + str(bus_name)
                        logger.info(f"auth_url: {auth_url}")
                        headers = {
                            "accept": "application/json",
                            "Authorization": f"Bearer {do_auth()}",
                        }
                        auth_response = requests.get(auth_url, headers=headers)
                        logger.info(
                            f"auth_url_status_code: {auth_response.status_code}:{auth_response.json()}"
                        )
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
                        bootstrap_servers, kafka_host, auth_type, auth_response
                    )
                    connect_filename = _configure_connect_mirror_maker_properties(
                        bootstrap_servers, source_topic_name, kafka_host, auth_type, auth_response
                    )
                    create_topic_and_start_mirrormaker(
                        source_topic_name, consumer_filename, connect_filename, bus_name
                    )

                    return jsonify(
                        {
                            "status_code": 200,
                            "message": f"Mirror maker functionality enabled for topic - {source_topic_name}",
                        }
                    )
                else:
                    logger.info("Topic is part of TINAA Kafka, hence skip mirrorring")
                    return jsonify(
                        {
                            "status_code": 200,
                            "message": f"Topic - {source_topic_name} is part of TINAA Kafka, hence skip "
                                       f"mirrorring",
                        }
                    )
            elif topic_response.status_code == 401:
                logger.error("Authorization issue")
                return jsonify({"status_code": 401, "message": "Authorization issue"})
            elif topic_response.status_code == 500:
                logger.error("Internal server error")
                return jsonify({"status_code": 500, "message": "Internal server error"})
            else:
                logger.error("unable to fetch topic_name")
                return jsonify(
                    {
                        "status_code": 409,
                        "message": f"Resource Conflict, topic - {source_topic_name} is not exist",
                    }
                )
        except Exception as err:
            logger.error(
                f"Error while creating kafka connect for topic - {source_topic_name}:{err}"
            )
            logger.info(f"{str(err)}")
            return jsonify(
                {
                    "status_code": 500,
                    "message": f"Error while creating kafka connect for topic - {source_topic_name}",
                }
            )
    elif subscription_response.status_code == 401:
        logger.error("Authorization issue")
        return jsonify({"status_code": 401, "message": "Authorization issue"})
    elif subscription_response.status_code == 404:
        logger.error("No subscription found for the provided SubscriptionName")
        return jsonify(
            {
                "status_code": 404,
                "message": f"No subscription found for the provided SubscriptionName - {SubscriptionName}",
            }
        )
    elif subscription_response.status_code == 500:
        logger.error("Internal server error")
        return jsonify({"status_code": 500, "message": "Internal server error"})
    else:
        logger.error("Unexpected subscription API response")
        return jsonify(
            {"status_code": 500, "message": "Unexpected subscription API response"}
        )


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=os.getenv("DEBUG", False), port=MM_SERVICE_PORT)
