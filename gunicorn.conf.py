# Conversations are process-local. Do not increase workers before persisting them.
bind = '0.0.0.0:8000'
workers = 1
worker_class = 'gthread'
threads = 8
# Must stay above AGENT_REQUEST_BUDGET_SECONDS in api/agent.py, which is what
# actually bounds a slow AI turn. This is only the backstop.
timeout = 210
graceful_timeout = 210
accesslog = None  # URLs can contain invitation and action tokens.
errorlog = '-'
preload_app = False
