# Sagashi justfile

default: fmt lint isort

# Format code with ruff (replaces black)
fmt:
    uv run ruff format .

# Lint and auto-fix issues
lint:
    uv run ruff check --fix .

# Sort imports (ruff's isort implementation)
isort:
    uv run ruff check --select I --fix .

# Verify everything passes without changing files (for CI)
check:
    uv run ty check .
    uv run ruff format --check .
    uv run ruff check --extend-ignore I .
    uv run ruff check --select I .

# Run tests
test:
    uv run pytest tests -q

# Start the server locally (uses .env)
run:
    @if [ -f .env ]; then set -a; source .env; set +a; fi && uv run sagashi

# Crawler verification
crawl-check:
    uv run crawl4ai-doctor
    curl -s https://www.example.com | grep -q "Example Domain"

# Sync dependencies
sync:
    uv sync --dev

# Build Docker image
docker-build:
    docker build -t sagashi:latest .

# Run Sagashi in Docker
docker-run:
    docker run -p 3000:3000 --env-file .env sagashi:latest

# Run tests in Docker
docker-test:
    docker run --rm --env-file .env sagashi:latest uv run pytest tests -q
