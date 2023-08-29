def retry(times, exceptions):
    """
    retry decorator method used to reset access token & retry response n times.

    Retry Decorator
    Retries the wrapped method `times` times if the exceptions listed
    in ``exceptions`` are thrown
    :param times: The number of times to repeat the wrapped function/method
    :type times: Int
    :param exceptions: Lists of exceptions that trigger a retry attempt
    :type exceptions: Tuple of Exceptions
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            attempt = 1
            while attempt < times:
                try:
                    if attempt > 1:
                        auth_obj = args[0]
                        auth_obj.get_access_token()
                    return func(*args, **kwargs)
                except exceptions as e:
                    print(
                        f'Exception thrown when attempting to run {func}, attempt '
                        f'{attempt} of {times} => {e}'
                    )
                    attempt += 1
            return func(*args, **kwargs)

        return wrapper

    return decorator
