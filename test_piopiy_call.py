#!/usr/bin/env python3
"""
test_piopiy_call.py
Manual test script for PIOPIY outbound calls
"""

import os
import sys
sys.path.append('.')

from dotenv import load_dotenv
from app.piopiy_handler import make_outbound_call

load_dotenv()

def test_call(phone=None):
    print("DEBUG: test_call function called")
    if not phone:
        if len(sys.argv) > 1:
            phone = sys.argv[1]
        else:
            phone = input("Enter phone number to call (e.g., +919876543210): ").strip()
    
    if not phone:
        print("❌ No phone number provided")
        return

    print(f"📞 Calling {phone}...")

    try:
        call_id = make_outbound_call(phone)
        result = f"✅ Call initiated! Call ID: {call_id}"
        print(result)
        # Write to file for verification
        with open('test_result_new.txt', 'w') as f:
            f.write(result)
    except Exception as e:
        error_msg = f"❌ Call failed: {e}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        # Write to file for verification
        with open('test_result_new.txt', 'w') as f:
            f.write(error_msg)

if __name__ == "__main__":
    print("🧪 PIOPIY Outbound Call Test")
    test_call()