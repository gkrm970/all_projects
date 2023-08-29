from datetime import datetime
import logging
import json
from json import JSONDecodeError
from fastapi import Depends, HTTPException, APIRouter, Path, Header
from app.models.models import FedKafkaBuses, SchemaRegistry, SchemasInfo, StatusEnum
from app.models.models import TopicSchemaAssociation as SchemaTopicAssociation
from app.models.models import FedKafkaTopics as KafkaTopics
from sqlalchemy.orm import Session
from sqlalchemy import exc
from app.schemas.schema_info_schema import SchemaInfo as SchemaInfoSchema
from app.api.deps import get_db, get_current_user
from app.schemas.token import TokenPayload
import fastavro
from app.api.utils.utils import infer_avro_schema
# from fastapi_pagination import Page, Params, paginate
from app.core.config import settings
from tinaa.logger.v1.tinaa_logger import get_app_logger
from fastapi.responses import JSONResponse
from app.responses.return_api_responses import unauthorized_responses, Unexpected_error_responses, \
    bad_request_responses, Resource_conflict_responses
from app.responses.schema_topic_return_response import schema_post_create_response, get_all_schema_response, \
    Get_all_schema_Successful_Response, Schema_Post_Successful_Response, no_schema_found
from app.api.utils.utils import gen_random_string, is_user_admin
from jsonschema import ValidationError

API_NAME = "Schema API"
logger_conf = [
    {
        "handler_name":settings.LOGGER_HANDLER_NAME,
        "log_level":settings.TINAA_LOG_LEVEL,
        "log_format":settings.LOGGER_FORMAT,
        "date_format":settings.LOGGER_DATE_FORMAT,
        "app_code":settings.LOGGER_APP_CODE,
        "app_name":API_NAME,
    }
]
logger = get_app_logger(log_conf = logger_conf, logger_name = API_NAME)

from app.responses.schema_topic_return_response import schema_post_create_response, get_all_schema_response, \
    Get_all_schema_Successful_Response

api_router = APIRouter()


# Schema Info CRUD Starts
@api_router.post("/schemas",
                 responses = {**Schema_Post_Successful_Response, **bad_request_responses, **unauthorized_responses,
                              **Resource_conflict_responses,
                              **Unexpected_error_responses})
async def Creates_a_schema(schema_info: SchemaInfoSchema, db: Session = Depends(get_db),
                           authorization: str = Header(None), current_user: TokenPayload = Depends(get_current_user)):
    is_admin = is_user_admin(authorization)

    try:
        if not schema_info.name:
            logger.error('Schema Name can not be left empty')

            return JSONResponse(
                content = {
                    "error":{
                        "code":400,
                        "message":"Schema Name can not be left empty",
                        "status":"Bad Request"
                    }
                },
                status_code = 400)

        if not schema_info.type and not is_admin:
            logger.error('Schema type can not be left empty')

            return JSONResponse(
                content = {
                    "error":{
                        "code":400,
                        "message":"Schema type can not be left empty",
                        "status":"Bad Request"
                    }
                },
                status_code = 400)

        schema_object = db.query(SchemasInfo).filter(SchemasInfo.schema_name == schema_info.name).first()

        if not is_admin:
            if schema_object:
                logger.error(f'Given schema already exists')

                return JSONResponse(
                    content = {
                        "error":{
                            "code":409,
                            "message":"Given schema already exists",
                            "status":"ALREADY_EXISTS"
                        }
                    },
                    status_code = 409
                )
        else:
            # To create multiple Schemas with same schema name for schema registry use case
            logger.info(f'Creating new schema entry with new revision id only for schema registry based on the check')

        # schemas_fields = json.dumps(schema_info.schemas_fields)
        schema_definition = schema_info.definition
        logger.info(f'schema definition is : {schema_definition}')
        if schema_definition:
            logger.info(f'Validating schema definition is valid or not')
            logger.info(f'type of schema definiton is: {type(schema_definition)}')
            if isinstance(schema_definition, str):
                try:
                    # Try this if string doesn't enclosed in double quotes
                    str_to_dict_schema = json.loads(schema_definition.replace("'", '"'))
                except json.JSONDecodeError:
                    # Alternative logic when JSON decoding fails
                    str_to_dict_schema = json.loads(schema_definition)
            elif isinstance(schema_definition, dict):
                logger.info(f'schema_definition is already a dict object')
                str_to_dict_schema = schema_definition
            logger.info(f'schema type after converstion - {type(str_to_dict_schema)}')
            try:
                if fastavro.parse_schema(str_to_dict_schema):
                    logger.info("Valid Schema, proceeding...")
            except Exception as e:
                logger.error(f'Not a valid schema - {str(e)}')

                return JSONResponse(
                    content = {
                        "error":{
                            "code":400,
                            "message":"Invalid schema definition",
                            "status":"INVALID_ARGUMENT"
                        }
                    },
                    status_code = 400
                )
        else:
            logger.info('schema definition is empty')
            if not is_admin:
                return JSONResponse(
                    content = {
                        "error":{
                            "code":400,
                            "message":"Schema definition can not be left empty",
                            "status":"Bad Request"
                        }
                    },
                    status_code = 400
                )

        # revisionId is the only identifier to verify the request coming from user or discovery plugin (infer schema use case)
        revisionId = schema_info.revisionId
        logger.info(f'revisionId is - {revisionId}')
        if revisionId:
            # revision Id exist if request is coming for schema registry iuse case
            schema_model = db.query(SchemasInfo).filter(SchemasInfo.schema_name == schema_info.name,
                                                        SchemasInfo.revisionId == revisionId).first()
            if schema_model:
                logger.error(f'Given schema already exists')

                return JSONResponse(
                    content = {
                        "error":{
                            "code":409,
                            "message":"Given schema already exists",
                            "status":"ALREADY_EXISTS"
                        }
                    },
                    status_code = 409
                )

        schema_model = SchemasInfo()
        schema_model.schema_name = schema_info.name
        schema_model.schema_type = schema_info.type
        schema_model.definition = schema_info.definition
        if revisionId is None:
            schema_model.revisionId = gen_random_string(8)
        else:
            schema_model.revisionId = str(revisionId)
        schema_model.revisionCreateTime = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]

        if is_admin:
            schema_model.created_by = "admin"

        db.add(schema_model)
        db.commit()
        db.refresh(schema_model)

        return schema_post_create_response(schema_model, is_admin)
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
                "message": "Error Occurred in while creating schema",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content = error, status_code = 500)


@api_router.get("/schemas/validate/{schema}", responses = {**unauthorized_responses, **Unexpected_error_responses})
async def validate_schema(schema: str, current_user: TokenPayload = Depends(get_current_user)):
    try:
        str_to_dict_schema = json.loads(schema.replace("'", '"'))
        if fastavro.parse_schema(str_to_dict_schema):
            return {"status_message":"Valid Schema", "status_code":200}
        else:
            return {"status_message":"Not Valid Schema", "status_code":200}
    except JSONDecodeError as e:
        error = {
            "error":{
                "code":422,
                "message":"schema must be a key-value pair",
                "status":"string",
                "details":{
                    "reason":str(e),
                    "type":"validation error"
                }
            }
        }
        return JSONResponse(content = error, status_code = 422)

    except ValidationError as e:
        error = {
            "error": {
                "code": 422,
                "message": f"schema should contain require fields - name, type, fields",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "validation error"

                }
            }
        }

        return JSONResponse(content=error, status_code=422)

    except Exception as e:

        error = {
            "error": {
                "code": 500,
                "message": "Error Occurred while validating schema",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }

        return JSONResponse(content=error, status_code=500)


@api_router.get("/schemas/{SchemaName}:listRevisions",
                responses = {**Get_all_schema_Successful_Response, **unauthorized_responses,
                             **Unexpected_error_responses})
async def Lists_all_the_Schema_Revisions(SchemaName: str = Path(..., description = "Name of the schema"),
                                         db: Session = Depends(get_db), authorization: str = Header(None),
                                         current_user: TokenPayload = Depends(get_current_user)):
    is_admin = is_user_admin(authorization)
    logger.info(f'Request received to fetch all revisions for schema - {SchemaName}')
    try:
        SchemaInfo_model_list = db.query(SchemasInfo).filter(
            SchemasInfo.schema_name == SchemaName).all()

        if SchemaInfo_model_list:
            logger.info(f'schema revisions are exist')
            return get_all_schema_response(SchemaInfo_model_list, is_admin)
        else:
            error = {
                "error":{
                    "code":404,
                    "message":"No records available",
                    "status":"error",
                    "details":{
                        "reason":"No records available",
                        "type":"No records available"
                    }
                }
            }
            return JSONResponse(content = error, status_code = 404)

    except Exception as e:
        logger.error(f'Error occurred - {str(e)}')
        error = {
            "error":{
                "code":500,
                "message":f"Failed to fetch the records, {str(e)}",
                "status":"error",
                "details":{
                    "reason":"Failed to fetch record",
                    "type":"unexpected_error"
                }
            }
        }
        return JSONResponse(content = error, status_code = 500)


@api_router.get("/schemas/{SchemaName}",
                responses = {**Schema_Post_Successful_Response, **unauthorized_responses, **no_schema_found,
                             **Unexpected_error_responses})
async def Read_a_schema(SchemaName: str = Path(..., description = "Name of the schema"), db: Session = Depends(get_db),
                        authorization: str = Header(None), current_user: TokenPayload = Depends(get_current_user)):
    is_admin = is_user_admin(authorization)

    schema_info_model = db.query(SchemasInfo).filter(
        SchemasInfo.schema_name == SchemaName).order_by(SchemasInfo.revisionCreateTime.desc()).first()
    try:
        if schema_info_model:
            response = schema_post_create_response(schema_info_model, is_admin)
            return response
        else:
            error = {
                "error":{
                    "code":404,
                    "message":"Schema object does not exist in database with given SchemaName.",
                    "status":"error",
                    "details":{
                        "reason":"Schema object does not exist in database with given SchemaName.",
                        "type":"invalid_parameter"
                    }
                }
            }
            logger.info('No Record Found In DB')
            return JSONResponse(content = error, status_code = 404)
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
                "message": "Error Occurred while reading schema",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content = error, status_code = 500)


@api_router.get("/schemas/{SchemaName}/{revisionId}", include_in_schema = False,
                responses = {**Schema_Post_Successful_Response, **unauthorized_responses, **no_schema_found,
                             **Unexpected_error_responses})
async def Read_a_schema(SchemaName: str = Path(..., description = "Name of the schema"),
                        revisionId: str = Path(..., description = "Version of the schema"),
                        db: Session = Depends(get_db), authorization: str = Header(None),
                        current_user: TokenPayload = Depends(get_current_user)):
    is_admin = is_user_admin(authorization)

    schema_info_model = db.query(SchemasInfo).filter(
        SchemasInfo.schema_name == SchemaName).first()
    schema_info_model = db.query(SchemasInfo).filter(SchemasInfo.schema_name == SchemaName,
                                                     SchemasInfo.revisionId == revisionId).first()
    try:
        if schema_info_model:
            response = schema_post_create_response(schema_info_model, is_admin)
            return response
        else:
            error = {
                "error":{
                    "code":404,
                    "message":"Schema object does not exist in database with given SchemaName.",
                    "status":"error",
                    "details":{
                        "reason":"Schema object does not exist in database with given SchemaName.",
                        "type":"invalid_parameter"
                    }
                }
            }
            logger.info('No Record Found In DB')
            return JSONResponse(content = error, status_code = 404)
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
                "message": "Error Occurred while reading schema",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content = error, status_code = 500)


@api_router.get("/schemas", responses = {**Get_all_schema_Successful_Response, **unauthorized_responses,
                                         **Unexpected_error_responses})
async def Lists_all_the_Schemas(db: Session = Depends(get_db), authorization: str = Header(None),
                                current_user: TokenPayload = Depends(get_current_user)):
    is_admin = is_user_admin(authorization)

    try:
        SchemaInfo_model_list = db.query(SchemasInfo).all()

        if SchemaInfo_model_list:
            return get_all_schema_response(SchemaInfo_model_list, is_admin)
        else:
            error = {
                "error":{
                    "code":404,
                    "message":"No records available",
                    "status":"error",
                    "details":{
                        "reason":"No records available",
                        "type":"No records available"
                    }
                }
            }
            return JSONResponse(content = error, status_code = 404)

    except Exception as e:
        logger.error(f'Error occurred - {str(e)}')
        error = {
            "error":{
                "code":500,
                "message":f"Failed to fetch the records",
                "status":"error",
                "details":{
                    "reason":str(e),
                    "type":"unexpected_error"
                }
            }
        }
        return JSONResponse(content = error, status_code = 500)


@api_router.put("/schemas/update_new_schema/{schema_id}", include_in_schema = False,
                responses = {**Schema_Post_Successful_Response, **bad_request_responses, **unauthorized_responses,
                             **Resource_conflict_responses,
                             **Unexpected_error_responses})
async def make_old_schemas_info_active_create_new_schema_with_definition(schema_id: int,
                                                                         schema_info_obj: SchemaInfoSchema,
                                                                         db: Session = Depends(get_db),
                                                                         authorization: str = Header(None),
                                                                         current_user: TokenPayload = Depends(
                                                                             get_current_user)):
    is_admin = is_user_admin(authorization)

    try:
        schema_info = db.query(SchemasInfo).filter(SchemasInfo.id == schema_id).first()

        schema_info.definition = schema_info_obj.definition
        db.add(schema_info)
        db.commit()

        return schema_post_create_response(schema_info, is_admin)
    except Exception as e:
        logger.error(f'Error occurred - {str(e)}')
        error = {
            "error": {
                "code": 500,
                "message": "Failed to update the new schema",
                "status": "error",
                "details": {
                    "reason": str(e),
                    "type": "unexpected_error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)


@api_router.delete("/schemas/{SchemaName}",
                   responses = {**bad_request_responses, **unauthorized_responses, **Unexpected_error_responses})
async def Deletes_a_schema(SchemaName: str, db: Session = Depends(get_db),
                           current_user: TokenPayload = Depends(get_current_user)):
    try:
        schema_model = db.query(SchemasInfo).filter(SchemasInfo.schema_name == SchemaName).first()
        if not schema_model:
            error = {
                "error":{
                    "code":404,
                    "message":"Item not found",
                    "status":"error",
                    "details":{
                        "reason":"Item not found",
                        "type":"invalid_parameter"
                    }
                }
            }
            logger.info('No Record Found In DB')
            return JSONResponse(content = error, status_code = 404)

        schema_topic_association_model = db.query(SchemaTopicAssociation).filter(
            SchemaTopicAssociation.schemas_info_id == schema_model.id).first()

        if schema_topic_association_model:
            logger.info(f"Schema topic association found for schema- {schema_model.schema_name}")
            topic_model = db.query(KafkaTopics).filter(
                KafkaTopics.id == schema_topic_association_model.kafka_topics_id).first()
            if topic_model:
                error = {
                    "error":{
                        "code":409,
                        "message":f"Schema is associated with the topic- {topic_model.kafka_topic_name}, can not "
                                  f"be deleted",
                        "status":"error",
                        "details":{
                            "reason":"resource conflict",
                            "type":"conflict"
                        }
                    }
                }
                return JSONResponse(content = error, status_code = 409)

        logger.info(f"Schema- {schema_model.schema_name} is not associated with any topic, hence going to delete")
        db.delete(schema_model)
        db.commit()
        logger.info(f"Deleted the schema- {schema_model.schema_name} successfully, since it is not associated "
                    f"with any topic")
        return ""

    except exc.SQLAlchemyError as e:
        logger.info(f"Data Base Connection Error- {e}")
        logger.debug(e)
        error = {
            "error":{
                "code":500,
                "message":f"Error occurred while connecting to database",
                "status":"string",
                "details":{
                    "reason":str(e),
                    "type":"Unexpected error"
                }
            }
        }
        return JSONResponse(content = error, status_code = 500)
    except Exception as e:
        error = {
            "error": {
                "code": 500,
                "message": "Error Occurred while deleting schema",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content = error, status_code = 500)


@api_router.get("/schemas/generate/{SchemaName}/{message}",
                responses = {**unauthorized_responses, **Unexpected_error_responses})
async def generate_schema(SchemaName: str, message: str, current_user: TokenPayload = Depends(get_current_user)):
    try:
        data = json.loads(message.replace("'", '"'))
        if data:
            avro_schema = infer_avro_schema(data, SchemaName)
            return {"status_code":200, "type":"AVRO", "definition":avro_schema}
        else:
            return {"status_message":"Message is empty"}
    except JSONDecodeError:
        error = {
            "error":{
                "code":422,
                "message":"schema must be a key-value pair",
                "status":"string",
                "details":{
                    "reason":"Error",
                    "type":"validation error"
                }
            }
        }
        return JSONResponse(content = error, status_code = 422)
    except Exception as e:
        error = {
            "error":{
                "code":422,
                "message":f"schema should contain require fields - name, type, fields, {str(e)}",
                "status":"string",
                "details":{
                    "reason":"Error",
                    "type":"validation error"
                }
            }
        }
        return JSONResponse(content = error, status_code = 422)

    except Exception as e:
        error = {
            "error": {
                "code": 500,
                "message": "Error Occurred while generating schema",
                "status": "string",
                "details": {
                    "reason": str(e),
                    "type": "Unexpected error"
                }
            }
        }
        return JSONResponse(content=error, status_code=500)

