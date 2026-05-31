# 使用官方 Python 3.9 基础镜像
FROM python:3.9-slim

# 设置容器内的工作目录
WORKDIR /app

# 先将依赖文件复制进去，以便单独安装依赖（这一步可以更好地利用 Docker 的缓存）
COPY requirements.txt .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 将项目所有文件复制到工作目录
COPY . .

# 声明运行时容器服务的端口
EXPOSE 80

# 启动 FastAPI 服务，使用 uvicorn 服务器
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "80"]
