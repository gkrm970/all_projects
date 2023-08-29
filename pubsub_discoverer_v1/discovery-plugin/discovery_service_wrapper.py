import logging
import os

from utils import cron_scheduler_config as config
from utils.logger import LoggerHandler
from utils.utils import schedular_frequency_value
from utils.blocking_scheduler import SchedulerJob
from tinaa.utils.v1.auth_handler import AuthHandler, AuthenticationException

logger = logging.getLogger('discovery-service-plugin')

AUTH_TOKEN_URL = os.getenv('AUTH_TOKEN_URL', 'http://dummy/token')
CLIENT_ID = os.getenv('CLIENT_ID', 'test')
CLIENT_SECRET = os.getenv('CLIENT_SECRET', 'test')
auth_config = {'oauth2': {'oauth2_client_id': CLIENT_ID, 'oauth2_client_secret': CLIENT_SECRET, 'oauth2_token_url': AUTH_TOKEN_URL}}
auth_obj = AuthHandler(auth_config['oauth2'], logger)


def main():
    log_level = config.Config.LOG_LEVEL
    LoggerHandler('discovery-service-plugin', log_level).setup_logger()
    logger.info('************** Blocking Scheduler - Discovery Plugin service ******************')
    logger.info('Fetching access token to use authentication')

    env = os.getenv('ENV', 'local')

    if env == 'local':
        logger.info('Starting scheduler using local configuration')
        local_config = config.LocalConfig()
        SchedulerJob(local_config).blockingscheduler(schedular_frequency_value, auth_obj)
    elif env == 'development':
        logger.info('Starting scheduler using development configuration')
        dev_config = config.DevelopmentConfig()
        SchedulerJob(dev_config).blockingscheduler(schedular_frequency_value, auth_obj)
    elif env == 'staging':
        logger.info('Starting scheduler using staging configuration')
        staging_config = config.StagingConfig()
        SchedulerJob(staging_config).blockingscheduler(schedular_frequency_value, auth_obj)
    elif env == 'production':
        logger.info('Starting scheduler using production configuration')
        prod_config = config.ProductionConfig()
        SchedulerJob(prod_config).blockingscheduler(schedular_frequency_value, auth_obj)
    elif env == 'testing':
        logger.info('Starting scheduler using test configuration')
        test_config = config.TestingConfig()
        SchedulerJob(test_config).blockingscheduler(schedular_frequency_value, auth_obj)
    else:
        logger.error('Invalid environment is provided')


if __name__ == '__main__':
    main()

