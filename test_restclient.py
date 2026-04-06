#!/usr/bin/env python3
import os
from dotenv import load_dotenv

load_dotenv()

app_id = os.getenv("PIOPIY_AGENT_ID")
app_secret = os.getenv("PIOPIY_AGENT_TOKEN")

print(f"App ID: {app_id}")
print(f"App Secret: {app_secret[:10]}...")

try:
    from piopiy import RestClient
    print("RestClient imported successfully")

    rc = RestClient(app_id, app_secret)
    print("RestClient created")

    print("Available methods:")
    methods = [m for m in dir(rc) if not m.startswith('_')]
    for method in methods:
        print(f"  - {method}")

    if hasattr(rc, 'call'):
        print("RestClient has 'call' method")
        # Try to call it
        try:
            result = rc.call(["+919301921913"], "01203134158", {"timeout": 30})
            print(f"Call result: {result}")
        except Exception as e:
            print(f"Call failed: {e}")
    else:
        print("RestClient does NOT have 'call' method")

except Exception as e:
    print(f"RestClient error: {e}")