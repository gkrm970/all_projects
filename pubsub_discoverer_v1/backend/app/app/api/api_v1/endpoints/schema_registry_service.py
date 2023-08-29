from fastapi import APIRouter, Depends, HTTPException
from app.models.models import SchemaRegistry
from sqlalchemy.orm import Session
from app.schemas.entity import SchemaRegistry as SchemaRegisterySchema
from app.api.deps import get_db, get_current_user
from app.schemas.token import TokenPayload
from app.core.config import settings
from tinaa.logger.v1.tinaa_logger import get_app_logger
from app.responses.return_api_responses import unauthorized_responses
from starlette.responses import JSONResponse

API_NAME = "Schema Registry Api"
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

router = APIRouter()


@router.post("/schemaregistry/create", status_code=201, responses={**unauthorized_responses})
async def Creates_a_schemaregistery(schema_info: SchemaRegisterySchema, db: Session = Depends(get_db),
                                    current_user: TokenPayload = Depends(get_current_user)):
    """
          {
          "schema_registry_host": "string",
          "schema_registry_port": int,
          "schema_name": "string",

        }
    """
    try:
        # schemas_fields = json.dumps(schema_info.schemas_fields)
        schema_registery_model = SchemaRegistry()

        schema_registery_model.schema_registry_host = schema_info.schema_registry_host
        schema_registery_model.schema_registry_port = schema_info.schema_registry_port
        schema_registery_model.schema_registry_version = schema_info.schema_registry_version
        schema_registery_model.schemaregistry_certificate_location = schema_info.schemaregistry_certificate_location
        schema_registery_model.schemaregistry_key_location = schema_info.schemaregistry_key_location
                      
        db.add(schema_registery_model)
        db.commit()
        schemaregistery_dict = {
            # "schema_info_id":schema_model.id,
            "schema_registry_host": schema_info.schema_registry_host,
            "schema_registry_port": schema_info.schema_registry_port,
            "schema_registry_version": schema_info.schema_registry_version,
            "schemaregistry_certificate_location": schema_info.schemaregistry_certificate_location,
            "schemaregistry_key_location": schema_info.schemaregistry_key_location,
            "created_by": "Prodapt",
            "updated_by": "Prodapt",

        }
        logger.info(f'schemaregistery_dict: {schemaregistery_dict}')
        return schemaregistery_dict, successful_response(201)
    except Exception as e:
        logger.error(f'error occured - {str(e)}')
        error = {
            "error": {
                "code": 500,
                "message": "Unable to create schema registry information",
                "status": "error",
                "details": {
                    "reason": "Exception while storing schema registry information - {str(e)}",
                    "type": ""
                }
            }
        }
        return JSONResponse(content=error, status_code=500)


@router.get("/schemaregistry/all_records",  responses={**unauthorized_responses})
async def Lists_all_the_Schemasregistery( db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):
    logger.info('Start the schema api get all')
    try:
        logger.info('Inside get_all_schema registry')
        SchemaRegistry_model_list = db.query(SchemaRegistry).all()
        logger.info(f"SchemaRegistry_model_list: {SchemaRegistry_model_list}")
        schema_list = []
        for values in SchemaRegistry_model_list:
            schema_registery_dict = {
                            "id":values.id,
                            "schema_registry_host": values.schema_registry_host,
                            "schema_registry_port": values.schema_registry_port,
                            "schemaregistry_certificate_location": values.schemaregistry_certificate_location,
                            "schemaregistry_key_location": values.schemaregistry_key_location,
                            "schema_registry_version": values.schema_registry_version,
                            #"created_by":values.created_by,
                            #"updated_by":values.updated_by,
                            "created_datetime":values.created_datetime,
                            "updated_datetime":values.updated_datetime
            }
            schema_list.append(schema_registery_dict)
        schema_dict = {"schemas": schema_list}
        logger.info(f'schema_list: {schema_list}')
        return schema_dict
    except Exception as e:
        logger.error(f'error occured - {str(e)}')
        error = {
            "error": {
                "code": 500,
                "message": "Unable to fetch all schema registry information",
                "status": "error",
                "details": {
                    "reason": "Exception while fetching schema registry information",
                    "type": ""
                }
            }
        }
        return JSONResponse(content=error, status_code=500)


@router.get("/schemaregistry/{register_id}", status_code=200, responses={**unauthorized_responses})
async def Read_a_schemaregistery(register_id: int, db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):
    try:
        SchemaRegistry_model = db.query(SchemaRegistry).filter(SchemaRegistry.id == int(register_id)).first()
        if not SchemaRegistry_model:
            error = {
                "error": {
                    "code": 404,
                    "message": f"this Schema registry{register_id} Not Existed",
                    "status": f"Given register_id {register_id} Not Existed",
                    "details": {
                        "reason": f"this Schema registry{register_id} Not Existed",
                        "type": "invalid_parameter"
                    }
                }
            }
            return JSONResponse(content=error, status_code=404)
        logger.info(f'SchemaRegistry_model_obj: {SchemaRegistry_model}')
        if SchemaRegistry_model:
            schema_info_dict = {
                            "id":SchemaRegistry_model.id,
                            "schema_registry_host": SchemaRegistry_model.schema_registry_host,
                            "schema_registry_port": SchemaRegistry_model.schema_registry_port,
                            "schema_registry_version": SchemaRegistry_model.schema_registry_version,
                            "schemaregistry_certificate_location": SchemaRegistry_model.schemaregistry_certificate_location,
                            "schemaregistry_key_location": SchemaRegistry_model.schemaregistry_key_location,
                            #"created_by":SchemaRegistry_model.created_by,
                            #"updated_by":SchemaRegistry_model.updated_by,
                            "created_datetime":SchemaRegistry_model.created_datetime,
                            "updated_datetime":SchemaRegistry_model.updated_datetime
                              }
            logger.info(f"schema_info_dict: {schema_info_dict}")
            return schema_info_dict
        else:
            error = {
                "error": {
                    "code": 409,
                    "message": "Given Schema Registry ID is not exist in DB",
                    "status": "error",
                    "details": {
                        "reason": "Schema registry id is not exist in database",
                        "type": "invalid_parameter"
                    }
                }
            }
            logger.info('Given Schema Registry ID is not exist in DB')
            return JSONResponse(content=error, status_code=409)
    except Exception as e:
        logger.error(f'error occured - {str(e)}')
        error = {
            "error": {
                "code": 500,
                "message": "Unable to read schema registry information",
                "status": "error",
                "details": {
                    "reason": "Exception while storing schema registry information - {str(e)}",
                    "type": ""
                }
            }
        }
        return JSONResponse(content=error, status_code=500)


@router.put("/schemaregistry/{SchemaName}", responses={**unauthorized_responses})
async def updates_a_schemaregistery(SchemahostName: str,schema_info: SchemaRegisterySchema,db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):
    try:
        schema_model = db.query(SchemaRegistry).filter(SchemaRegistry.schema_registry_host == SchemahostName).first()

        if schema_model is None:
            raise http_exception()

        schema_dict = {
            "schema_registry_host": schema_info.schema_registry_host,
            "schema_registry_port": schema_info.schema_registry_port,
            "schemaregistry_certificate_location": schema_info.schemaregistry_certificate_location,
            "schemaregistry_key_location": schema_info.schemaregistry_key_location,
            "schema_registry_version": schema_info.schema_registry_version
        }
        logger.info(schema_dict)

        schema_model.schema_registry_host = schema_info.schema_registry_host
        schema_model.schema_registry_port = schema_info.schema_registry_port
        schema_model.schema_auth_type = schema_info.schema_registry_auth_type
        schema_model.schemaregistry_certificate_location = schema_info.schemaregistry_certificate_location
        schema_model.schemaregistry_key_location = schema_info.schemaregistry_key_location
        schema_model.schema_registry_version = schema_info.schema_registry_version
        #schema_model.schema_name = schema_info.schema_name
        db.add(schema_model)
        db.commit()
        return schema_dict, successful_response(200)
    except Exception as e:
        logger.error(f'error occured - {str(e)}')
        error = {
            "error": {
                "code": 500,
                "message": "Unable to update schema registry information",
                "status": "error",
                "details": {
                    "reason": "Exception while updating schema registry information",
                    "type": ""
                }
            }
        }
        return JSONResponse(content=error, status_code=500)


@router.delete("/schemaregistry/{register_id}", responses={**unauthorized_responses})
async def Deletes_a_schemaregistery(register_id: int,db: Session = Depends(get_db), current_user: TokenPayload = Depends(get_current_user)):
    try:
        SchemaRegistry_model = db.query(SchemaRegistry).filter(SchemaRegistry.id == int(register_id)).first()
        logger.info(f'SchemaRegistry_model_obj: {SchemaRegistry_model}')
        if SchemaRegistry_model:
            schema_model = db.query(SchemaRegistry).filter(SchemaRegistry.id == int(register_id)).delete()
            db.commit()
            SchemaRegistry_model = db.query(SchemaRegistry).filter(SchemaRegistry.id == int(register_id)).first()
            if SchemaRegistry_model is None:
                return successful_response(204)
            else:
                error = {
                   "error": {
                    "code": 500,
                    "message": "Unable to delete schema registry information",
                    "status": "error",
                    "details": {
                        "reason": "Schema registry deletion failed",
                        "type": ""
                        }
                    }
                }
                return JSONResponse(content=error, status_code=500)
        else:
            error = {
                "error": {
                    "code": 409,
                    "message": "Given Schema Registry ID is not exist in DB",
                    "status": "error",
                    "details": {
                        "reason": "Schema registry id is not exist in database",
                        "type": "invalid_parameter"
                    }
                }
            }
            logger.info('Given Schema Registry ID is not exist in DB')
            return JSONResponse(content=error, status_code=409)
    except Exception as e:
        logger.error(f'error occured - {str(e)}')
        error = {
            "error": {
                "code": 500,
                "message": "Unable to delete schema registry information",
                "status": "error",
                "details": {
                    "reason": "Exception while deleting schema registry information",
                    "type": ""
                }
            }
        }
        return JSONResponse(content=error, status_code=500)


def successful_response(status_code: int):
    return {
        'status': status_code,
        'transaction': 'Successful'
    }


def http_exception():
    return HTTPException(status_code=404, detail="Todo not found")
