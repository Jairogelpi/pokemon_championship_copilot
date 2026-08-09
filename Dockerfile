FROM python:3.12-slim

WORKDIR /app
COPY . .

ENV COPILOT_HOST=0.0.0.0
ENV COPILOT_PORT=8765
EXPOSE 8765

CMD ["python", "scripts/dev.py"]
