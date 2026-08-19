FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
EXPOSE 5001
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5001", "--log-config", "/dev/null"]
