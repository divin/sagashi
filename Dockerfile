# Sagashi Docker image
# uv-based Python 3.12 image with Chromium for Crawl4AI

FROM python:3.12-slim

# Set non-root user
RUN useradd --create-home --shell /bin/bash appuser

# Install system dependencies for Chromium and Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends \
    nodejs \
    # Chromium dependencies
    chromium \
    chromium-driver \
    # Chromium/Playwright shared-library dependencies (Debian trixie names)
    libwoff1 \
    libopus0 \
    libwebpdemux2 \
    libenchant-2-2 \
    libgudev-1.0-0 \
    libsecret-1-0 \
    libhyphen0 \
    libgdk-pixbuf-2.0-0 \
    libegl1 \
    libgl1 \
    libgbm1 \
    libasound2t64 \
    # Crawl4AI requirements
    ffmpeg \
    libsm6 \
    libxext6 \
    # Cleanup
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy source (README.md is needed by the build backend for package metadata)
COPY README.md ./
COPY src ./src

# Install the package
RUN uv sync --frozen --no-dev

# Install Chromium for Crawl4AI using Playwright into a shared location
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN uv run python -m playwright install chromium --with-deps \
    && chmod -R a+rX /opt/ms-playwright

# Set environment variables
ENV CRAWL4AI_NO_CLOUD_NOTICE=1
ENV PYTHONUNBUFFERED=1
# Bind to all interfaces on the exposed port inside the container
ENV HOST=0.0.0.0
ENV PORT=3000

# Switch to non-root user
USER appuser

# Expose the FastMCP server port (can be remapped by deployer)
EXPOSE 3000

# Run the installed console script directly — do not let uv re-sync the
# root-owned venv at startup (appuser cannot write to it)
ENV PATH="/app/.venv/bin:${PATH}"
CMD ["sagashi"]
