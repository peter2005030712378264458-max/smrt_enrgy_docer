FROM python:3.12-slim
WORKDIR /app
COPY adapter/requirements.txt /tmp/adapter.txt
COPY ["Бэк/project_backend/requirements.txt", "/tmp/backend.txt"]
COPY ["Аналитический сервис/Analytical_service/requirements.txt", "/tmp/analytics.txt"]
RUN pip install --no-cache-dir -r /tmp/adapter.txt -r /tmp/backend.txt -r /tmp/analytics.txt
COPY adapter /app/adapter
COPY ["Бэк/project_backend", "/app/backend"]
COPY ["Аналитический сервис/Analytical_service", "/app/analytics"]
CMD ["python", "-m", "adapter.tests.run_checks"]
