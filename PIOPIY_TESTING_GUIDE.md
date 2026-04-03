# PIOPIY Outbound Calls - Testing Guide

## Overview
This guide will help you test the PIOPIY outbound calling system step by step. You have two components:
1. **piopiy_agent.py** - Background agent that handles incoming and outgoing calls
2. **piopiy_outbound_caller.py** - REST API client that triggers outbound calls

---

## Prerequisites

### 1. PIOPIY Account Setup
1. Go to [PIOPIY Dashboard](https://dashboard.piopiy.com/)
2. Create an account and verify your email
3. Navigate to **Settings > API Credentials**
4. Copy your:
   - **AGENT_ID** (unique agent identifier)
   - **AGENT_TOKEN** (authentication token)
   - **PIOPIY_TOKEN** (REST API token for outbound calls)
5. Purchase a phone number and assign it to your agent
   - Copy this number as **PIOPIY_NUMBER**

### 2. AI Service Credentials
Choose ONE provider for each service (STT, LLM, TTS). Recommended stack:

| Service | Provider | Setup |
|---------|----------|-------|
| STT (Speech-to-Text) | **Deepgram** (preferred) | Get free API key from [console.deepgram.com](https://console.deepgram.com/) |
| LLM (Brain) | **OpenAI** (preferred) | Get API key from [platform.openai.com](https://platform.openai.com/) |
| TTS (Text-to-Speech) | **Cartesia** (preferred) | Get API key from [play.cartesia.ai](https://play.cartesia.ai/) |

**Alternative providers supported:**
- STT: Sarvam (Hindi support)
- LLM: Groq (fast, free tier available)
- TTS: Sarvam (Hindi support)

### 3. Environment Variables
Create or update `.env` file in the project root:

```bash
# PIOPIY Settings
AGENT_ID=your_agent_id_here
AGENT_TOKEN=your_agent_token_here
PIOPIY_TOKEN=your_rest_api_token_here
PIOPIY_NUMBER=+919876543210  # Phone number to dial FROM

# AI Services (Recommended Stack)
DEEPGRAM_API_KEY=your_deepgram_key
OPENAI_API_KEY=your_openai_key
CARTESIA_API_KEY=your_cartesia_key

# Optional: Alternative providers
GROQ_API_KEY=your_groq_key
SARVAM_API_KEY=your_sarvam_key

# Application Settings
PUBLIC_URL=https://your-public-domain.com
ENVIRONMENT=development
```

### 4. Install Dependencies
```bash
# Install the piopiy-ai SDK with all providers
pip install "piopiy-ai[openai,deepgram,cartesia,silero,groq,sarvam]"
```

---

## Testing Steps

### Phase 1: Background Agent Setup ✓

**Step 1: Start the PIOPIY Agent**
```bash
cd app
python piopiy_agent.py
```

Expected output:
```
🚀 Starting PIOPIY Agent (Priya)...
   Agent ID: xxx-xxx-xxx
📡 Connecting to PIOPIY signaling server...
✅ Connected to PIOPIY infrastructure
📞 Waiting for incoming calls...
```

**What this does:**
- Connects to PIOPIY's real-time transport
- Waits for call events (inbound or outbound-triggered)
- Streams audio to STT → LLM → TTS pipeline
- Plays greeting when call connects

**Troubleshooting:**
```
❌ Error: Missing Agent_ID or AGENT_TOKEN
→ Check your .env file has valid credentials

❌ Error: connection refused
→ Verify internet connection and PIOPIY_TOKEN is valid

❌ Error: No STT provider configured
→ Add DEEPGRAM_API_KEY or SARVAM_API_KEY to .env
```

---

### Phase 2: Outbound Call Testing

**Step 2: Test Outbound Call (Command Line)**

In a new terminal:
```bash
cd app
python piopiy_outbound_caller.py 9876543210 "Rajesh Kumar"
```

Expected output:
```
📞 Triggering outbound call
   To: +919876543210 | From: +919876543210 | Agent: xxx
   Customer: Rajesh Kumar
✅ Call successfully initiated!
   Response: {...}
```

The agent terminal should show:
```
📞 New call session | Call ID: xyz | From: +919876543210 | To: +919876543210
   Using personalized greeting for: Rajesh Kumar
✅ Starting voice pipeline for call xyz
```

**What happens:**
1. ✅ PIOPIY REST API receives outbound trigger
2. ✅ PIOPIY dials `+919876543210`
3. ✅ Agent picks up call in background agent
4. ✅ Plays Hindi greeting with customer name
5. ✅ Starts STT→LLM→TTS pipeline
6. ✅ Customer can now speak naturally

---

### Phase 3: Integration Testing with FastAPI

**Step 3: Add API Route for Outbound Calls**

Edit [app/api_routes.py](app/api_routes.py) and add:

```python
from app.piopiy_handler import make_outbound_call_async

@router.post("/api/piopiy/outbound")
async def trigger_piopiy_call(request: Request):
    """Trigger a PIOPIY outbound call."""
    body = await request.json()
    
    to_number = body.get("to_number")
    customer_name = body.get("customer_name")
    lead_id = body.get("lead_id")
    
    if not to_number:
        raise HTTPException(status_code=400, detail="to_number required")
    
    result = await make_outbound_call_async(
        to_number=to_number,
        customer_name=customer_name,
        lead_id=lead_id,
    )
    
    if result["status"] == "success":
        db.add_log(f"📞 PIOPIY outbound: {to_number} ({customer_name})")
        return result
    else:
        raise HTTPException(status_code=500, detail=result["message"])
```

**Test with curl:**
```bash
curl -X POST http://localhost:8000/api/piopiy/outbound \
  -H "Content-Type: application/json" \
  -d '{
    "to_number": "9876543210",
    "customer_name": "Rajesh Kumar",
    "lead_id": "123"
  }'
```

Expected response:
```json
{
  "status": "success",
  "to_number": "+919876543210",
  "caller_id": "+919876543210",
  "customer_name": "Rajesh Kumar",
  "lead_id": "123",
  "response": {...}
}
```

---

### Phase 4: Campaign Testing

**Step 4: Test Bulk Campaign Calling**

Update [app/campaign_runner.py](app/campaign_runner.py) to use PIOPIY:

```python
async def make_single_call(phone: str, lead_id: str = None, customer_name: str = None):
    """Fire one outbound call via PIOPIY."""
    from app.piopiy_handler import make_outbound_call_async
    
    result = await make_outbound_call_async(
        to_number=phone,
        customer_name=customer_name,
        lead_id=lead_id,
    )
    
    if result["status"] == "success":
        logger.info(f"✅ PIOPIY call initiated to {phone}")
        return result
    else:
        logger.error(f"❌ PIOPIY call failed for {phone}: {result['message']}")
        return None
```

Test with a CSV file:
```python
import csv
import asyncio

async def test_campaign():
    leads = []
    with open("Test Contact List.csv") as f:
        reader = csv.DictReader(f)
        for row in reader:
            leads.append(row)
    
    for lead in leads[:3]:  # Test first 3
        phone = lead.get("Phone", "").strip()
        name = lead.get("Name", "")
        
        if phone:
            result = await make_outbound_call_async(phone, customer_name=name)
            print(f"Called {name}: {result['status']}")
            
        await asyncio.sleep(5)  # 5 second delay between calls

# Run test
asyncio.run(test_campaign())
```

---

## Debugging

### Enable Detailed Logging
```python
# In piopiy_agent.py or piopiy_outbound_caller.py
from loguru import logger

# Already configured, logs go to:
# - logs/piopiy_agent.log        (agent activity)
# - logs/piopiy_outbound.log     (outbound call attempts)
```

### Check Logs
```bash
# Real-time agent logs
tail -f logs/piopiy_agent.log

# Real-time outbound attempt logs
tail -f logs/piopiy_outbound.log

# Or search for errors
grep "❌" logs/piopiy_agent.log
```

### Common Issues

| Issue | Solution |
|-------|----------|
| **Call doesn't connect** | ✓ Verify phone number format (+91 for India) |
| | ✓ Check PIOPIY_NUMBER is assigned to agent in dashboard |
| | ✓ Ensure lead number is valid (10-12 digits) |
| **No greeting heard** | ✓ Verify DEEPGRAM_API_KEY or SARVAM_API_KEY is set |
| | ✓ Check internet connection (STT needs to stream) |
| | ✓ Verify TTS provider (CARTESIA or SARVAM) working |
| **Agent not responding** | ✓ Check OPENAI_API_KEY or GROQ_API_KEY is valid |
| | ✓ Verify LLM API quotas not exceeded |
| | ✓ Check agent.log for exact error |
| **Metadata not passed** | ✓ Ensure `customer_name` key in variables dict |
| | ✓ Check `create_session` receives `metadata` kwarg |

---

## Performance Tips

1. **Provider Selection:**
   - Deepgram (STT): Lowest latency, great for real-time
   - OpenAI (LLM): Best quality responses
   - Cartesia (TTS): Most natural audio quality

2. **Call Concurrency:**
   - PIOPIY supports multiple concurrent calls
   - Use async/await for non-blocking campaign execution:
   ```python
   tasks = [make_outbound_call_async(phone) for phone in phone_list]
   results = await asyncio.gather(*tasks)
   ```

3. **Rate Limiting:**
   - Add delays between calls to avoid throttling:
   ```python
   for phone in phone_list:
       await make_outbound_call_async(phone)
       await asyncio.sleep(2)  # 2 second gap
   ```

---

## Next Steps

1. ✅ Verify agent starts and logs show "Waiting for calls"
2. ✅ Test single outbound call with CLI
3. ✅ Verify call arrives and greeting plays
4. ✅ Add API route and test with curl
5. ✅ Load 3-5 test contacts and run mini campaign
6. ✅ Monitor logs and verify quality
7. ➜ Deploy to production with proper error handling

---

## Support Resources

- [PIOPIY Documentation](https://doc.piopiy.com/)
- [PIOPIY Phone Agent Example](https://doc.piopiy.com/piopiy/docs/sdk/examples/phone-agent)
- [PIOPIY Dashboard](https://dashboard.piopiy.com/)
- Check logs in `logs/` directory for detailed debugging
