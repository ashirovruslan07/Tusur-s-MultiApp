FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV MULTIAPP_DB_PATH=/app/data/multi_app.sqlite
ENV MULTIAPP_TIMEZONE=Asia/Tomsk
ENV TZ=Asia/Tomsk

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server ./server
COPY public ./public
COPY sql.txt ./sql.txt

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
