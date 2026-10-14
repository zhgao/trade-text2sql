FROM python:3.12-slim

WORKDIR /app

# 依赖层单独缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码（SQLite 库启动时自动从 schema.sql/seed.sql 重建，无需 volume）
COPY backend ./backend
COPY frontend ./frontend

EXPOSE 8000

CMD ["python", "-m", "backend.main"]
