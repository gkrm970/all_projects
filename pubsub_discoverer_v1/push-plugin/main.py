import os
from flask import Flask, jsonify
from utils.logger import logger
from app_worker import basic_consume_loop, active_worker_controller, app as celery_app
import threading
import subprocess
import time
from apscheduler.schedulers.background import BackgroundScheduler


app = Flask(__name__)
app.config['VERIFY_CERTIFICATES'] = False

scheduler = BackgroundScheduler()

port = os.getenv('PUSH_SERVICE_PORT', '8087')


@app.route('/start/push-service/<SubscriptionName>', methods=['POST'])
def start_push_service(SubscriptionName):
    #os.environ['SUBSCRIPTION_NAME'] = SubscriptionName
    #logger.info(f'subscription_name:{os.environ.get("SUBSCRIPTION_NAME")}')
    try:
        # Start the worker process in the background
        os.environ['CUSTOM_WORKER_NAME'] = f'{SubscriptionName}_worker'
        os.environ['CUSTOM_QUEUE_NAME'] = f'{SubscriptionName}_queue'
        time.sleep(2)
        process = subprocess.Popen(['sh', './start_worker.sh'])
        # process = start_celery_worker(str(SubscriptionName))
        logger.info(f'Start celery process: {process}')
        # Check if the process was started successfully
        if process.returncode is None:
            logger.info(f'Celery workers started successfully, proceeding to start a task asynchronously')
            # non-blocking call, which means they do not wait for the task to complete
            time.sleep(5)
            celery_app.control.add_consumer(f'{SubscriptionName}_queue')
            result = basic_consume_loop.apply_async([SubscriptionName], queue=f'{SubscriptionName}_queue')
            #result = basic_consume_loop.delay(SubscriptionName)
            # id: Returns the unique identifier of the task.
            if result.id:
                logger.info("Task was sent successfully")
                return jsonify({"status_code": 200, "message": f'Push subscription service created for subscription - {SubscriptionName} successfully'})
            else:
                logger.error("Failed to send task.")
                return jsonify({"status_code": 500, "message": f'Failed to create push service for subscription - {SubscriptionName}'})
        else:
            return jsonify(
                {"status_code": 500, "message": f'Failed to create push service for subscription - {SubscriptionName}'})
    except Exception as err:
        logger.error(f'Error while creating push service for subscription - {SubscriptionName}')
        logger.info(f'{str(err)}')
        return jsonify(
            {"status_code": 500, "message": f'Error while creating push subscription for - {SubscriptionName}'})


# Start the background task when the Flask app is initialized
def initialize_background_task():
    # Schedule the task to run every 5 minutes
    logger.info('BackgroundScheduler strated')
    scheduler.add_job(active_worker_controller, 'interval', seconds=30)
    scheduler.start()



if __name__ == '__main__':
    initialize_background_task()
    app.run(host='0.0.0.0', debug=True, port=port, use_reloader=False)
