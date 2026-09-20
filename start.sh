#!/bin/bash
# start.sh — Start all services for AI Calling Agent

set -e

echo "🚀 Starting AI Cold Calling Agent..."

# Activate virtual environment
source ~/ai-caller-env/bin/activate

# Check .env exists
if [ ! -f .env ]; then
    echo "❌ .env file not found! Copy .env.example to .env and fill in your API keys."
    exit 1
fi

# Start Redis (if not running)
if ! pgrep -x "redis-server" > /dev/null; then
    echo "📦 Starting Redis..."
    redis-server --daemonize yes
    sleep 1
fi

echo "✅ Redis running"

# Start Celery worker in background unless one is already running.
mkdir -p logs
if [[ -f logs/celery.pid ]]; then
    celery_pid=$(<logs/celery.pid)
    if [[ "$celery_pid" =~ ^[0-9]+$ ]] && kill -0 "$celery_pid" 2>/dev/null; then
        echo "✅ Celery worker already running — skipping"
    else
        rm -f logs/celery.pid
    fi
fi

if [[ ! -f logs/celery.pid ]]; then
    echo "⚙️  Starting Celery worker..."
    celery -A app.celery_worker worker --loglevel=info --detach \
        --logfile=logs/celery.log \
        --pidfile=logs/celery.pid
    echo "✅ Celery worker started"
fi

# Start multi_agent_manager only if not already running (systemd may already manage it)
if pgrep -f "app/multi_agent_manager.py" > /dev/null 2>&1; then
    echo "✅ PIOPIY Agent (multi_agent_manager) already running — skipping"
else
    echo "🤖 Starting PIOPIY Agent (multi_agent_manager)..."
    mkdir -p logs
    /root/ai-caller-env/bin/python app/multi_agent_manager.py >> logs/multi_agent_manager.log 2>&1 &
    echo $! > logs/multi_agent_manager.pid
    sleep 2
    echo "✅ PIOPIY Agent started"
fi

# Start FastAPI server unless another instance already owns the port.
if ss -ltn "sport = :8000" | grep -q LISTEN; then
    echo "✅ FastAPI server already running on port 8000 — skipping"
    exit 0
fi

echo "🌐 Starting FastAPI server..."
uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info \
    --access-log

echo "✅ All services started!"
