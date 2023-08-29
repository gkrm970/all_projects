import base64
import binascii
import concurrent.futures
import functools
import json
import logging
import os
import re
import sys
import time

import fastavro
import requests
from confluent_kafka import Consumer
from confluent_kafka.admin import AdminClient, ConfigResource
from confluent_kafka.cimpl import KafkaException, KafkaError
from utils.avro_utils import infer_avro_schema
from utils.cron_scheduler_config import Config as service_config

logging.getLogger('apscheduler.scheduler').propagate = False
logging.getLogger('apscheduler.executors').propagate = False
logger = logging.getLogger('bg-scheduler-instance')


service_url = service_config.service_url
fetch_fed_kafka_bus_by_id_url = service_url + 'fed_kafka_bus/get_by_id/'
create_fed_kafka_topics_url = service_url + 'topics'
get_schema_registry_info_url = service_url + 'schemaregistry/'
get_latest_schema_info_db_url = service_url + 'schemas'
create_new_inactive_old_schema_url = service_url + 'schemas/update_new_schema'
update_schema_topic_association_to_latest_schema_version_url = \
    '{0}topics/update_latest_schema_in_topic_schema_association'.format(service_url)
create_schema_topic_url = '{0}topics/create/topic_schema_association'.format(service_url)
create_schema_info_url = service_url + 'schemas'
get_auth_record_url = service_url + 'auth_type/get_record_by/'


def get_message_retention_period(admin_client, topic_name_from_cloud):
    try:
        msg_retention_time_obj = ''
        logger.info(
            f'Schema:: fetching message retention period for topic - {topic_name_from_cloud}')
        admin_client.poll(timeout=10)
        topic_configResource = admin_client.describe_configs(
            [ConfigResource(restype=2, name=topic_name_from_cloud)], request_timeout=10)
        for j in concurrent.futures.as_completed(iter(topic_configResource.values())):
            config_response = j.result(timeout=1)
            logger.info(f'schema:: config response is : {config_response}')
            msg_retention_time_obj = config_response.get('retention.ms')
        msg_retention_time_ms = (str(msg_retention_time_obj).split('=')[-1]).replace('"', "")
        msg_retention_time_sec = (int(msg_retention_time_ms) / 1000)
        logger.info(f'schema:: message retention time for topic is : {msg_retention_time_sec}')
    except KafkaException as e:
        # Check if the error is due to topic authorization failure
        if e.args[0].code() == KafkaError.TOPIC_AUTHORIZATION_FAILED:
            # Handle the topic authorization failure error
            logger.error("Topic authorization failed. Hence setting default retention time of 7 days")
        else:
            # Handle other Kafka exceptions
            logger.error("Kafka exception while fetching retention, Setting default retention of 7 days")
        logger.error(f'Kafka exception: {str(e)}')
        msg_retention_time_sec = 604800

    return msg_retention_time_sec


def infer_schema_from_messages(topic_names, auth_obj, query_endpoint_with_token, consumer, admin_client, kafka_host):
    logger.info(
        f'if schema_registry not exits with the admin payload,reading data from all topics')
    if len(topic_names) > 0:
        for topic_name in topic_names:
            try:
                if topic_name == "_schemas" or topic_name == "__consumer_offsets":
                    continue

                logger.info(f'Fetching message retention period for topic - {topic_name}')
                msg_retention_time_sec = get_message_retention_period(admin_client, topic_name)
                if 0 >= msg_retention_time_sec > 604800:
                    logger.info(f"Message retention- {msg_retention_time_sec}sec for topic- {topic_name} is beyond "
                                f"the limit(i.e. 0 sec >= msg_retention_time_sec > 604800 sec), skipping the discovery")
                    continue
                logger.info(f'message retention time for topic is : {msg_retention_time_sec}')

                schema_name = None
                logger.info(f"Calling get topic API to check whether topic- {topic_name} exist "
                            f"in table or not")
                get_topic_url = service_url + "topics/" + str(topic_name)
                logger.info(f"get_topic_url- {get_topic_url}")
                topic_response = query_endpoint_with_token(auth_obj, get_topic_url, 'GET',
                                                           timeout = 60)
                if topic_response.status_code == 200:
                    if topic_response.json().get("schemaSettings"):
                        schema_name = topic_response.json().get("schemaSettings").get("schema")
                        logger.info(f"Topic- {topic_name} exists, & have schema- {schema_name} mapped to it")

                if schema_name is None:
                    logger.info(
                        f'assigning schema name with topic name: {topic_name}')
                    schema_name = topic_name

                get_latest_schema_info_url = get_latest_schema_info_db_url + '/' + str(
                    schema_name)
                logger.debug(
                    f'get_latest_schema_info_url - {get_latest_schema_info_url}')
                schema_info_object = query_endpoint_with_token(auth_obj, get_latest_schema_info_url, 'GET',
                                                               timeout=60)
                schema_info_resp = schema_info_object.json()
                logger.debug(
                    f'response from schema_info table - {schema_info_resp}')

                if schema_info_object.status_code == 200:
                    logger.info(f"Validating whether schema- {schema_name} belongs to pubsub category & "
                                f"contains definition")
                    definition = schema_info_resp.get("definition")
                    created_by = schema_info_resp.get("created_by")

                    if created_by == "" and definition != "":
                        logger.info(f"Schema- {schema_name} found pubsub category and contains definition,"
                                    f"hence skipping discovery for it")
                        continue
                    logger.info(f"Schema- {schema_name} is not created either along with topic creation"
                                f" or schema definition is empty")
                else:
                    logger.info(f"Schema- {schema_name} does not exist, creating first time.")

                logger.info(
                    f'getting all messages from single topic - {topic_name}')
                logger.info(
                    f'getting first message in topic - {topic_name}')
                first_message_in_topic = None

                try:
                    logger.info(f'getting subscribed to the topic - {topic_name}')
                    # consumer.subscribe([topic_name] , on_assign=on_assign)
                    consumer.subscribe([topic_name])
                    logger.info('Subscribed to the topic successfully')
                    logger.info('Polling to fetch the record using offset')
                    msg = consumer.poll(5.0)
                    logger.info(f'{type(msg)}')

                    if msg is None:
                        logger.info(f'message is None')
                        first_message_in_topic = "NO_MSG_TO_DISCOVER_SCHEMA"
                        logger.info(f'message is set to - {first_message_in_topic}')
                    elif msg.error():
                        if msg.error().code() == KafkaError._PARTITION_EOF:
                            logger.info(f'reached end at offset - {msg.topic(), msg.partition(), msg.offset()}')
                        elif msg.error():
                            logger.info(f'Consumer error: {msg.error()}')
                            # raise KafkaException(msg.error())
                            logger.error(KafkaException(msg.error()))
                        first_message_in_topic = "UNABLE_TO_DISCOVER_SCHEMA"
                    else:
                        all_messages = msg.value()
                        logger.info(f'message type is - {type(all_messages)}')
                        if isinstance(all_messages, (bytes, bytearray)):
                            try:
                                all_message_in_topic = all_messages.decode('utf-8').replace("'", '"')
                                logger.info(f'all messages in topics are - {all_message_in_topic}')
                                string_to_json_list = re.split(r'(?<=})\B(?={)', all_message_in_topic)
                                logger.info(f'byte stream to json objects are : {string_to_json_list}')
                                list_after_split = re.split('\n', string_to_json_list[0])
                                for json_msg in list_after_split:
                                    first_message_in_topic = json_msg
                            except Exception as err:
                                logger.info(f'Messages are in encoded format, decode format not known')
                                logger.error(f'Exception while decoding - {str(err)}')
                                first_message_in_topic = "UNABLE_TO_DISCOVER_SCHEMA"
                        else:
                            first_message_in_topic = all_messages.decode('utf-8')
                            logger.info(f'message received is : {first_message_in_topic}')
                finally:
                    logger.info('Finally block')
                    # consumer.close()

                if first_message_in_topic != "NO_MSG_TO_DISCOVER_SCHEMA" and \
                        first_message_in_topic != "UNABLE_TO_DISCOVER_SCHEMA":
                    logger.info(
                        f'Message existed in topic {first_message_in_topic}')
                    logger.info(f'type of message is : {type(first_message_in_topic)}')

                    if isinstance(first_message_in_topic, str):
                        logger.info('Decoding message if in base64 format')
                        try:
                            str_to_dict_data = ''
                            is_valid = base64.b64decode(first_message_in_topic, validate=True)
                            if is_valid is not None:
                                logger.info(f"converting encoded data into decoded bytes")
                                decoded_string = is_valid.decode('utf-8')
                                logger.info(f"decoded string: {decoded_string}")
                                str_to_dict_data = json.loads(decoded_string)
                            else:
                                logger.info('Message after decode is empty')
                        except binascii.Error as e:
                            str_to_dict_data = json.loads(first_message_in_topic)

                        consumed_message = str_to_dict_data
                        generated_schema_from_topic = infer_avro_schema(str_to_dict_data, topic_name)
                    else:
                        consumed_message = first_message_in_topic
                        generated_schema_from_topic = infer_avro_schema(first_message_in_topic, topic_name)
                    logger.info(f'generated schema from topic is - {generated_schema_from_topic}')
                    logger.info(f'{type(generated_schema_from_topic)}')

                    if schema_info_object.status_code == 200:
                        schema_id = schema_info_resp['id']
                        if schema_info_resp.get('definition') != '':
                            schema_definition_from_db = json.loads(schema_info_resp.get('definition'))

                            # Compile the Avro schema
                            logger.info(f'Compiling avro schema ')
                            schema_definition_from_db = fastavro.parse_schema(schema_definition_from_db)

                            try:
                                logger.info('Validating the consumed message against existing schema in DB')
                                fastavro.validate(consumed_message, schema_definition_from_db)
                                logger.info('Schema of Consumed Message matches with the existing schema in DB')
                            except fastavro.validation.ValidationError as e:
                                logger.info(f'Current generated schema and stored schema are different, '
                                            f'Hence setting schema to UNDISCOVERED')

                                new_version_schema_info_payload_dict = {
                                    "name": schema_name,
                                    "type": "",
                                    "definition": "",
                                }
                                create_new_inactive_old_schema_with_schema_id_url = \
                                    create_new_inactive_old_schema_url + \
                                    "/" + str(schema_id)
                                schema_info_object = query_endpoint_with_token(auth_obj,
                                                                               create_new_inactive_old_schema_with_schema_id_url,
                                                                               'PUT',
                                                                               data=new_version_schema_info_payload_dict,
                                                                               timeout=60)
                                schema_info_json_resp = schema_info_object.json()
                                logger.info(
                                    f'response after inserting latest version - {schema_info_json_resp}')
                                # latest_version_schema_id = schema_info_json_resp['id']
                                # logger.info(
                                #     f'updating schema topics associations')
                                # update_schema_topic_association_to_latest_schema_version_with_ids_url = \
                                #     update_schema_topic_association_to_latest_schema_version_url + \
                                #     "/" + str(schema_id) + "/" + \
                                #     str(latest_version_schema_id)
                                # schema_topic_relation_object_resp = requests.put(
                                #     update_schema_topic_association_to_latest_schema_version_with_ids_url,
                                #     headers=headers)
                                # schema_topic_relation_object_resp = schema_topic_relation_object_resp.json()
                                # logger.info(
                                #     f'Updated topic schema association...')
                        else:
                            # Need to check with Aditya
                            logger.info(f"Schema- {schema_name} stored with empty definition, found new "
                                        f"definition, hence updating with new one")
                            new_version_schema_info_payload_dict = {
                                "name": schema_name,
                                "type": "AVRO",
                                "definition": json.dumps(generated_schema_from_topic),
                            }
                            create_new_inactive_old_schema_with_schema_id_url = \
                                create_new_inactive_old_schema_url + \
                                "/" + str(schema_id)
                            schema_info_object = query_endpoint_with_token(auth_obj,
                                                                           create_new_inactive_old_schema_with_schema_id_url,
                                                                           'PUT',
                                                                           data=new_version_schema_info_payload_dict,
                                                                           timeout=60)
                            schema_info_json_resp = schema_info_object.json()
                            logger.info(
                                f'response after inserting latest version - {schema_info_json_resp}')

                    elif schema_info_object.status_code != 200:
                        logger.debug(
                            f'saving into schema info table')
                        schema_info_payload = {
                            "name": schema_name,
                            "type": "AVRO",
                            "definition": json.dumps(generated_schema_from_topic)
                        }
                        logger.debug(
                            f'schema_info_payload is - {schema_info_payload}')
                        logger.debug(
                            f'{create_schema_info_url}')
                        schema_info_object = query_endpoint_with_token(auth_obj, create_schema_info_url, 'POST',
                                                                       data=schema_info_payload, timeout=60)
                        schema_info_json_resp = schema_info_object.json()
                        schema_info_object_id = schema_info_json_resp["id"]

                        logger.info(
                            f'After saving into Schema info DB {schema_info_json_resp} and '
                            f'id is {schema_info_object_id}')

                        topic_payload_dict = {
                            "labels": {
                                "key": "",
                                "value": ""
                            },
                            "schemaSettings": {
                                "schema": schema_name,
                                "encoding": "JSON",
                                "firstRevisionId": "",
                                "lastRevisionId": ""
                            },
                            "kafka_bus_name": json.dumps(kafka_host),
                            "messageRetentionDuration": f'{msg_retention_time_sec}s',
                            "retentionSettings": {
                                "liveDateRetentionDuration": "",
                                "historicalDataRetentionDuration": ""
                            },
                            "supportedSubscriptionTypes": ""
                        }
                        logger.debug(
                            f'Inserting kafka topic details-{topic_payload_dict} in fed_kafka_topics table')
                        create_fed_kafka_topics_with_topic_name_url = "{0}/{1}".format(
                            create_fed_kafka_topics_url, str(topic_name))

                        if topic_response.status_code == 200:
                            fed_kafka_topics_object = query_endpoint_with_token(auth_obj,
                                                                                create_fed_kafka_topics_with_topic_name_url,
                                                                                'PATCH', data = topic_payload_dict,
                                                                                timeout = 60)
                            fed_kafka_topics_object = fed_kafka_topics_object.json()
                            logger.info(
                                f'fed_kafka_topics_object - {fed_kafka_topics_object}')
                            continue

                        fed_kafka_topics_object = query_endpoint_with_token(auth_obj,
                                                                            create_fed_kafka_topics_with_topic_name_url,
                                                                            'PUT', data=topic_payload_dict, timeout=60)
                        fed_kafka_topics_object = fed_kafka_topics_object.json()
                        logger.info(
                            f'fed_kafka_topics_object - {fed_kafka_topics_object}')
                        fed_kafka_topics_object_id = fed_kafka_topics_object["id"]
                        logger.info(
                            f'after saving fed_kafka_topics_object-{fed_kafka_topics_object} and '
                            f'id is {fed_kafka_topics_object_id}')

                        logger.info(
                            f'saving schema topics association table')
                        schema_topic_payload_dict = {
                            "schemas_info_id": schema_info_object_id,
                            "kafka_topics_id": fed_kafka_topics_object_id,
                        }
                        schema_topic_relation_object = query_endpoint_with_token(auth_obj, create_schema_topic_url,
                                                                                 'POST', data=schema_topic_payload_dict,
                                                                                 timeout=60)
                        schema_topic_relation_object = schema_topic_relation_object.json()
                        logger.info(
                            f'schema topics relation response {schema_topic_relation_object}')
                else:
                    if (first_message_in_topic == "NO_MSG_TO_DISCOVER_SCHEMA" or
                        first_message_in_topic == "UNABLE_TO_DISCOVER_SCHEMA") \
                            and schema_info_object.status_code != 200:
                        logger.info(
                            f'no messages existed for this topic - {topic_name}')
                        schema_info_payload = {
                            "name": schema_name,
                            "type": "",
                            "definition": ""
                        }
                        logger.debug(
                            f'schema_info_payload is - {schema_info_payload}')
                        logger.debug(
                            f'{create_schema_info_url}')
                        schema_info_object = query_endpoint_with_token(auth_obj, create_schema_info_url, 'POST',
                                                                       data=schema_info_payload, timeout=60)
                        schema_info_json_resp = schema_info_object.json()
                        schema_info_object_id = schema_info_json_resp["id"]

                        logger.info(
                            f'After saving into Schema info DB '
                            f'{schema_info_json_resp} and id is {schema_info_object_id}')

                        topic_payload_dict = {
                            "labels": {
                                "key": "",
                                "value": ""
                            },
                            "schemaSettings": {
                                "schema": schema_name,
                                "encoding": "JSON",
                                "firstRevisionId": "",
                                "lastRevisionId": ""
                            },
                            "kafka_bus_name": json.dumps(kafka_host),
                            "messageRetentionDuration": f'{msg_retention_time_sec}s',
                            "retentionSettings": {
                                "liveDateRetentionDuration": "",
                                "historicalDataRetentionDuration": ""
                            },
                            "supportedSubscriptionTypes": ""
                        }
                        logger.debug(
                            f'Inserting kafka topic details - '
                            f'{topic_payload_dict} in fed_kafka_topics table')
                        create_fed_kafka_topics_with_topic_name_url = "{0}/{1}".format(
                            create_fed_kafka_topics_url, str(topic_name))

                        if topic_response.status_code == 200:
                            fed_kafka_topics_object = query_endpoint_with_token(auth_obj,
                                                                                create_fed_kafka_topics_with_topic_name_url,
                                                                                'PATCH', data = topic_payload_dict,
                                                                                timeout = 60)
                            fed_kafka_topics_object = fed_kafka_topics_object.json()
                            logger.info(
                                f'fed_kafka_topics_object - {fed_kafka_topics_object}')
                            continue
                        fed_kafka_topics_object = query_endpoint_with_token(auth_obj,
                                                                            create_fed_kafka_topics_with_topic_name_url,
                                                                            'PUT', data=topic_payload_dict, timeout=60)
                        fed_kafka_topics_object = fed_kafka_topics_object.json()
                        logger.info(
                            f'fed_kafka_topics_object - {fed_kafka_topics_object}')
                        fed_kafka_topics_object_id = fed_kafka_topics_object["id"]
                        logger.info(
                            f'after saving fed_kafka_topics_object - {fed_kafka_topics_object} '
                            f'and id is {fed_kafka_topics_object_id}')

                        logger.info(
                            f'saving schema topics association table')
                        schema_topic_payload_dict = {
                            "schemas_info_id": schema_info_object_id,
                            "kafka_topics_id": fed_kafka_topics_object_id,
                        }
                        schema_topic_relation_object = query_endpoint_with_token(auth_obj, create_schema_topic_url,
                                                                                 'POST', data=schema_topic_payload_dict,
                                                                                 timeout=60)
                        schema_topic_relation_object = schema_topic_relation_object.json()
                        logger.info(
                            f'schema topics relation response {schema_topic_relation_object}')
                    else:
                        logger.info('Entry already exist for this topic-schema relation')
            except Exception as e:
                logger.exception(f'Exception while dealing topic name - {topic_name}')
                exc_type, exc_obj, exc_tb = sys.exc_info()
                fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                logger.error(f'{exc_type, fname, exc_tb.tb_lineno}')
                continue


def on_assign (c, ps):
    for p in ps:
        p.offset=-2
        c.assign(ps)


def compare_and_delete_topic_from_table(all_topics_from_kafka, query_endpoint_with_token, auth_obj, service_url, kafka_host):
    try:
        logger.info("Inside compare_and_delete_topic_from_table")
        all_topics_from_table_url = service_url + "topics"
        all_topics_from_table = query_endpoint_with_token(auth_obj, all_topics_from_table_url, 'GET', timeout = 60)
        logger.debug(f"get all topic from table- {all_topics_from_table}")
        if all_topics_from_table.status_code != 200:
            logger.info(f"Failed to get all topic from get all topic API- {all_topics_from_table.status_code}")
            return None
        logger.info("Fetched all topics from get all topic api")
        all_topics_from_table = all_topics_from_table.json().get("topics")
        all_topics_from_table = [record.get("name") for record in all_topics_from_table if record.get("kafka_bus_name") == json.dumps(kafka_host)]
        logger.info(f"All topic of kafka host- {kafka_host} from table: {all_topics_from_table}")

        for topic_from_table in all_topics_from_table:
            if topic_from_table not in all_topics_from_kafka:
                # delete the topic from table
                logger.info(f"Topic-{topic_from_table} not found in Kafka, going to delete from table as well")
                fed_kafka_delete_topic_url = service_url + f"topics/{topic_from_table}"
                logger.info(f"delete topic url- {fed_kafka_delete_topic_url}")
                delete_topic_response = query_endpoint_with_token(auth_obj, fed_kafka_delete_topic_url, 'DELETE',
                                                                 timeout = 60)
                if delete_topic_response.status_code != 200:
                    logger.info(f"Failed to delete the topic- {topic_from_table} from table.")
                    continue
                logger.info(f"Deleted the topic- {topic_from_table} for Bus- {kafka_host} from table successfully.")
    except Exception as exe:
        logger.info(f"Exception occurred while comparing & deleting topic from topic table - {exe}")
        return None


def _get_token(args, config_str):
    """Note here value of config comes from sasl.oauthbearer.config below.
        It is not used in this example but you can put arbitrary values to
        configure how you can get the token (e.g. which token URL to use)
        """
    resp = requests.post(args[1], data=args[0], verify=False)
    token = resp.json()
    logger.info(f'token------------------ {token["access_token"]}')
    return token['access_token'], time.time() + float(token['expires_in'])


def discovery_service(job_ids, auth_obj, query_endpoint_with_token):
    logger.info(
        '****************************** Discovery Plugin service ******************************')
    ids_list = job_ids
    logger.info(
        f'All the job ids for this frequency interval are: {ids_list}')
    for job_id in ids_list:
        logger.info(f'Fetching details of kafka bus id - {job_id}')
        fed_kafka_info_id_url = fetch_fed_kafka_bus_by_id_url + str(job_id)
        logger.debug(
            f'url to fetch info for bus id is: {fed_kafka_info_id_url}')
        fed_kafka_buses_resp = query_endpoint_with_token(auth_obj, fed_kafka_info_id_url, 'GET', timeout=60)
        logger.debug(
            f'Response from fed_kafka_buses table is: {fed_kafka_buses_resp.json()} ')

        if fed_kafka_buses_resp.status_code == 200:
            logger.info('fed_kafka_buses entries are exist')
            response = fed_kafka_buses_resp.json()
            fed_kafka_bus_id = response['fed_kafka_id']
            fed_kafka_bus_name = response['kafka_host']
            schema_registry_id = response['schema_registry_id']
            kafka_host = response['kafka_host']
            kafka_port = response['kafka_port']
            kafka_auth_type = response.get('kafka_auth_type')
            created_by = 'Discovery-Plugin-Service'
            updated_by = 'Discovery-Plugin-Service'

            logger.info(
                'Fetching each row host, port and auth_type of bus from table - fed_kafka_buses')
            bootstrap_servers = []
            for bootstrap_server in kafka_host:
                bootstrap_servers.append(bootstrap_server + ":" + str(kafka_port))
            logger.info('kafka consumer call')
            conf = ''
            if kafka_auth_type is None:
                conf = {
                    'bootstrap.servers': ','.join(bootstrap_servers),
                    'group.id': os.getenv('CLIENT_ID'),
                    'auto.offset.reset': 'earliest',
                    'session.timeout.ms': 6000
                }

            elif kafka_auth_type == "OAUTHBEARER":
                logger.info(f'Get OAUTHBEARER details from auth_info table')
                logger.info(f'kafka_host - {kafka_host}')
                logger.info(f'type of kafka host is - {type(kafka_host)}')
                get_auth_record_url_with_params = f"{get_auth_record_url}{kafka_auth_type}/{json.dumps(kafka_host)}"
                logger.info(f'URL is: {get_auth_record_url_with_params}')
                auth_info_response = query_endpoint_with_token(auth_obj, get_auth_record_url_with_params, 'GET', timeout=60)
                auth_info_response_json = auth_info_response.json()
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
                logger.info(f'decoded_oauth_issuer_response - {decoded_oauth_issuer}')
                
                certificate_filename = f"telus_tcso_ca_{kafka_host[0]}.pem.txt"
                logger.info(f'certificate_filename - {certificate_filename}')
                certificate_filepath = os.path.join("/tmp", certificate_filename)
                logger.info(f'certificate_filepath - {certificate_filepath}')

                with open(certificate_filepath, 'wb') as file:
                    file.write(decoded_oauth_issuer)

                conf = {
                    'bootstrap.servers': ','.join(bootstrap_servers),
                    #'debug': 'broker,admin,protocol',
                    'security.protocol': auth_info_response_json.get('oauth_security_protocol'),
                    # 'security.protocol': 'SASL_PLAINTEXT',
                    'session.timeout.ms': 6000,
                    'auto.offset.reset': 'earliest',
                    'sasl.mechanisms': auth_info_response_json.get('auth_type'),
                    #'ssl.certificate.location': auth_info_response_json.get('oauth_issuer'),
                    #'ssl.ca.location': auth_info_response_json.get('oauth_issuer'),
                    'ssl.certificate.location': certificate_filepath,
                    'ssl.ca.location': certificate_filepath,
                    'enable.ssl.certificate.verification': False,
                    'group.id': auth_info_response_json.get('oauth_client_id'),
                    'oauth_cb': functools.partial(_get_token, token_payload_url_list)
                }
                logger.info(f"Oauthbearer kafka conf dict- {conf}")

            # ssl start*********************************************
            elif kafka_auth_type == "SSL":
                logger.info(f'Get SSL details from auth_info table')
                get_auth_record_url_with_params = "{0}{1}/{2}".format(get_auth_record_url, str(kafka_auth_type),
                                                                      json.dumps(fed_kafka_bus_name))
                logger.info(f'URL is: {get_auth_record_url_with_params}')
                auth_info_response = query_endpoint_with_token(auth_obj, get_auth_record_url_with_params, 'GET',
                                                               timeout = 60)
                auth_info_response_json = auth_info_response.json()
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
                    'bootstrap.servers': ','.join(bootstrap_servers),
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
                logger.info(f"ssl kafka conf dict- {conf}")

            consumer = Consumer(conf)
            admin_client = AdminClient(conf)
            consumer.poll(timeout=10)
            list_of_topics = consumer.list_topics().topics
            logger.debug(
                f'Fetching list  of topics from consumer call: {list_of_topics}')

            logger.info('topics appending to list')
            topic_names = []
            for topic_name in list_of_topics:
                topic_names.append(topic_name)
            logger.info(
                f'list of topic name from consumer call: {topic_names}')

            # Checking & deleting if any topic deleted in Kafka still present in Table
            logger.info("Checking & deleting if any topic deleted in Kafka still present in Table")
            # topic_names = topic_names[4:]    # added this line for testing purpose we can remove comment & 4 topics will be sliced out from actual kafka topic list & we can test the deletion for same 4.
            compare_and_delete_topic_from_table(topic_names, query_endpoint_with_token, auth_obj, service_url, kafka_host)

            if schema_registry_id is not None:
                cert = ('/tmp/pubsub-discovery.toll6.develop.tinaa.tlabs.crt', '/tmp/pubsub-discovery.toll6.develop.tinaa.tlabs_decrypt.key')
                logger.info(
                    f'Schema Registry id - {schema_registry_id} exist for order id: {fed_kafka_bus_id}')
                try:
                    logger.info(
                        f'Fetching the Schema Registry details for Schema Registry id - {schema_registry_id} '
                        f'from schema_registry table')
                    schema_registry_resp = query_endpoint_with_token(auth_obj, get_schema_registry_info_url + f'{schema_registry_id}', 'GET', timeout=60)
                    logger.debug(
                        f'Response from schema_registry table is - {schema_registry_resp}')
                    schema_registry_db_data = schema_registry_resp.json()
                    schema_registry_host = schema_registry_db_data["schema_registry_host"]
                    schema_registry_port = schema_registry_db_data["schema_registry_port"]

                    decoded_schemaregistry_certificate_location = base64.b64decode(schema_registry_db_data['schemaregistry_certificate_location'])
                    decoded_schemaregistry_key_location = base64.b64decode(schema_registry_db_data['schemaregistry_key_location'])

                    with open('/tmp/pubsub-discovery.toll6.develop.tinaa.tlabs.crt', 'wb') as file:
                        file.write(decoded_schemaregistry_certificate_location)
                    with open('/tmp/pubsub-discovery.toll6.develop.tinaa.tlabs_decrypt.key', 'wb') as file:
                        file.write(decoded_schemaregistry_key_location)

                    schema_info_from_schema_registery_url = "https://" + schema_registry_host + ":" + \
                                                            str(schema_registry_port) + "/schemas"
                    logger.debug(
                        f'schema_registry_url is - {schema_info_from_schema_registery_url}')
                    schema_info_resp = query_endpoint_with_token(auth_obj, schema_info_from_schema_registery_url , 'GET', timeout=60, cert=cert)
                    all_schema_defintions_from_cloud = schema_info_resp.json()
                    if schema_info_resp.status_code == 200:
                        logger.debug(
                            f'Response from schema_registry url is successful')
                        #f'Response from schema_registry url: {all_schema_defintions_from_cloud}')

                    for each_cloud_schema_dict in all_schema_defintions_from_cloud:
                        subject_from_cloud = each_cloud_schema_dict.get('subject')
                        if "-key" in subject_from_cloud:
                            continue
                        topic_name_from_cloud = (subject_from_cloud.split('-value'))[:-1][0]
                        schema_name_from_cloud = topic_name_from_cloud
                        schema_type_from_cloud = each_cloud_schema_dict.get('schemaType')
                        if schema_type_from_cloud is None:
                            schema_type_from_cloud = 'AVRO'
                        latest_schema_definition_from_cloud = each_cloud_schema_dict.get('schema')
                        latest_schema_version_from_cloud = each_cloud_schema_dict.get('version')

                        logger.debug(
                            f"Values after extracting from schema are: {subject_from_cloud}, {topic_name_from_cloud}, "
                            f"{schema_name_from_cloud},{schema_type_from_cloud}")
                        # logger.debug(f'Schema definition for {topic_name_from_cloud} is - {latest_schema_definition_from_cloud}')
                        get_latest_schema_info_url = '{0}/{1}/{2}'.format(get_latest_schema_info_db_url, str(schema_name_from_cloud), str(latest_schema_version_from_cloud))
                        logger.debug(
                            f'get_latest_schema_info_url - {get_latest_schema_info_url}')
                        schema_info_object = query_endpoint_with_token(auth_obj, get_latest_schema_info_url, 'GET', timeout=60)

                        if schema_info_object.status_code == 200:
                            schema_info_resp = schema_info_object.json()
                            logger.debug(f'response from schema_info table is successful')
                            schema_id = schema_info_resp['id']
                            old_schema_definition_in_db = schema_info_resp['definition']
                            logger.debug(
                                f'type of old schema - {type(old_schema_definition_in_db)}')
                            logger.debug(
                                f'type of new schema - {type(latest_schema_definition_from_cloud)}')
                            old_schema, new_schema = old_schema_definition_in_db, latest_schema_definition_from_cloud
                            logger.info(
                                'comparing old_schema with latest_schema')
                            logger.info(f'old schema is - {old_schema}')
                            logger.info(f'new schema is - {new_schema}')
                            if old_schema != new_schema:
                                logger.info(
                                    f'both schemas are not same, Updating the DB definition with the new one')

                                logger.info(
                                    f'create_new_inactive_old_schema_url: {create_new_inactive_old_schema_url}')
                                new_version_schema_info_payload_dict = {
                                    "definition": json.dumps(latest_schema_definition_from_cloud),
                                }
                                create_new_inactive_old_schema_with_schema_id_url = "{0}/{1}".format(
                                    create_new_inactive_old_schema_url, str(schema_id))
                                schema_info_object = query_endpoint_with_token(auth_obj, create_new_inactive_old_schema_with_schema_id_url, 'PUT', data=new_version_schema_info_payload_dict, timeout=60)
                                schema_info_json_resp = schema_info_object.json()
                                logger.info(
                                    f'response after inserting latest version - {schema_info_json_resp}')
                                # latest_version_schema_id = schema_info_json_resp['id']
                                # logger.info(
                                #     f'updating schema topics associations')
                                # update_schema_topic_association_to_latest_schema_version_with_ids_url = \
                                #     update_schema_topic_association_to_latest_schema_version_url + "/" + schema_id + \
                                #     "/" + latest_version_schema_id
                                # schema_topic_relation_object_resp = requests.put(
                                #     update_schema_topic_association_to_latest_schema_version_with_ids_url,
                                #     headers=headers)
                                # schema_topic_relation_object_resp = schema_topic_relation_object_resp.json()
                                logger.info(
                                    f'topic-schema association updated successfully')
                            else:
                                logger.info(
                                    f'no differences found the the old and latest schema definitions. Old schema: '
                                    f'{old_schema} , New schema: {new_schema}')
                        elif schema_info_object.status_code != 200:
                            logger.debug(
                                f'saving schema info table because no record existed')

                            msg_retention_time_sec = get_message_retention_period(admin_client, topic_name_from_cloud)
                            if 0 >= msg_retention_time_sec > 604800:
                                logger.info(
                                    f"Message retention- {msg_retention_time_sec}sec for topic- {topic_name_from_cloud}"
                                    f" is beyond the limit(i.e. 0 sec >= msg_retention_time_sec > 604800 sec), "
                                    f"skipping the discovery")
                                continue

                            schema_info_payload = {
                                "name": schema_name_from_cloud,
                                "type": schema_type_from_cloud,
                                "definition": latest_schema_definition_from_cloud,
                                "revisionId": latest_schema_version_from_cloud
                            }
                            logger.debug(
                                f'schema_info_payload is - {schema_info_payload}')
                            logger.debug(f'{create_schema_info_url}')
                            schema_info_object = query_endpoint_with_token(auth_obj, create_schema_info_url, 'POST', data=schema_info_payload, timeout=60)
                            schema_info_json_resp = schema_info_object.json()
                            logger.debug(
                                f'Response after inserting into schema info table - {schema_info_json_resp}')
                            schema_info_object_id = schema_info_json_resp["id"]

                            logger.info(
                                f'After saving into Schema info DB {schema_info_json_resp} and '
                                f'id is {schema_info_object_id}')

                            if topic_name_from_cloud not in topic_names:
                                continue

                            logger.info(f'Checking for topic existance')
                            get_topic_url = '{0}/{1}'.format(create_fed_kafka_topics_url, str(topic_name_from_cloud))
                            logger.debug(
                                f'get_topic_url - {get_topic_url}')
                            get_topic_obj = query_endpoint_with_token(auth_obj, get_topic_url, 'GET', timeout=60)
                            if get_topic_obj.status_code == 200:
                                logger.info(f'Topic is already exist in DB')
                                continue

                            topic_payload_dict = {
                                "labels": {
                                    "key": "",
                                    "value": ""
                                },
                                "schemaSettings": {
                                    "schema": schema_name_from_cloud,
                                    "encoding": "JSON",
                                    "firstRevisionId": "",
                                    "lastRevisionId": ""
                                },
                                "kafka_bus_name": json.dumps(kafka_host),
                                "messageRetentionDuration": f'{msg_retention_time_sec}s',
                                "retentionSettings": {
                                    "liveDateRetentionDuration": "",
                                    "historicalDataRetentionDuration": ""
                                },
                                "supportedSubscriptionTypes": ""
                            }
                            logger.debug(
                                f'Inserting kafka topic details - {topic_payload_dict} in fed_kafka_topics table')
                            create_fed_kafka_topics_with_topic_name_url = "{0}/{1}".format(
                                create_fed_kafka_topics_url, str(topic_name_from_cloud))
                            fed_kafka_topics_object = query_endpoint_with_token(auth_obj, create_fed_kafka_topics_with_topic_name_url, 'PUT', data=topic_payload_dict, timeout=60)
                            fed_kafka_topics_object = fed_kafka_topics_object.json()
                            logger.info(
                                f'fed_kafka_topics_object - {fed_kafka_topics_object}')
                            fed_kafka_topics_object_id = fed_kafka_topics_object["id"]
                            logger.info(
                                f'after saving fed_kafka_topics_object - {fed_kafka_topics_object} '
                                f'and id is {fed_kafka_topics_object_id}')

                            logger.info(
                                f'saving schema topics association table')
                            schema_topic_payload_dict = {
                                "schemas_info_id": schema_info_object_id,
                                "kafka_topics_id": fed_kafka_topics_object_id,
                            }
                            schema_topic_relation_object = query_endpoint_with_token(auth_obj, create_schema_topic_url, 'POST', data=schema_topic_payload_dict, timeout=60)
                            schema_topic_relation_object = schema_topic_relation_object.json()
                            logger.info(
                                f'schema topics relation response {schema_topic_relation_object}')

                            # Identifying list of topics that have schema mapped to it based on confluent schema registry
                            # Removing such topics from all the topics returning by kafka client
                            if topic_name_from_cloud in topic_names:
                                topic_names.remove(topic_name_from_cloud)
                except Exception as e:
                    logger.error(
                        f'Error while dealing with schema registry ID - {schema_registry_id}')
                    logger.error(f'Exception : {e}')
                    continue

            # Infering schema from messages for topics which doesn't have schema associated to it
            infer_schema_from_messages(topic_names, auth_obj, query_endpoint_with_token, consumer, admin_client, kafka_host)
        else:
            logger.error(
                f'Issue while fetching the kafka bus id - {job_id}')

