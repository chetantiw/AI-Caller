
import asyncio

import os

from dotenv import load_dotenv

from piopiy.agent import Agent

from piopiy.voice_agent import VoiceAgent

from piopiy.services.sarvam.stt import SarvamSTTService

from piopiy.services.sarvam.tts import SarvamTTSService

from piopiy.services.groq.llm import GroqLLMService



load_dotenv()



SYSTEM_PROMPT = """आप प्रिया हैं, म्यूटेक ऑटोमेशन की पेशेवर सेल्स एजेंट हैं।

म्यूटेक ऑटोमेशन: इंडस्ट्रियल IoT और फैक्ट्री ऑटोमेशन कंपनी।

उत्पाद: स्मार्ट एनर्जी मीटर, IoT सेंसर, रिमोट मॉनिटरिंग, प्रेडिक्टिव मेंटेनेंस।

नियम: हमेशा हिंदी में बोलें। हर जवाब 2 वाक्य। स्वाभाविक रूप से बोलें। अंत में डेमो शेड्यूल करें।"""



async def create_session(agent_id=None, call_id=None, from_number=None, to_number=None, **kwargs):

    print(f"📞 CALL: {call_id} from {from_number}")

    

    metadata = kwargs.get("metadata", {}) or {}

    customer_name = metadata.get("customer_name", "")



    if customer_name:

        greeting = f"नमस्ते {customer_name} जी! मैं प्रिया बोल रही हूँ म्यूटेक ऑटोमेशन से। हम इंडस्ट्रियल IoT सेंसर, फैक्ट्री ऑटोमेशन, और स्मार्ट एनर्जी मैनेजमेंट के समाधान देते हैं। आपकी फैक्ट्री में कोई चुनौती है क्या?"

    else:

        greeting = "नमस्ते! मैं प्रिया बोल रही हूँ म्यूटेक ऑटोमेशन से। हम इंडस्ट्रियल IoT सेंसर, फैक्ट्री ऑटोमेशन, और स्मार्ट एनर्जी मैनेजमेंट के समाधान देते हैं। आपकी फैक्ट्री में कोई चुनौती है क्या?"



    voice_agent = VoiceAgent(instructions=SYSTEM_PROMPT, greeting=greeting)



    stt = SarvamSTTService(api_key=os.getenv("SARVAM_API_KEY"), model="saarika:v2.5")

    llm = GroqLLMService(api_key=os.getenv("GROQ_API_KEY"), model="llama-3.3-70b-versatile")

    tts = SarvamTTSService(api_key=os.getenv("SARVAM_API_KEY"), model="bulbul:v2", voice_id="anushka")



    await voice_agent.Action(stt=stt, llm=llm, tts=tts, vad=True, allow_interruptions=True)

    await voice_agent.start()



async def main():

    print("🚀 Starting Priya PIOPIY Agent...")

    agent = Agent(

        agent_id=os.getenv("PIOPIY_AGENT_ID"),

        agent_token=os.getenv("PIOPIY_AGENT_TOKEN"),

        create_session=create_session,

        debug=True,

    )

    print("📡 Connecting to PIOPIY signaling server...")

    await agent.connect()



if __name__ == "__main__":

    asyncio.run(main())

