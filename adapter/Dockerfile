FROM python:3.12-slim
WORKDIR /app
COPY adapter/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY adapter/*.py /app/adapter/
COPY adapter/profiles /app/adapter/profiles
RUN useradd --create-home adapter
USER adapter
ENV PYTHONUNBUFFERED=1
EXPOSE 8002
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import os, urllib.request; r=urllib.request.Request('http://127.0.0.1:8002/health', headers={'X-Internal-Token':os.environ['ENERGY_ADAPTER_TOKEN']}); urllib.request.urlopen(r, timeout=4).close()"
ENTRYPOINT ["python", "-m", "adapter"]
CMD ["serve"]
