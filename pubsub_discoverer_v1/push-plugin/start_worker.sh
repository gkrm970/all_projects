#!/bin/sh
echo 'Inside start worker'

# Get the custom worker name from the environment variable
WORKER_NAME="$CUSTOM_WORKER_NAME"
QUEUE_NAME="$CUSTOM_QUEUE_NAME"

# Concurrency value need to be changed based on number of workers that needed for parallel processing
# concurrency to 1 is useful in scenarios where you want to ensure that tasks are processed in a specific order
celery -A app_worker worker --concurrency=1 -l debug -n "$WORKER_NAME" -Q $QUEUE_NAME
