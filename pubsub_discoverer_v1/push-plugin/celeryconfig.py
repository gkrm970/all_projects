from datetime import timedelta

from celery.schedules import crontab

accept_content = ['json', 'msgpack', 'yaml']
result_serializer = 'json'
task_serializer = 'json'

imports = 'app_worker'
result_expires = 30
timezone = 'UTC'

worker_send_task_event = False
task_ignore_result = True

# task will be killed after x seconds
#task_time_limit = 280
# task will raise exception SoftTimeLimitExceeded after x seconds
#task_soft_time_limit = 270

# task messages will be acknowledged after the task has been executed
task_acks_late = True

# One worker taks 10 tasks from queue at a time
worker_prefetch_multiplier = 10

# Maximum number of tasks a pool worker process can execute before it’s
# replaced with a new one.
worker_max_tasks_per_child = 5000

# Maximum amount of resident memory, in kilobytes, that may be
# consumed by a worker before it will be replaced by a new worker.
worker_max_memory_per_child = 500000  # 500MB
