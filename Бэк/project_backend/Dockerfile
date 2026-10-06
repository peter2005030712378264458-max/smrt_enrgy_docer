FROM python:3.11-slim

WORKDIR /app/project_backend

ENV RUNNING_IN_DOCKER=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["sh", "/app/project_backend/start_server.sh"]
