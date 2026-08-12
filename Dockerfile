FROM python:3.14-slim

WORKDIR /app

RUN pip install --no-cache-dir flask pymssql apscheduler

COPY server.py .

VOLUME ["/logs"]
VOLUME ["/backups"]
VOLUME ["/mssql-backup"]

EXPOSE 5000

ENV LOG_DIR=/logs \
    SQL_SERVER=sqlserver \
    SQL_PORT=1433 \
    SQL_USER=sa \
    SQL_DB=NL9_Traffic \
    BACKUP_DIR=/backups \
    BACKUP_ZIP_NAME=NL9_Traffic.zip \
    BACKUP_BAK_NAME=NL9_Traffic.BAK \
    BAK_STAGE_DIR=/mssql-backup \
    BAK_SQLSERVER_DIR=/var/opt/mssql/backup

CMD ["python", "server.py"]
