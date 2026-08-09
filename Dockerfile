FROM node:22-bookworm-slim

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts
COPY . .

ENV COPILOT_HOST=0.0.0.0
ENV COPILOT_PORT=8765
EXPOSE 8765

CMD ["python3", "scripts/dev.py"]
