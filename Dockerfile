FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DB_PATH=/app/data/bot.db
ENV STORAGE_PATH=/app/storage
ENV TZ=Europe/Moscow

CMD ["python", "bot.py"]
