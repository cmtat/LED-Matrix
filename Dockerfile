FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl ca-certificates libwebp-dev && rm -rf /var/lib/apt/lists/*

RUN curl -L -o /tmp/pixlet.tar.gz https://github.com/tidbyt/pixlet/releases/download/v0.34.0/pixlet_0.34.0_linux_amd64.tar.gz \
    && tar -xzf /tmp/pixlet.tar.gz -C /usr/local/bin \
    && chmod +x /usr/local/bin/pixlet \
    && rm /tmp/pixlet.tar.gz

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5050

CMD ["python3", "app.py"]