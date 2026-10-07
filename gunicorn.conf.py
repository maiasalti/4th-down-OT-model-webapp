# gunicorn settings for Render's free instance (512 MB RAM, 0.1 CPU).
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
# Import the app (and load every model) once in the master before forking, so
# the port only opens when the app can actually answer.
preload_app = True
# One process keeps memory low; a few threads let /health and static files be
# served while an analysis is running.
workers = 1
worker_class = "gthread"
threads = 4
timeout = 60
keepalive = 5
accesslog = "-"
