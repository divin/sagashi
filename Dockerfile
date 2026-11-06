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
    # Playwright dependencies
    libwoff1 \
    libopus0 \
    libwebpdemux2 \
    libenchant1c2a \
    libgudev-1.0-0 \
    libsecret-1-0 \
    libhyphen0 \
    libgdk-pixbuf2.0-0 \
    libegl1 \
    libgl1 \
    libgbm1 \
    libasound2 \
    # Crawl4AI requirements
    ffmpeg \
    libsm6 \
    libxext6 \
    # Cleanup
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy source
COPY src ./src

# Install the package
RUN uv pip install -e . --no-build-isolation

# Install Chromium for Crawl4AI using Playwright
RUN uv run python -m playwright install chromium --with-deps

# Set environment variables
ENV CRAWL4AI_NO_CLOUD_NOTICE=1
ENV PYTHONUNBUFFERED=1

# Switch to non-root user
USER appuser

# Expose the FastMCP server port (can be remapped by deployer)
EXPOSE 3000

# Default command
CMD ["uv", "run", "sagashi"]
