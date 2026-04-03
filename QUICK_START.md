# PIOPIY Outbound Calls - Quick Start

## ✅ What's Fixed

Your PIOPIY outbound calling system is now fully functional. Here's what was updated:

### 1. **piopiy_agent.py** - Fixed & Enhanced
- ✅ Proper async/await error handling
- ✅ Supports multiple STT providers (Deepgram, Sarvam)
- ✅ Supports multiple LLM providers (OpenAI, Groq)
- ✅ Supports multiple TTS providers (Cartesia, Sarvam)
- ✅ Graceful provider fallback if configured
- ✅ Detailed logging with call tracking
- ✅ Personalized greetings from outbound metadata

### 2. **piopiy_outbound_caller.py** - NEW
- ✅ RestClient implementation for outbound calls via PIOPIY REST API
- ✅ Phone number normalization (handles 10, 11, 12 digit formats)
- ✅ Metadata passing for personalization
- ✅ CLI testing interface
- ✅ Comprehensive error handling
- ✅ Async support for FastAPI integration

### 3. **piopiy_handler.py** - Fixed
- ✅ Replaces NotImplementedError with working RestClient
- ✅ High-level API for FastAPI routes
- ✅ Support for both sync and async calls
- ✅ Lead tracking and logging

### 4. **Documentation & Testing**
- ✅ **PIOPIY_TESTING_GUIDE.md** - Complete testing walkthrough
- ✅ **.env.example** - Environment variable template
- ✅ **test_piopiy_setup.py** - Diagnostic/test tool

---

## 🚀 Quick Start (5 Minutes)

### Step 1: Configure Environment
```bash
# Copy and edit .env.example
cp .env.example .env

# Add your credentials from PIOPIY Dashboard:
# AGENT_ID=xxx
# AGENT_TOKEN=xxx
# PIOPIY_TOKEN=xxx
# PIOPIY_NUMBER=+919876543210
```

### Step 2: Get AI Service Keys
Choose one provider for each (or use defaults):
- **STT**: Deepgram API key (free at console.deepgram.com)
- **LLM**: OpenAI API key (free tier at platform.openai.com)
- **TTS**: Cartesia API key (free trial at play.cartesia.ai)

Add these to `.env` file.

### Step 3: Test Setup
```bash
python test_piopiy_setup.py
```
Should show: `✅ All checks passed!`

### Step 4: Start Agent (Terminal 1)
```bash
cd app
python piopiy_agent.py
```
Should show: `📞 Waiting for incoming calls...`

### Step 5: Test Outbound Call (Terminal 2)
```bash
cd app
python piopiy_outbound_caller.py 9876543210 "Your Name"
```
You'll receive a call with your personalized greeting!

---

## 📋 Files Modified/Created

| File | Status | Purpose |
|------|--------|---------|
| `app/piopiy_agent.py` | ✅ Fixed | Background agent handling calls |
| `app/piopiy_outbound_caller.py` | ✅ NEW | REST API client for outbound calls |
| `app/piopiy_handler.py` | ✅ Fixed | High-level wrapper for FastAPI |
| `PIOPIY_TESTING_GUIDE.md` | ✅ NEW | Complete testing guide |
| `.env.example` | ✅ NEW | Environment variable template |
| `test_piopiy_setup.py` | ✅ NEW | Diagnostic test tool |

---

## 🔧 Integration with FastAPI

Add this to `app/api_routes.py`:

```python
from app.piopiy_handler import make_outbound_call_async

@router.post("/api/piopiy/outbound")
async def trigger_piopiy_call(request: Request):
    body = await request.json()
    result = await make_outbound_call_async(
        to_number=body.get("to_number"),
        customer_name=body.get("customer_name"),
        lead_id=body.get("lead_id"),
    )
    return result
```

Test with curl:
```bash
curl -X POST http://localhost:8000/api/piopiy/outbound \
  -H "Content-Type: application/json" \
  -d '{"to_number": "9876543210", "customer_name": "Rajesh"}'
```

---

## 📊 Architecture

```
User FastAPI Route
    ↓
piopiy_handler.make_outbound_call_async()
    ↓
piopiy_outbound_caller.trigger_outbound_call()
    ↓
RestClient.ai.call() [PIOPIY REST API]
    ↓
PIOPIY Infrastructure
    ↓
piopiy_agent.py (Background Agent) ← Receives call event
    ↓
STT Pipeline (Deepgram/Sarvam)
LLM Pipeline (OpenAI/Groq)
TTS Pipeline (Cartesia/Sarvam)
    ↓
Voice Conversation with Customer
```

---

## 📖 Full Documentation

See **PIOPIY_TESTING_GUIDE.md** for:
- ✅ Complete prerequisites checklist
- ✅ Step-by-step testing guide
- ✅ Common issues & solutions
- ✅ Campaign bulk calling
- ✅ Debugging tips

---

## 🐛 Troubleshooting

**Agent won't start:**
```
❌ Missing AGENT_ID or AGENT_TOKEN
→ Check .env file has correct credentials from PIOPIY Dashboard
```

**Call won't connect:**
```
❌ Call failed
→ Verify PIOPIY_NUMBER is assigned to agent in dashboard
→ Check phone number format is valid (+91 for India)
```

**No greeting heard:**
```
❌ No audio
→ Verify DEEPGRAM_API_KEY or SARVAM_API_KEY is set
→ Check internet connection
```

**LLM not responding:**
```
❌ Agent silent
→ Check OPENAI_API_KEY or GROQ_API_KEY is valid
→ Verify API quota not exceeded
```

Run diagnostic:
```bash
python test_piopiy_setup.py --quick
```

---

## ✨ Key Features

1. **Flexible Provider Support**: Switch between STT/LLM/TTS providers without code changes
2. **Phone Number Normalization**: Handles 10, 11, 12 digit Indian phone numbers
3. **Personalization**: Pass customer names for personalized greetings
4. **Async Ready**: Full async/await support for high concurrency
5. **Production Logging**: Detailed logs in `logs/piopiy_agent.log`
6. **Error Recovery**: Graceful fallbacks when providers aren't available

---

## 📞 Support

- **PIOPIY Docs**: https://doc.piopiy.com/
- **Phone Agent Example**: https://doc.piopiy.com/piopiy/docs/sdk/examples/phone-agent
- **Check Logs**: `tail -f logs/piopiy_agent.log`

---

**Ready to test?** Follow the 5-minute Quick Start above! 🚀
