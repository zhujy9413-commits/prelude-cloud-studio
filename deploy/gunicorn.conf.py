"""One application process owns the SQLite-backed generation worker."""
bind = '127.0.0.1:4180'
workers = 1
worker_class = 'gthread'
threads = 4
preload_app = False
reload = False
timeout = 120
graceful_timeout = 110
keepalive = 2
max_requests = 0
limit_request_line = 4094
limit_request_fields = 40
limit_request_field_size = 8190
forwarded_allow_ips = '127.0.0.1'
secure_scheme_headers = {'X-FORWARDED-PROTO': 'https'}
accesslog = None
errorlog = '-'
capture_output = False


def worker_exit(server, worker):
    application = getattr(worker, 'wsgi', None)
    if application is not None and hasattr(application, 'close'):
        application.close()
