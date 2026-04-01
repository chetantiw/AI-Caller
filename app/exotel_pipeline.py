
"""

app/exotel_pipeline.py

Proper Pipecat pipeline for Exotel WebSocket

Uses: ExotelFrameSerializer + Silero VAD + Sarvam STT + Groq LLM + Sarvam TTS

"""

import os

from loguru import logger

from dotenv import load_dotenv

from fastapi import WebSocket



from pipecat.audio.vad.silero import SileroVADAnalyzer

from pipecat.pipeline.pipeline import Pipeline

from pipecat.pipeline.runner import PipelineRunner

from pipecat.pipeline.task import PipelineParams, PipelineTask

from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext

from pipecat.serializers.exotel import ExotelFrameSerializer

from pipecat.services.groq.llm import GroqLLMService

from pipecat.services.sarvam.stt import SarvamSTTService

from pipecat.services.sarvam.tts import SarvamTTSService

from pipecat.transcriptions.language import Language

from pipecat.transports.websocket.fastapi import (

    FastAPIWebsocketParams,

    FastAPIWebsocketTransport,

)



load_dotenv()



SYSTEM_PROMPT = """आप प्रिया हैं, म्यूटेक ऑटोमेशन की पेशेवर सेल्स एजेंट हैं।



म्यूटेक ऑटोमेशन के बारे में: हम इंडस्ट्रियल IoT और फैक्ट्री ऑटोमेशन कंपनी हैं। हमारे उत्पाद: स्मार्ट एनर्जी मीटर, IoT सेंसर, रिमोट मॉनिटरिंग, प्रेडिक्टिव मेंटेनेंस। हम भारत और UAE में काम करते हैं।



बातचीत का तरीका:

- हमेशा हिंदी में बोलें

- हर जवाब 2 वाक्य में दें — पहला जानकारी, दूसरा ग्राहक से सवाल

- बिना रुके स्वाभाविक रूप से बोलें

- ग्राहक जो पूछे उसका पूरा जवाब दें

- अंत में डेमो शेड्यूल करने की कोशिश करें

- अगर रुचि नहीं है तो विनम्रता से कॉल समाप्त करें"""



GREETING = "नमस्ते! मैं प्रिया बोल रही हूँ म्यूटेक ऑटोमेशन से। हम इंडस्ट्रियल आई-ओ-टी और फैक्ट्री ऑटोमेशन में विशेषज्ञ हैं।"





async def run_exotel_pipeline(websocket: WebSocket, lead: dict = None):

    """Run proper Pipecat pipeline for Exotel WebSocket call."""



    lead_name = lead.get("name", "") if lead else ""



    # Wait for the 'start' event to get stream_sid

    stream_sid = None

    call_sid = None



    import json

    while True:

        msg = await websocket.receive_text()

        data = json.loads(msg)

        event = data.get("event")

        if event == "connected":

            logger.info("Exotel connected, waiting for start...")

            continue

        elif event == "start":

            stream_sid = (data.get("stream_sid") or

                         data.get("start", {}).get("stream_sid") or

                         data.get("start", {}).get("streamSid", "stream"))

            call_sid = data.get("start", {}).get("call_sid", "")

            logger.info(f"Stream started: {stream_sid}")

            break



    # Now set up proper Pipecat pipeline

    serializer = ExotelFrameSerializer(stream_sid=stream_sid, call_sid=call_sid)



    transport = FastAPIWebsocketTransport(

        websocket=websocket,

        params=FastAPIWebsocketParams(

            audio_in_enabled=True,

            audio_out_enabled=True,

            add_wav_header=False,

            vad_enabled=True,

            vad_analyzer=SileroVADAnalyzer(),

            vad_audio_passthrough=True,

            serializer=serializer,

        ),

    )



    # Groq LLM

    llm = GroqLLMService(

        api_key=os.getenv("GROQ_API_KEY"),

        model="llama-3.3-70b-versatile",

    )



    # Sarvam STT - Hindi

    stt = SarvamSTTService(

        api_key=os.getenv("SARVAM_API_KEY"),

        model="saarika:v2.5",

        params=SarvamSTTService.InputParams(

            language=Language.HI_IN,

            vad_signals=True,

        ),

    )



    # Sarvam TTS - Hindi Anushka voice

    tts = SarvamTTSService(

        api_key=os.getenv("SARVAM_API_KEY"),

        model="bulbul:v2",

        voice_id="anushka",

        params=SarvamTTSService.InputParams(

            language=Language.HI,

            pace=1.0,

        ),

    )



    # LLM context with greeting

    greeting = f"नमस्ते{', ' + lead_name if lead_name else ''}! मैं प्रिया बोल रही हूँ म्यूटेक ऑटोमेशन से। हम इंडस्ट्रियल IoT सेंसर, फैक्ट्री ऑटोमेशन, और स्मार्ट एनर्जी मैनेजमेंट के समाधान देते हैं। आपकी फैक्ट्री में ऑटोमेशन या एनर्जी की कोई चुनौती है क्या?"



    messages = [

        {"role": "system", "content": SYSTEM_PROMPT},

        {"role": "assistant", "content": greeting},

    ]

    greeting_triggered = False  # Track if greeting has been sent



    context = OpenAILLMContext(messages)

    context_aggregator = llm.create_context_aggregator(context)



    from pipecat.frames.frames import UserStartedSpeakingFrame

    from pipecat.processors.frame_processor import FrameProcessor, FrameDirection



    class GreetingTrigger(FrameProcessor):

        """Send greeting on first customer speech detected by VAD"""

        def __init__(self):

            super().__init__()

            self._greeted = False



        async def process_frame(self, frame, direction):

            await super().process_frame(frame, direction)

            if isinstance(frame, UserStartedSpeakingFrame) and not self._greeted:

                self._greeted = True

                logger.info("Customer started speaking - triggering greeting")

                from pipecat.frames.frames import LLMMessagesFrame

                await task.queue_frames([context_aggregator.user().get_context_frame()])

            await self.push_frame(frame, direction)



    greeting_trigger = GreetingTrigger()



    # Build pipeline

    pipeline = Pipeline([

        transport.input(),           # Audio from Exotel

        greeting_trigger,            # Trigger greeting on first speech

        stt,                         # Sarvam STT → transcription

        context_aggregator.user(),   # Add user text to context

        llm,                         # Groq LLM → response

        tts,                         # Sarvam TTS → audio

        transport.output(),          # Audio back to Exotel

        context_aggregator.assistant(),  # Save assistant response

    ])



    task = PipelineTask(

        pipeline,

        params=PipelineParams(

            allow_interruptions=True,

            enable_metrics=True,

            audio_in_sample_rate=8000,

            audio_out_sample_rate=8000,

        ),

    )



    # Do NOT send greeting on connect for outbound calls

    # Greeting will be triggered after customer speaks

    @transport.event_handler("on_client_connected")

    async def on_connected(transport, client):

        logger.info("Client connected - waiting for customer to speak first")



    @transport.event_handler("on_client_disconnected")

    async def on_disconnected(transport, client):

        logger.info("Client disconnected")

        await task.cancel()



    runner = PipelineRunner()

    await runner.run(task)

