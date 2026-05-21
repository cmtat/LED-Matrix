import os


bind = f"0.0.0.0:{os.environ.get('FLASK_PORT', '5050')}"
worker_class = "gthread"
workers = int(os.environ.get("GUNICORN_WORKERS", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "0"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "75"))
accesslog = "-"
errorlog = "-"
capture_output = True


def post_worker_init(worker):
    from app import start_runtime_services

    start_runtime_services()
