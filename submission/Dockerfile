FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY project/admin/requirements.txt /app/project/admin/requirements.txt
COPY project/llm/requirements-llm.txt project/llm/requirements-bot.txt /app/project/llm/
COPY requirements-dev.txt requirements-validation.txt /app/
COPY tools/data-api/requirements.txt /app/tools/data-api/requirements.txt
RUN pip install --no-cache-dir -r project/admin/requirements.txt -r project/llm/requirements-bot.txt -r requirements-dev.txt -r requirements-validation.txt
RUN groupadd --system appuser && useradd --system --gid appuser --no-create-home appuser
COPY --chown=appuser:appuser project /app/project
COPY --chown=appuser:appuser tools /app/tools
COPY --chown=appuser:appuser submission /app/submission
COPY --chown=appuser:appuser docs /app/docs
COPY --chown=appuser:appuser DATA-API.yaml openapi.yaml /app/
RUN mkdir -p /app/project/cache /app/project/logs && chown -R appuser:appuser /app/project/cache /app/project/logs
USER appuser
CMD ["uvicorn", "project.miniapp.app:app", "--host", "0.0.0.0", "--port", "8766", "--no-access-log"]
