#!/usr/bin/env python3
import sys
import os
sys.path.append('.')

from dotenv import load_dotenv
load_dotenv()

print("Starting test")

try:
    from app.piopiy_handler import make_outbound_call
    print("Import successful")
    
    result = make_outbound_call('+919301921913')
    print(f"Call result: {result}")
    
    with open('simple_test.txt', 'w') as f:
        f.write(f"SUCCESS: {result}")
        
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
    
    with open('simple_test.txt', 'w') as f:
        f.write(f"ERROR: {str(e)}")
        f.write(f"\nTraceback:\n{traceback.format_exc()}")