FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir flask

COPY server.py .

VOLUME ["/logs"]

EXPOSE 5000

ENV LOG_DIR=/logs

CMD ["python", "server.py"]
