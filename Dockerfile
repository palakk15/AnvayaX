FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY configs ./configs
COPY dashboard.py ./
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "dashboard.py", "--server.address=0.0.0.0", "--server.headless=true"]
