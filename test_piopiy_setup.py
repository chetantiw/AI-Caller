#!/usr/bin/env python3
"""
test_piopiy_setup.py
Quick diagnostic script to verify PIOPIY configuration and connectivity.

Usage:
    python test_piopiy_setup.py              # Full diagnostic
    python test_piopiy_setup.py --quick      # Quick check only
    python test_piopiy_setup.py --call <phone> [name]  # Test outbound call
"""

import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

# Load environment
load_dotenv()

class Colors:
    OK = '\033[92m'     # Green
    FAIL = '\033[91m'   # Red
    WARN = '\033[93m'   # Yellow
    INFO = '\033[94m'   # Blue
    END = '\033[0m'

def check(condition, message, details=""):
    """Print check result."""
    if condition:
        print(f"{Colors.OK}✅ {message}{Colors.END}")
        if details:
            print(f"   {details}")
    else:
        print(f"{Colors.FAIL}❌ {message}{Colors.END}")
        if details:
            print(f"   {details}")
    return condition

def test_piopiy_credentials():
    """Test PIOPIY credentials."""
    print(f"\n{Colors.INFO}═══ PIOPIY CREDENTIALS ═══{Colors.END}")
    
    all_ok = True
    
    agent_id = os.getenv("AGENT_ID")
    check(agent_id, "AGENT_ID configured", agent_id[:20] + "..." if agent_id else "")
    all_ok = all_ok and bool(agent_id)
    
    agent_token = os.getenv("AGENT_TOKEN")
    check(agent_token, "AGENT_TOKEN configured", "***" if agent_token else "")
    all_ok = all_ok and bool(agent_token)
    
    piopiy_token = os.getenv("PIOPIY_TOKEN")
    check(piopiy_token, "PIOPIY_TOKEN configured (for REST API)", "***" if piopiy_token else "")
    all_ok = all_ok and bool(piopiy_token)
    
    piopiy_number = os.getenv("PIOPIY_NUMBER")
    check(piopiy_number, "PIOPIY_NUMBER configured", piopiy_number if piopiy_number else "")
    all_ok = all_ok and bool(piopiy_number)
    
    return all_ok

def test_ai_providers():
    """Test AI service credentials."""
    print(f"\n{Colors.INFO}═══ AI SERVICE CREDENTIALS ═══{Colors.END}")
    
    all_ok = True
    
    # STT
    print("\n  Speech-to-Text (STT):")
    deepgram = os.getenv("DEEPGRAM_API_KEY")
    sarvam = os.getenv("SARVAM_API_KEY")
    
    if deepgram:
        check(True, "Deepgram configured (preferred)")
    elif sarvam:
        check(True, "Sarvam configured (good Hindi support)")
    else:
        check(False, "No STT provider configured", "Set DEEPGRAM_API_KEY or SARVAM_API_KEY")
        all_ok = False
    
    # LLM
    print("\n  Language Model (LLM):")
    openai = os.getenv("OPENAI_API_KEY")
    groq = os.getenv("GROQ_API_KEY")
    
    if openai:
        check(True, "OpenAI configured (preferred)")
    elif groq:
        check(True, "Groq configured (fast)")
    else:
        check(False, "No LLM provider configured", "Set OPENAI_API_KEY or GROQ_API_KEY")
        all_ok = False
    
    # TTS
    print("\n  Text-to-Speech (TTS):")
    cartesia = os.getenv("CARTESIA_API_KEY")
    sarvam_tts = os.getenv("SARVAM_API_KEY")
    
    if cartesia:
        check(True, "Cartesia configured (preferred)")
    elif sarvam_tts:
        check(True, "Sarvam configured (good Hindi support)")
    else:
        check(False, "No TTS provider configured", "Set CARTESIA_API_KEY or SARVAM_API_KEY")
        all_ok = False
    
    return all_ok

def test_piopiy_sdk():
    """Test PIOPIY SDK installation."""
    print(f"\n{Colors.INFO}═══ PIOPIY SDK ═══{Colors.END}")
    
    try:
        import piopiy
        check(True, "piopiy-ai package installed")
        
        # Test specific imports
        try:
            from piopiy.agent import Agent
            check(True, "Can import Agent")
        except ImportError as e:
            check(False, "Cannot import Agent", str(e))
            return False
        
        try:
            from piopiy.voice_agent import VoiceAgent
            check(True, "Can import VoiceAgent")
        except ImportError as e:
            check(False, "Cannot import VoiceAgent", str(e))
            return False
        
        try:
            from piopiy_voice import RestClient
            check(True, "Can import RestClient")
        except ImportError as e:
            check(False, "Cannot import RestClient", str(e))
            return False
        
        return True
    except ImportError:
        check(False, "piopiy-ai not installed", "Run: pip install 'piopiy-ai[openai,deepgram,cartesia]'")
        return False

def test_ai_sdk():
    """Test AI provider SDKs."""
    print(f"\n{Colors.INFO}═══ AI PROVIDER SDKS ═══{Colors.END}")
    
    all_ok = True
    
    # Deepgram
    try:
        from deepgram import Deepgram
        check(True, "Deepgram SDK installed")
    except ImportError:
        if os.getenv("DEEPGRAM_API_KEY"):
            check(False, "Deepgram SDK not installed", "Run: pip install deepgram-sdk")
            all_ok = False
    
    # OpenAI
    try:
        import openai
        check(True, "OpenAI SDK installed")
    except ImportError:
        if os.getenv("OPENAI_API_KEY"):
            check(False, "OpenAI SDK not installed", "Run: pip install openai")
            all_ok = False
    
    # Groq
    try:
        import groq
        check(True, "Groq SDK installed")
    except ImportError:
        if os.getenv("GROQ_API_KEY"):
            check(False, "Groq SDK not installed", "Run: pip install groq")
            all_ok = False
    
    return all_ok

def test_api_connectivity():
    """Test API connectivity."""
    print(f"\n{Colors.INFO}═══ API CONNECTIVITY ═══{Colors.END}")
    
    try:
        import requests
    except ImportError:
        check(False, "requests module not installed", "Run: pip install requests")
        return False
    
    all_ok = True
    
    # Test PIOPIY connectivity
    piopiy_token = os.getenv("PIOPIY_TOKEN")
    if piopiy_token:
        try:
            from piopiy_voice import RestClient
            client = RestClient(token=piopiy_token)
            check(True, "PIOPIY RestClient initialized")
            # Note: We won't actually call any endpoints to avoid charges
        except Exception as e:
            check(False, "PIOPIY RestClient failed", str(e))
            all_ok = False
    else:
        print("  ⊘ PIOPIY_TOKEN not set, skipping connectivity test")
    
    return all_ok

def test_required_files():
    """Check required files exist."""
    print(f"\n{Colors.INFO}═══ PROJECT FILES ═══{Colors.END}")
    
    files = [
        "app/piopiy_agent.py",
        "app/piopiy_outbound_caller.py",
        "app/piopiy_handler.py",
        "PIOPIY_TESTING_GUIDE.md",
    ]
    
    all_ok = True
    for fname in files:
        path = Path(fname)
        exists = path.exists()
        check(exists, f"Found {fname}", "<-- REQUIRED" if exists else "MISSING - restore from backup")
        all_ok = all_ok and exists
    
    return all_ok

def test_outbound_call(phone: str, name: str = None):
    """Test actual outbound call."""
    print(f"\n{Colors.INFO}═══ TESTING OUTBOUND CALL ═══{Colors.END}")
    print(f"Phone: {phone}")
    print(f"Name: {name or 'Not specified'}")
    print()
    
    try:
        from app.piopiy_outbound_caller import trigger_outbound_call
        
        result = trigger_outbound_call(phone, customer_name=name)
        
        print(f"\nResult:")
        print(json.dumps(result, indent=2, default=str))
        
        if result.get("status") == "success":
            print(f"\n{Colors.OK}✅ Call successfully initiated!{Colors.END}")
            print("   Listen for the call and greeting on your phone")
            return True
        else:
            print(f"\n{Colors.FAIL}❌ Call failed{Colors.END}")
            return False
            
    except Exception as e:
        print(f"{Colors.FAIL}❌ Error: {e}{Colors.END}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run tests."""
    if len(sys.argv) > 1:
        if sys.argv[1] == "--quick":
            print(f"{Colors.INFO}Running quick check...{Colors.END}")
            piopiy_ok = test_piopiy_credentials()
            ai_ok = test_ai_providers()
            
            if piopiy_ok and ai_ok:
                print(f"\n{Colors.OK}✅ Setup looks good!{Colors.END}")
                return 0
            else:
                print(f"\n{Colors.FAIL}❌ Fix errors above before proceeding{Colors.END}")
                return 1
        
        elif sys.argv[1] == "--call":
            phone = sys.argv[2] if len(sys.argv) > 2 else None
            name = sys.argv[3] if len(sys.argv) > 3 else None
            
            if not phone:
                print("Usage: python test_piopiy_setup.py --call <phone> [name]")
                return 1
            
            return 0 if test_outbound_call(phone, name) else 1
    
    # Full diagnostic
    print(f"{Colors.INFO}")
    print("╔════════════════════════════════════════════╗")
    print("║    PIOPIY SETUP DIAGNOSTIC                ║")
    print("╚════════════════════════════════════════════╝")
    print(f"{Colors.END}")
    
    result = []
    result.append(("PIOPIY Credentials", test_piopiy_credentials()))
    result.append(("AI Providers", test_ai_providers()))
    result.append(("Project Files", test_required_files()))
    result.append(("PIOPIY SDK", test_piopiy_sdk()))
    result.append(("AI SDKs", test_ai_sdk()))
    result.append(("API Connectivity", test_api_connectivity()))
    
    print(f"\n{Colors.INFO}═══ SUMMARY ═══{Colors.END}")
    for name, ok in result:
        status = f"{Colors.OK}✅{Colors.END}" if ok else f"{Colors.FAIL}❌{Colors.END}"
        print(f"{status} {name}")
    
    all_ok = all(ok for _, ok in result)
    
    if all_ok:
        print(f"\n{Colors.OK}✅ All checks passed! Ready to test.{Colors.END}")
        print("\nNext steps:")
        print("1. Start the agent: python app/piopiy_agent.py")
        print("2. In another terminal, test outbound: python test_piopiy_setup.py --call 9876543210 'Your Name'")
        return 0
    else:
        print(f"\n{Colors.FAIL}❌ Fix errors above before testing{Colors.END}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
