# AI-Caller Project Guidelines

## Architecture

The system consists of 4 distinct layers:
- **Telephony**: PIOPIY (primary) with Exotel fallback
- **Voice AI Pipeline**: Pipecat-based for PIOPIY, WebSocket for Exotel
- **Business Logic**: Campaign runner with Celery background tasks
- **REST API**: FastAPI endpoints for leads, campaigns, analytics

Use PIOPIY for new features due to better SDK support.

## Build and Test

**Setup**:
```bash
cp .env.example .env
python test_piopiy_setup.py
```

**Development**:
```bash
python app/piopiy_agent.py  # Start agent
python app/piopiy_outbound_caller.py 9876543210 "John Doe"  # Test call
```

**Production**:
```bash
./start.sh  # Launches Redis → Celery → FastAPI on :8000
```

**Celery worker**:
```bash
celery -A app.celery_worker worker --loglevel=info
```

## Conventions

- **Phone numbers**: Always store in E.164 format (+91XXXXXXXXXX). Database auto-fixes scientific notation from Excel.
- **Environment variables**: Configure AI providers flexibly (Deepgram/Sarvam for STT, OpenAI/Groq for LLM, Cartesia/Sarvam for TTS).
- **Default users**: admin/mutech123 (admin), agent/agent123 (sales), manager/manager123 (manager), view/view123 (viewer).
- **Language**: Default to Hindi for leads; set explicitly in CSV uploads.
- **Campaigns**: Use Celery for bulk operations (>10 leads); direct API for single calls.

## Common Pitfalls

- Verify Redis is running before starting Celery tasks.
- Format phone columns as TEXT in Excel to prevent scientific notation.
- Install PIOPIY with all extras: `pip install "piopiy-ai[openai,deepgram,cartesia,silero,groq,sarvam]"`.
- Check logs in `logs/` directory and DB `system_logs` table for debugging.

See [QUICK_START.md](QUICK_START.md) for setup and [PIOPIY_TESTING_GUIDE.md](PIOPIY_TESTING_GUIDE.md) for testing workflow.</content>
<parameter name="filePath">c:\Users\Admin\Desktop\Projects\MuTech Products\temp\AI-Caller\.github\copilot-instructions.md