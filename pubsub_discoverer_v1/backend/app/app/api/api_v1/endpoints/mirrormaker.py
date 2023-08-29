import ast

from typing_extensions import Annotated
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy.orm import Session
from fastapi import Depends, HTTPException, status, APIRouter

from app.api.utils.security import logger
from app.responses.return_api_responses import unauthorized_responses
from app.schemas.token import TokenPayload
from app.api.deps import get_db, get_current_user
from app.models.models import FedKafkaTopics, MirrorMaker
from app.schemas.mirror_maker_schema import \
    MirrorMakerCreate, MirrorMakerUpdate, MirrorMakerCreateDisplay

# router = APIRouter()

router = APIRouter(prefix="/mirror-maker")

# def auth_and_db_access(db: Session = Depends(get_db),
#                        current_user: TokenPayload = Depends(get_current_user)):
#     return db,current_user


# Create a reusable function to handle database operations
def create_or_update_mirror_maker(schema_info: MirrorMakerCreate, db: Session):
    ex_worker_model = db.query(MirrorMaker).filter(
        MirrorMaker.subscription_name == schema_info.subscription_name).first()
    if ex_worker_model:
        MirrorMaker.subscription_name = schema_info.subscription_name
        MirrorMaker.source_topic_name = schema_info.source_topic_name
        MirrorMaker.destination_topic_name = schema_info.destination_topic_name
        MirrorMaker.fed_kafka_bus_name = schema_info.fed_kafka_bus_name
        MirrorMaker.auth_type = schema_info.auth_type
        db.add(ex_worker_model)
        db.commit()
        db.refresh(ex_worker_model)
    else:
        worker_model = MirrorMaker()
        worker_model.subscription_name = schema_info.subscription_name
        worker_model.source_topic_name = schema_info.source_topic_name
        worker_model.destination_topic_name = schema_info.destination_topic_name
        worker_model.fed_kafka_bus_name = schema_info.fed_kafka_bus_name
        worker_model.auth_type = schema_info.auth_type
        db.add(worker_model)
        db.commit()

    worker_dict = {
        "subscription_name": schema_info.subscription_name,
        "source_topic_name": schema_info.source_topic_name,
        "destination_topic_name": schema_info.destination_topic_name,
        "fed_kafka_bus_name": schema_info.fed_kafka_bus_name,
        "auth_type": schema_info.auth_type
    }

    return worker_dict


# Create mirror-maker endpoint
@router.post("/", response_model=MirrorMakerCreateDisplay, responses={**unauthorized_responses})
def start_mirror_maker(schema_info: MirrorMakerCreate, db: Session = Depends(get_db),
                       current_user: TokenPayload = Depends(get_current_user)):
    try:
        logger.info(f'/mirror-maker/create_service: {schema_info}')
        worker_dict = create_or_update_mirror_maker(schema_info, db)

        logger.info(f'Mirror_maker details: {worker_dict}')
        return JSONResponse(
            content={
                "status_code": 201,
                "message": f"Successfully created : {worker_dict}",
            },
            status_code=status.HTTP_201_CREATED,
        )
    except Exception as e:
        logger.error(f'error occurred - {str(e)}', exc_info=True)
        return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal socketserver error")


# Delete mirror-maker endpoint by name
@router.delete("/by-topic/{topic_name}", responses={**unauthorized_responses})
def delete_topic(topic_name=Field(alias="TopicName"), db: Session = Depends(get_db),
                 current_user: TokenPayload = Depends(get_current_user)):
    try:
        topic_response = db.query(FedKafkaTopics).filter(FedKafkaTopics.kafka_topic_name == topic_name).first()
        logger.info(
            f"topic_response:{topic_response.status_code}:{topic_response}"
        )
        if topic_response.status_code == 200:
            topic_json = topic_response.json()
            logger.info(f"topic_json:{topic_json}")
            bus_name = topic_json["kafka_bus_name"]
            bus_name_txt = ast.literal_eval(bus_name)[0]  # This is the string format of bus name
            mirrored_topic = f'upubsub.{bus_name_txt}.{topic_name}'
            logger.info(f'Mirrored_topic :{mirrored_topic}')

            # delete_response = requests.delete(delete_topic_url, headers=headers)
            delete_response = db.query(FedKafkaTopics).filter(
                FedKafkaTopics.kafka_topic_name == mirrored_topic).delete()

            if delete_response.status_code == 200:
                delete_json = delete_response.json()
                logger.info(f"delete_json:{delete_json}")
                logger.info(f"topic_name:{mirrored_topic} deleted successfully")
                return HTTPException(status_code=status.HTTP_200_OK,
                                     detail=f"Mirror maker topic :{mirrored_topic} deleted successfully.")
            else:
                logger.error(
                    f"Error while deleting for topic - {mirrored_topic},exc_info=True"
                )
                return JSONResponse(
                    content={
                        "status_code": 500,
                        "message": f"Error while deleting for topic - {mirrored_topic}",
                    },
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        elif topic_response.status_code == 401:
            logger.error("Authorization issue")
            return JSONResponse(
                content={"status_code": 401, "message": "Authorization issue"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        elif topic_response.status_code == 500:
            logger.error("Internal server error")
            return JSONResponse(
                content={"status_code": 500, "message": "Internal server error"},
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        else:
            logger.error("unable to fetch topic_name")
            return JSONResponse(
                content={
                    "status_code": 409,
                    "message": f"Resource Conflict, topic - {topic_name} is not exist",
                },
                status_code=status.HTTP_409_CONFLICT,
            )

    except Exception as e:
        logger.exception(f'Error occurred while connecting to delete endpoint :{e}')
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


# Get all record mirror-maker endpoint
@router.get("/", responses={**unauthorized_responses})
def get_all_mirror_maker_records(db: Session =Depends(get_db),current_user: TokenPayload =Depends(get_current_user)):
    try:
        mirror_maker_response = db.query(MirrorMaker).all()
        mirrored_topics = []
        if mirror_maker_response:
            for get_all_mirror_maker_topic in mirror_maker_response:
                mirrored_topics.append(get_all_mirror_maker_topic)
                return {"Retrieved mirror maker records successfully": mirrored_topics}
        else:
            logger.error("Records not found")
            return JSONResponse(
                content={
                    "status_code": 404,
                    "message": "Mirror maker record not found",
                },
                status_code=status.HTTP_404_NOT_FOUND,
            )
    except Exception as e:
        logger.exception(f"Error occurred while fetching mirror maker records from db {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


# Get by subscription mirror-maker endpoint
@router.get("/by-subscription/{subscription_name}", responses={**unauthorized_responses})
def get_mirror_maker_record_by_subscription_name(subscription_name=Field(alias="SubscriptionName"),
                                                 db: Session = Depends(get_db),
                                                 current_user: TokenPayload = Depends(get_current_user)):
    try:
        mirror_maker_object = db.query(MirrorMaker).filter(
            MirrorMaker.subscription_name == subscription_name).first()

        if mirror_maker_object:
            mirror_maker_record = {
                "id": mirror_maker_object.id,
                "subscription_name": mirror_maker_object.Subscription_name,
                "Source_topic_name": mirror_maker_object.Source_topic_name,
                "Destination_topic_name": mirror_maker_object.Destination_topic_name,
                "Fed_kafka_bus_name": mirror_maker_object.Fed_kafka_bus_name,
                "Auth_type": mirror_maker_object.Auth_type
            }
            return JSONResponse(
                content={
                    "status_code": 200,
                    "message": {"Retried mirror_maker_record successfully": mirror_maker_record},
                },
                status_code=status.HTTP_200_OK,
            )
        else:
            return JSONResponse(
                content={
                    "status_code": 404,
                    "message": {"Mirror-maker record not found for given subscription_name": subscription_name},
                },
                status_code=status.HTTP_404_NOT_FOUND,
            )

    except Exception as e:
        logger.exception(f"Error occurred while fetching mirror maker record from db {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


# Get by id mirror-maker endpoint.
@router.get("/{id}", responses={**unauthorized_responses})
def get_mirror_maker_record_by_id(id: int, db: Session = Depends(get_db),
                                  current_user: TokenPayload = Depends(get_current_user)):
    try:
        mirror_maker_object = db.query(MirrorMaker).filter(MirrorMaker.id == id).first()

        if mirror_maker_object:
            mirror_maker_record = {
                "id": mirror_maker_object.id,
                "subscription_name": mirror_maker_object.Subscription_name,
                "Source_topic_name": mirror_maker_object.Source_topic_name,
                "Destination_topic_name": mirror_maker_object.Destination_topic_name,
                "Fed_kafka_bus_name": mirror_maker_object.Fed_kafka_bus_name,
                "Auth_type": mirror_maker_object.Auth_type
            }
            return JSONResponse(
                content={
                    "status_code": 200,
                    "message": f"Retried mirror_maker_record successfully :{mirror_maker_record}",
                },
                status_code=status.HTTP_200_OK, )
        else:
            logger.info("Details not found for given Mirror_maker id")
            return JSONResponse(
                content={
                    "status_code": 404,
                    "message": "Details not found for given Mirror_maker id",
                },
                status_code=status.HTTP_404_NOT_FOUND,
            )

    except Exception as e:
        logger.exception(f"Error occurred while fetching mirror maker record from db {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


# Delete by id mirror-maker endpoint.
@router.delete("/{id}", responses={**unauthorized_responses})
def delete_mirror_maker_record_by_id(id: int, db: Session = Depends(get_db),
                                     current_user: TokenPayload = Depends(get_current_user)):
    try:
        mirror_maker_object = db.query(MirrorMaker).filter(MirrorMaker.id == id).delete()

        if mirror_maker_object:
            mirror_maker_record = {
                "id": mirror_maker_object.id,
                "subscription_name": mirror_maker_object.subscription_name,
                "Source_topic_name": mirror_maker_object.source_topic_name,
                "Destination_topic_name": mirror_maker_object.destination_topic_name,
                "Fed_kafka_bus_name": mirror_maker_object.ded_kafka_bus_name,
                "Auth_type": mirror_maker_object.auth_type
            }
            return JSONResponse(
                content={"status_code": 200, "mirror_maker_record delete successfully": mirror_maker_record},
                status_code=status.HTTP_200_OK, )
        else:
            logger.error(f"Unable to delete record for given mirror_maker id ")
            return JSONResponse({"Either of not found or unable to delete given mirror-maker id": id})
    except Exception as e:
        logger.exception(f"Error occurred while deleting mirror maker records from db {str(e)}", exch_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


# Update by id mirror-maker endpoint
@router.put("/{id}", responses={**unauthorized_responses})
def update_mirror_maker_record_by_id(id: int, schema_info: MirrorMakerUpdate,
                                     db: Session = Depends(get_db),
                                     current_user: TokenPayload = Depends(get_current_user)):
    try:
        mirror_maker_object = db.query(MirrorMaker).filter(MirrorMaker.id == id).first()

        if mirror_maker_object:
            # Update the mirror maker record with the provided data
            mirror_maker_object.subscription_name = schema_info.subscription_name
            mirror_maker_object.source_topic_name = schema_info.source_topic_name
            mirror_maker_object.destination_topic_name = schema_info.destination_topic_name
            mirror_maker_object.fed_kafka_bus_name = schema_info.fed_kafka_bus_name
            mirror_maker_object.auth_type = schema_info.auth_type
            db.commit()
            db.refresh(mirror_maker_object)

            updated_record = {
                "id": mirror_maker_object.id,
                "subscription_name": mirror_maker_object.subscription_name,
                "source_topic_name": mirror_maker_object.source_topic_name,
                "destination_topic_name": mirror_maker_object.destination_topic_name,
                "fed_kafka_bus_name": mirror_maker_object.fed_kafka_bus_name,
                "auth_type": mirror_maker_object.auth_type
            }

            return {"Updated mirror_maker_record successfully": updated_record}
        else:
            logger.error(f"Given mirror maker id {id} not found in the mirror_maker table")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Mirror maker record not found")

    except Exception as e:
        logger.exception(f"Error occurred while updating mirror maker records from db {str(e)}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


@router.patch("/{id}", responses={**unauthorized_responses})
async def patch_mirror_maker_record_by_id(id: int, schema_info: MirrorMakerUpdate,
                                          db: Session = Depends(get_db),
                                          current_user: TokenPayload = Depends(get_current_user)):
    try:
        mirror_maker_object = db.query(MirrorMaker).filter(MirrorMaker.id == id).first()
        if mirror_maker_object:
            # Update the mirror maker record with the provided data
            if schema_info.subscription_name:
                mirror_maker_object.subscription_name = schema_info.subscription_name
            elif schema_info.source_topic_name:
                mirror_maker_object.source_topic_name = schema_info.source_topic_name
            elif schema_info.destination_topic_name:
                mirror_maker_object.destination_topic_name = schema_info.destination_topic_name
            elif schema_info.fed_kafka_bus_name:
                mirror_maker_object.fed_kafka_bus_name = schema_info.fed_kafka_bus_name
            elif schema_info.auth_type:
                mirror_maker_object.auth_type = schema_info.auth_type

            db.commit()
            db.refresh(mirror_maker_object)

            updated_record = {
                "id": mirror_maker_object.id,
                "subscription_name": mirror_maker_object.subscription_name,
                "source_topic_name": mirror_maker_object.source_topic_name,
                "destination_topic_name": mirror_maker_object.destination_topic_name,
                "ded_kafka_bus_name": mirror_maker_object.fed_kafka_bus_name,
                "auth_type": mirror_maker_object.auth_type
            }

            return {"Patched mirror_maker_record successfully": updated_record}
        else:
            return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"message": f"Mirror maker record not "
                                                                                           f"found for given id {id}"}, )

    except Exception as e:
        logger.exception(f'Error occurred while updating the record: {str(e)}', exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")
