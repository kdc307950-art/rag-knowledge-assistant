FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
EXPOSE 5001
# 不要加 --log-config /dev/null：uvicorn 会把它当 logging fileConfig 解析，
# 空文件直接 RuntimeError("/dev/null is an empty file")，容器起不来。
# server.py 自己做了 logging.basicConfig，用默认日志配置即可。
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5001"]
