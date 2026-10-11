FROM python:3.11-slim

# 默认使用清华 PyPI 镜像(国内网络);海外构建可 --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip config set global.index-url ${PIP_INDEX_URL}
RUN pip install --no-cache-dir musicdl fastapi "uvicorn[standard]" requests

WORKDIR /app
COPY main.py /app/main.py

VOLUME /music
EXPOSE 8700
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8700"]
