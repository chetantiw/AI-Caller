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

# Start Celery worker in background
echo "⚙️  Starting Celery worker..."
mkdir -p logs
celery -A app.celery_worker worker --loglevel=info --detach \
    --logfile=logs/celery.log \
    --pidfile=logs/celery.pid

echo "✅ Celery worker started"

# Start PIOPIY Agent in background
echo "🤖 Starting PIOPIY Agent..."
python -m app.piopiy_agent > logs/piopiy_agent.log 2>&1 &
echo $! > logs/piopiy_agent.pid
sleep 2

echo "✅ PIOPIY Agent started"

# Start FastAPI server
echo "🌐 Starting FastAPI server..."
uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info \
    --access-log

echo "✅ All services started!"
