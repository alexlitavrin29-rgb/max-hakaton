FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY project/admin/requirements.txt /app/requirements-admin.txt
COPY project/llm/requirements-llm.txt project/llm/requirements-bot.txt /app/requirements/
RUN pip install --no-cache-dir -r /app/requirements-admin.txt -r /app/requirements/requirements-bot.txt
RUN groupadd --system appuser && useradd --system --gid appuser --no-create-home appuser
COPY --chown=appuser:appuser project/llm /app/project/llm
COPY --chown=appuser:appuser project/miniapp /app/project/miniapp
COPY --chown=appuser:appuser project/data/knowledge /app/project/data/knowledge
RUN mkdir -p /app/project/cache && chown appuser:appuser /app/project/cache
USER appuser
CMD ["uvicorn", "project.miniapp.app:app", "--host", "0.0.0.0", "--port", "8766", "--no-access-log", "--no-proxy-headers"]
