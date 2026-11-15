FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY capcut_mcp ./capcut_mcp
RUN pip install --no-cache-dir .
ENV MCP_TRANSPORT=http HOST=0.0.0.0 PORT=8000 CAPCUT_OUTPUT_DIR=/data
EXPOSE 8000
CMD ["capcut-mcp"]
