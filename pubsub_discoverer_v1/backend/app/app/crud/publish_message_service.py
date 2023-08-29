import base64, binascii
import functools
import time
import os
import uuid
import json
import socket
import fastavro
import requests
from avro import schema
from confluent_kafka import Producer
from confluent_kafka.serialization import StringSerializer, SerializationContext, MessageField
from fastavro._schema_common import SchemaParseException
from starlette.responses import JSONResponse
from urllib3 import HTTPSConnectionPool
import paramiko
from app.api.utils.exception_handler import TopicNotFoundException
from app.api.utils.utils import infer_avro_schema, get_current_utc_ascii_timestamp
from app.schemas.publish_message_schema import Messages
from app.models.models import FedKafkaBuses, SchemaRegistry
from app.models.models import AuthType, GSSAPIAuthenticationInfo, KeycloakAuthenticationInfo, LDAPAuthenticationInfo, OAuthBearerAuthenticationInfo, PlainTextAuthenticationInfo, SASLAuthenticationInfo, SSLAuthenticationInfo, ScramAuthenticationInfo, KerberosAuthenticationInfo, FedKafkaBuses
from app.api.utils.enums import AuthTypeEnum
from sqlalchemy.orm import Session

from app.core.config import settings
from tinaa.logger.v1.tinaa_logger import get_app_logger

from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.schema_registry.protobuf import ProtobufSerializer
from confluent_kafka.schema_registry.json_schema import JSONSerializer
from confluent_kafka.schema_registry import SchemaRegistryClient, SchemaRegistryError

API_NAME = "Publish Service"
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


def acked(err, msg):
    if err is not None:
        logger.error("Failed to deliver message: %s: %s" % (str(msg), str(err)))
    else:
        logger.info("Message produced: %s" % (str(msg)))


def _get_token(args, config_str):
    """Note here value of config comes from sasl.oauthbearer.config below.
        It is not used in this example but you can put arbitrary values to
        configure how you can get the token (e.g. which token URL to use)
    """
    resp = requests.post(args[1], data=args[0], verify=False)
    token = resp.json()
    logger.info(f'token------------------ {token["access_token"]}')
    return token['access_token'], time.time() + float(token['expires_in'])


def write_payload_to_kafka(topic_name, json_data):
    logger.debug("Writing to kafka")
    try:
        producer = Producer(
            bootstrap_servers=["localhost:9092"],
            value_serializer=lambda m: json.dumps(m).encode('ascii')
        )
        producer.send(topic_name, json_data)

        logger.debug("Message sent to Kafka topic:", topic_name)
        logger.debug('Writing to kafka Completed')

    except NoBrokersAvailable as e:
        logger.error(f'Error in getting data from Kafka: {str(e)}')
        logger.error('Writing to kafka FAILED')


def timestamp(uuid_16, msg):
    out_dict = {}
    time_str = get_current_utc_ascii_timestamp()
    out_dict['Timestamp'] = time_str
    out_dict['messageId'] = uuid_16
    out_dict['data'] = msg
    return out_dict


class PublishMessage:

    @staticmethod
    def create(TopicName: str, info_schema: Messages, db: Session, schema_definition: str, bus_name: str):
        logger.info(f"Publishing message to the Kafka bus: {bus_name}")

        try:
            logger.info('Validating all messages are encoded in base64')
            data_values = [message.data for message in info_schema.messages]
            for msg in data_values:
                try:
                    is_valid = base64.b64decode(msg, validate=True)
                except binascii.Error as e:
                     error = {
                        "error": {
                             "code": 400,
                             "message": f"Message data not encrypted",
                             "status": "error",
                             "details": {
                                 "reason": f"Detail description: Message should be base64 encoded format",
                                 "type": "Data not encoded"}
                         }
                     }
                     return JSONResponse(content=error, status_code=400)

            logger.info(f'Fetching bus details associated to topic - {TopicName}')
            bus_info = db.query(FedKafkaBuses).filter(FedKafkaBuses.kafka_host == bus_name).first()

            if bus_info is None:
                raise Exception(f"FedKafkaBus with the host {bus_name} does not exist in the DB")

            kafka_host = bus_info.kafka_host
            kafka_port = bus_info.kafka_port
            auth_type = bus_info.kafka_auth_type
            schema_registry_id = bus_info.schema_registry_id

            if auth_type == "SSL":
                logger.info("Auth type is SSL, making schema_definition as empty")
                schema_definition = ""

            if schema_definition:
                logger.info(f'Schema for topic - {TopicName} is - {schema_definition}, '
                            f'proceeding message validation with schema')
                for msg in data_values:
                    logger.info(f'Verifying each message with schema associated')
                    logger.info(f"converting encoded data into decoded bytes")
                    decoded_bytes = base64.b64decode(msg)
                    decoded_string = decoded_bytes.decode('utf-8')
                    logger.info(f"decoded string: {decoded_string}")
                    json_data = json.loads(decoded_string)
                    #json_data_list.append(json_data)
                    logger.info(f"converted decoded string into json data: {json_data}")
                    #avro_schema = infer_avro_schema(json_data, TopicName)

                    #logger.info(f'Compare schema generated from message with schema definition')
                    logger.info(f'schema definition in DB - {schema_definition}')
                    #logger.info(f'type of schema definition in DB is - {type(schema_definition)}')
                    #logger.info(f'type of message schema definition is - {type(avro_schema)}')

                    schema_definition_dict = json.loads(schema_definition)
                    # Compile the Avro schema
                    logger.info(f'Compiling avro schema ')
                    schema = fastavro.parse_schema(schema_definition_dict)

                    try:
                        logger.info('Validating the message against schema')
                        fastavro.validate(json_data, schema)
                        logger.info('Message is with valid schema')
                    except fastavro.validation.ValidationError as e:
                        logger.error(f'Schema for message is not valid - {str(e)}')
                        raise SchemaParseException

                    #if fastavro.parse_schema(schema_definition_dict) and fastavro.parse_schema(avro_schema):
                    #    logger.info("Both schemas are valid")
                        # Setting name field to dummy since avro isn't accept special characters
                        # Ex: {"type":"record","name":"pltf-develop-test","fields":[{"name":"color","type":"string"}]}
                    #    avro_schema['name'] = 'dummy'
                    #    avro_schema_definition = schema.parse(json.dumps(avro_schema))
                    #    avro_schema_dict = avro_schema_definition.to_json()
                    #    if schema_definition_dict['fields'] == avro_schema_dict['fields']:
                    #        logger.info(f'Schema definition and generated schema for message - {msg} are same')
                    #    else :
                    #        logger.error(f'Messsage - {msg} is not alligned with associated schema definition')
                    #        raise SchemaParseException
                    # else: 
                    #    logger.error(f'Schema for message is not valid')
                    #    raise SchemaParseException

            logger.info(f'Proceeding to publish messages to topic - {TopicName}')

            bootstrap_servers = ",".join(
                [f"{host}:{kafka_port}" for host in kafka_host]
            )

            logger.info(f'Fetching Authentication details for bus - {bus_name}')
            if auth_type == AuthTypeEnum.OAUTHBEARER.value:
                auth_info_obj = db.query(AuthType).filter(AuthType.auth_type == auth_type).filter(AuthType.fed_kafka_bus_name == bus_name).first()
                if auth_info_obj:
                    logger.info(f'Bus - {bus_name} is registered with {auth_type} authentication')
                    OAuth_id = auth_info_obj.OAuth_auth_info_id
                    OAUth_obj = db.query(OAuthBearerAuthenticationInfo).filter(OAuthBearerAuthenticationInfo.id == OAuth_id).first()
                    oauth_client_secret = OAUth_obj.oauth_client_secret
                    oauth_URL = OAUth_obj.oauth_URL
                    oauth_security_protocol = OAUth_obj.oauth_security_protocol
                    oauth_security_mechanism = OAUth_obj.oauth_security_mechanism
                    decoded_oauth_issuer = base64.b64decode(OAUth_obj.oauth_issuer)

                    certificate_filename = f"telus_tcso_ca_{kafka_host[0]}.pem.txt"
                    logger.info(f'certificate_filename - {certificate_filename}')
                    certificate_filepath = os.path.join("/tmp", certificate_filename)
                    logger.info(f'certificate_filepath - {certificate_filepath}')


                    with open(certificate_filepath, "wb") as file:
                        file.write(decoded_oauth_issuer)

                    #oauth_issuer = OAUth_obj.oauth_issuer
                    oauth_client_id = OAUth_obj.oauth_client_id

                    payload = {
                        'grant_type': 'client_credentials',
                        'client_id': oauth_client_id,
                        'client_secret': oauth_client_secret
                    }
                    token_payload_url_list = [payload, oauth_URL]
                    logger.info(f'token_payload_url_list is : {token_payload_url_list}')

                    conf = {
                        'bootstrap.servers': bootstrap_servers,
                        'security.protocol': oauth_security_protocol,
                        'debug': 'broker,admin,protocol',
                        # 'security.protocol': 'SASL_PLAINTEXT',
                        'sasl.mechanisms': auth_type,
                        #'ssl.certificate.location': oauth_issuer,
                        #'ssl.ca.location': oauth_issuer,
                        'ssl.certificate.location': certificate_filepath,
                        'ssl.ca.location': certificate_filepath,
                        'client.id': socket.gethostname(),
                        'compression.type':'none',
                        'enable.ssl.certificate.verification': False,
                        'group.id': oauth_client_id,
                        'oauth_cb': functools.partial(_get_token, token_payload_url_list)
                    }
                    logger.info(f'OAUTHBEARER kafka conf response - {conf}')
                else:
                    logger.error(f'Error while fetching authentication info for bus- {bus_name}')
                    raise BaseException

            # ssl start **************************************
            elif auth_type == AuthTypeEnum.SSL.value:
                auth_info_obj = db.query(AuthType).filter(AuthType.auth_type == auth_type).filter(
                    AuthType.fed_kafka_bus_name == bus_name).first()
                if auth_info_obj:
                    logger.info(f'Bus - {bus_name} is registered with {auth_type} authentication')
                    ssl_id = auth_info_obj.SSL_auth_info_id
                    ssl_obj = db.query(SSLAuthenticationInfo).filter(SSLAuthenticationInfo.id == ssl_id).first()
                    decoded_ssl_key_location = base64.b64decode(ssl_obj.ssl_key_location)
                    decoded_ssl_cert_location = base64.b64decode(ssl_obj.ssl_certificate_location)

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

                    #ssl_key_location = ssl_obj.ssl_key_location
                    #ssl_certificate_location = ssl_obj.ssl_certificate_location
                    ssl_key_password = ssl_obj.ssl_key_password
                    conf = {
                        'bootstrap.servers':bootstrap_servers,
                        'security.protocol':'SSL',
                        #'ssl.certificate.location':ssl_certificate_location,
                        'ssl.certificate.location':cert_filepath,
                        'ssl.key.password':ssl_key_password,
                        #'ssl.key.location':ssl_key_location,
                        'ssl.key.location':key_filepath,
                        'enable.ssl.certificate.verification':False,
                        'session.timeout.ms':6000,
                        'auto.offset.reset':'earliest',
                        'group.id': os.getenv('CLIENT_ID')
                    }

                schema_registry_model = db.query(SchemaRegistry).filter(
                    SchemaRegistry.id == schema_registry_id).first()

                if schema_registry_model:
                    ssl_publish_response = publish_for_ssl(TopicName, data_values, conf, schema_registry_model)
                    return ssl_publish_response
                else:
                    logger.info("Bus onboarded without schema registry, publishing message without serialization")

            else:
                logger.info(f'No authentication set for the bus - {bus_name}')
                conf = {'bootstrap.servers': bootstrap_servers, 'session.timeout.ms': 6000,
                        'client.id': socket.gethostname()}

            logger.info(f"Kafka producer configuration: {conf}")

            producer = Producer(conf)
            serializer = StringSerializer('utf_8')
            list_ids = []
            input_topic_name = TopicName
            logger.info(f"User_input: {input_topic_name}")
            logger.debug(f'Data values are : {data_values}')
            for msg in data_values:
                uuid_16 = str(uuid.uuid4().int)[:16]
                logger.info('Writing message using confluent kafka producer')
                logger.info(f'Message to publish is - {msg}')

                try:
                    record_key = uuid_16
                    record_value = msg
                    logger.info("Producing record: {}\t{}".format(record_key, record_value))
                    producer.produce(input_topic_name,
                                 key=record_key,
                                 value=record_value, on_delivery=acked)
                    producer.poll(0)
                    list_ids.append(uuid_16)
                except Exception as e:
                    logger.error('Issue while producing messages to topic - {topic_name}')
                    raise BaseException
                    return {"status": "Failed to produce messages to Kafka"}
            if producer.flush(timeout=10) == 0:
                logger.info("All messages sent successfully")
                return {"messageIds": list_ids}
            else:
                logger.error("Failed to send messages to Kafka")
                raise Exception

        except SchemaParseException as e:
            error = {
                "error": {
                    "code": 400,
                    "message": f"schema validation failed, can't publish message",
                    "status": "error",
                    "details": {
                        "reason": f"Detail description: schema definition format is - '{schema_definition}'",
                        "type": "Invalid Message format"}
                }
            }
            return JSONResponse(content=error, status_code=400)

        except TopicNotFoundException as e:
            error = {
                "error": {
                    "code": 400,
                    "message": f"Topic '{TopicName}' not found in the Kafka topic table list.",
                    "status": "error",
                    "details": {
                        "reason": f"Detail description: Create topic - '{TopicName}' to publish messages",
                        "type": "Topic Not Found"}
                }
            }
            return JSONResponse(content=error, status_code=400)

        except paramiko.AuthenticationException as e:
            error = {
                "error": {
                    "code": 401,
                    "message": "Authentication failed",
                    "status": "error",
                    "details": {
                        "reason": f"Detail description: {e}",
                        "type": "Unauthorized"}
                }
            }
            return JSONResponse(content=error, status_code=401)

        except Exception as e:
            error = {
               "error": {
                    "code": 500,
                    "message": "Unable to publish messages to Kafka",
                    "status": "error",
                    "details": {
                        "reason": f"Detail description: {e}",
                        "type": "Kafka Error"}
                }
            }
            return JSONResponse(content=error, status_code=500)

        except BaseException as e:
            error = {
                "error": {
                    "code": 500,
                    "message": f"Unexpected Error",
                    "status": "error",
                    "details": {
                        "reason": f"Detail description: {e}",
                        "type": "Unexpected Error"}
                }
            }
            return JSONResponse(content=error, status_code=500)


def delivery_report(err, msg):
    """
        Reports the failure or success of a message delivery.
        Args:
            err (KafkaError): The error that occurred on None on success.
            msg (Message): The message that was produced or failed.
        Note:
            In the delivery report callback the Message.key() and Message.value()
            will be the binary format as encoded by any configured Serializers and
            not the same object that was passed to produce().
            If you wish to pass the original object(s) for key and value to delivery
            report callback we recommend a bound callback or lambda where you pass
            the objects along.
        """

    if err is not None:
        logger.error("Failed to deliver message: %s: %s" % (str(msg), str(err)))
    else:
        logger.info('Record {} successfully produced to {} [{}] at offset {}'.format(
            msg.key(), msg.topic(), msg.partition(), msg.offset()))


def publish_for_ssl(TopicName, messages, conf, schema_registry_model):
    logger.info("Inside publish_for_ssl function")
    schema_registry_host = schema_registry_model.schema_registry_host
    schema_registry_port = schema_registry_model.schema_registry_port
    schema_registry_url = f"https://{schema_registry_host}:{schema_registry_port}"
    logger.info(f"Schema registry url- {schema_registry_url}")

    decoded_schema_registry_ssl_key = base64.b64decode(schema_registry_model.schemaregistry_key_location)
    decoded_schema_registry_ssl_cert = base64.b64decode(schema_registry_model.schemaregistry_certificate_location)
    schema_registry_ssl_key_location = "/tmp/api_created_client_schema_registry.ssl.key"
    schema_registry_ssl_certificate_location = "/tmp/api_created_client_schema_registry.ssl.crt"
    with open(schema_registry_ssl_key_location, 'wb') as file:
        file.write(decoded_schema_registry_ssl_key)
    with open(schema_registry_ssl_certificate_location, 'wb') as file:
        file.write(decoded_schema_registry_ssl_cert)

    schema_registry_conf = {'url': schema_registry_url,
                            "ssl.certificate.location": schema_registry_ssl_certificate_location,
                            "ssl.key.location": schema_registry_ssl_key_location}
    logger.info(f"Schema Registry conf- {schema_registry_conf}")

    schema_registry_client = SchemaRegistryClient(schema_registry_conf)
    schema_str = ""
    try:
        schema = schema_registry_client.get_latest_version(subject_name = f"{TopicName}-value")
        schema_type = schema.schema.schema_type
        schema_str = schema.schema.schema_str
        logger.info(f"Latest SchemaType is- {schema_type}")
        logger.info(f"Latest SchemaStr is- {schema_str}")

        if schema_type == "JSON":
            serializer = JSONSerializer(schema_registry_client = schema_registry_client, schema_str = schema_str)
        elif schema_type == "PROTOBUF":
            serializer = ProtobufSerializer(schema_registry_client = schema_registry_client,
                                            conf = {'use.deprecated.format':True})
        else:
            serializer = AvroSerializer(schema_registry_client = schema_registry_client, schema_str = schema_str)

    except SchemaRegistryError as e:
        logger.info(f"Schema registry/version not found for the topic- {TopicName}. Error- {e}")

    producer = Producer(conf)
    published_keys = []

    bad_request_error = ""
    for msg in messages:
        uuid_16 = str(uuid.uuid4().int)[:16]
        logger.info(f"converting encoded data into decoded bytes")
        decoded_bytes = base64.b64decode(msg)
        decoded_string = decoded_bytes.decode('utf-8')
        logger.info(f"decoded string: {decoded_string}")
        try:
            record_key = uuid_16
            logger.info("Producing record: {}\t{}".format(record_key, msg))
            if not schema_str:
                logger.info(f"no schema found in schema registry associated with the topic- {TopicName}, "
                            f"hence publishing the messages without serialization")
                producer.produce(TopicName,
                                 key = record_key,
                                 value = msg,
                                 on_delivery = delivery_report)
            else:
                decoded_msg = json.loads(decoded_string)
                logger.info('Writing message using confluent kafka producer')
                logger.info(f'Message to publish is- {decoded_msg}')

                producer.produce(TopicName,
                             key = record_key,
                             value = serializer(decoded_msg, SerializationContext(TopicName, MessageField.VALUE)), on_delivery = delivery_report)
            producer.poll(0)
            published_keys.append(uuid_16)

        except ValueError as v_err:
            logger.error(f"Invalid input, discarding record...: {msg}")
            bad_request_error = {
                  "error": {
                    "code": 400,
                    "message": f"Schema Validation Failed, {v_err}",
                    "status": "Bad Request",
                    "details": {
                      "reason": "Message is not inline with Schema",
                      "type": "Error"
                    }
                  }
                }
            continue
        except Exception as exe:
            logger.error(f'Issue while producing messages to topic -{TopicName}: {str(exe)}')
            continue

    if producer.flush(timeout = 10) == 0 and published_keys:
        logger.info("Messages sent successfully")
        return {"messageIds": published_keys}
    elif bad_request_error:
        logger.error(f"Failed to send messages to due to- {str(bad_request_error)}")
        return JSONResponse(content = bad_request_error, status_code=400)
    logger.error("Failed to send messages to Kafka")
    error = {
                  "error": {
                    "code": 500,
                    "message": f"Unable to publish messages to Kafka",
                    "status": "Error",
                    "details": {
                      "reason": "Unexpected Error",
                      "type": "Unexpected Error"
                    }
                  }
                }
    return JSONResponse(content = error, status_code = 500)

