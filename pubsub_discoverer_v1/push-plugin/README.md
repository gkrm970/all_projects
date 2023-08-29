# Start the beat periodic task scheduler
  celery -A app_worker beat

# Erase all messages from all known task queues
  celery -A app_worker purge beat

# Run the worker
  celery -A app_worker worker -l INFO

# Run the worker - with 2 worker concurrency
  celery -A app.celery worker -c 2 -l INFO

# Run the worker - with default queue and purge old messages
  celery -A app_worker worker -Q default -l INFO --purge

# start one or more workers in the background
  celery multi start w1 -A app_worker -l info

# restart workers
  celery multi restart w1 -A app_worker -l info

# stop workers aynchronously
  celery multi stop w1 -A app_worker -l info

# worker and beat load in one command
  celery -A app_worker worker -l info -B

