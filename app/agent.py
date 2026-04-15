
"""

MuTech Automation AI Cold Calling Agent — Aira

Uses piopiy-ai SDK with Sarvam STT/TTS + OpenRouter LLM

"""



import asyncio

import os

from dotenv import load_dotenv

from loguru import logger



from piopiy.agent import Agent

from piopiy.voice_agent import VoiceAgent

from piopiy.services.sarvam.stt import SarvamSTTService

from piopiy.services.sarvam.tts import SarvamTTSService

from piopiy.services.openrouter.llm import OpenRouterLLMService

from piopiy.transcriptions.language import Language



load_dotenv()



SALES_PROMPT = """You are Aira, a professional sales agent for MuTech Automation, 

an IoT-based industrial automation company serving India and UAE markets.



Your goal is to introduce MuTech's industrial IoT automation solutions to potential 

customers and schedule a demo or follow-up call with our technical team.



Key products:

- Industrial IoT sensors and controllers

- Factory automation systems  

- Remote monitoring dashboards

- Predictive maintenance solutions



Guidelines:

- Be professional, friendly, and concise

- Speak naturally as if on a real phone call

- Ask about their current automation challenges

- Focus on ROI: cost savings, efficiency, reduced downtime

- Try to schedule a demo call with our technical team

- If not interested, politely thank them and end the call

- Keep responses brief — this is a voice call, not a chat

- Speak in Hindi or English based on customer preference

"""



async def create_session(

    agent_id: str,

    call_id: str,

    from_number: str,

    to_number: str,

    metadata: dict = None,

):

    logger.info(f"New call | ID: {call_id} | From: {from_number} | To: {to_number}")

    if metadata:

        logger.info(f"Metadata: {metadata}")



    # Determine customer name from metadata if available

    customer_name = metadata.get("name", "") if metadata else ""

    greeting = f"Hello{', ' + customer_name if customer_name else ''}! This is Aira calling from MuTech Automation. Am I speaking with the right person regarding industrial automation solutions?"



    voice_agent = VoiceAgent(

        instructions=SALES_PROMPT,

        greeting=greeting,

    )



    stt = SarvamSTTService(

        api_key=os.getenv("SARVAM_API_KEY"),

        model="saarika:v2",

        params=SarvamSTTService.InputParams(

            language=Language.HI_IN,

            vad_signals=True,

        ),

    )



    llm = OpenRouterLLMService(

        api_key=os.getenv("OPENROUTER_API_KEY"),

        model=os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),

    )



    tts = SarvamTTSService(

        api_key=os.getenv("SARVAM_API_KEY"),

        model="bulbul:v2",

        voice_id="anushka",

        params=SarvamTTSService.InputParams(

            language=Language.HI,

            pace=1.1,

        ),

    )



    await voice_agent.Action(

        stt=stt,

        llm=llm,

        tts=tts,

        vad=True,

        allow_interruptions=True,

    )

    await voice_agent.start()





async def main():

    agent_id = os.getenv("AGENT_ID")

    agent_token = os.getenv("AGENT_TOKEN")



    if not agent_id or not agent_token:

        raise ValueError("AGENT_ID and AGENT_TOKEN must be set in .env")



    logger.info(f"Starting MuTech AI Agent (Aira) | Agent ID: {agent_id}")



    agent = Agent(

        agent_id=agent_id,

        agent_token=agent_token,

        create_session=create_session,

        debug=True,

    )



    await agent.connect()





if __name__ == "__main__":

    asyncio.run(main())

