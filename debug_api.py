#!/usr/bin/env python3
import os
import requests
from dotenv import load_dotenv

load_dotenv()

app_id = os.getenv("PIOPIY_AGENT_ID")
app_secret = os.getenv("PIOPIY_AGENT_TOKEN")
caller_id = os.getenv("PIOPIY_CALLER_ID", "01203134158")
to_number = "+919301921913"

print(f"Calling {to_number} from {caller_id}")
print(f"Using app_id: {app_id}")

url = f"https://{app_id}:{app_secret}@api.telecmi.com/v1/call"
payload = {
    "to": to_number,
    "from": caller_id,
    "timeout": 30,
    "duration": 300
}

print(f"URL: {url}")
print(f"Payload: {payload}")

try:
    response = requests.post(url, json=payload, timeout=10)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")

    with open('api_response.txt', 'w') as f:
        f.write(f"Status: {response.status_code}\n")
        f.write(f"Response: {response.text}\n")

except Exception as e:
    print(f"Error: {e}")
    with open('api_response.txt', 'w') as f:
        f.write(f"Error: {e}\n")