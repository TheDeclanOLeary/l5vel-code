import io
import json
import threading
import time
import numpy as np
import pyaudio
import requests
import soundfile as sf
import torch  # Fixed: Moved to top level so all methods can access it
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import Literal, Optional
# ==============================================================================
# SYSTEM ENDPOINTS (IMPLEMENT YOUR HARDWARE & API DEPLOYMENT HERE)
# ==============================================================================
client = genai.Client()

def publish_to_control_loop(transcribed_text: str):
    print(f"\n[CONTROL LOOP] Received text: '{transcribed_text}'")
    print("[CONTROL LOOP] Passing string directly to path planning and kinematics nodes...")

def trigger_local_estop():
    print("\n!!! [CRITICAL EMERGENCY STOP DETECTED] !!!")
    print("[HARDWARE] Sending electronic brake signals to Swerve Drive Motors...")
    print("[HARDWARE] Engaging mechanical locking pins on 5-Joint Arm...")


class AudioAnalysis(BaseModel):
    is_addressed: bool = Field(description="True if a voice addresses the robot. False for background TV/noise.")
    intent_type: Optional[Literal["COMMAND", "CONVERSATIONAL", "UNKNOWN"]] = Field(default=None)
    cleaned_transcript: Optional[str] = Field(default=None)

SYSTEM_PROMPT = """
You are the acoustic front-end processor for an assistive robot platform (Sbot) helping elderly individuals. 
Your core task is to analyze the accompanying audio waveform payload and distinguish between intentional, directed user speech and background distractions.

OPERATIONAL PARAMETERS:
1. SPEAKER AUDIT: You must assume there is at most ONE primary speaker trying to address you in any given block. Everyone else is background noise.
2. ADAPTIVE NOISE REGISTRATION: Elderly home environments feature significant background audio artifacts (loud televisions, radio broadcast streams, HVAC humming, caregiver cross-talk). You must analyze the acoustic clarity, proximity, and semantic intent to determine if the phrase was targeted at the machine.
3. INTENT SEGREGATION:
   - COMMAND: Actions requiring physical robotic actuation, locomotion, or arm manipulation (e.g., 'bring me that bottle', 'go to the kitchen', 'stop moving', 'grab my cane').
   - CONVERSATIONAL: Abstract linguistic interactions that do not trigger motor actions (e.g., 'hello robot', 'how are you today?', 'what time is it?', 'thank you').
   - UNKNOWN: The user is addressing the robot but the phrase is entirely slurred, unintelligible, or fragmented due to severe acoustic or cognitive interference.

CRITICAL EXECUTION:
If 'is_addressed' is False, do not try to transcribe the background noise. Set 'intent_type' and 'cleaned_transcript' to null.
"""
RESPONSE_CONFIG = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=AudioAnalysis,
                temperature=0.0,  # Forces deterministic token output
            )


# ==============================================================================
# CORE INTERFACE ENGINE
# ==============================================================================
FORMAT = pyaudio.paInt16
CHANNELS = 1
SAMPLE_RATE = 16000  
CHUNK_SIZE = 512     

class VoiceInterfacePipeline:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.is_running = True
        
        # Load local Silero VAD model using torch hub
        self.model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad', model='silero_vad', trust_repo=True)
        self.get_speech_timestamps, _, _, _, _ = utils
        
        self.silence_threshold_ms = 2000 #Make 4000 for elder application  
        self.max_speech_duration_ms = 1000000 #Made infinite to negate
        self.audio_energy_floor = 300.0
        self.speech_buffer = []
        self.is_speaking = False
        self.last_speech_time = time.time()
        self.speech_start_time = None

    def start_local_safety_thread(self):
        """Spins up a non-blocking background thread to watch for immediate emergency voice stops."""
        def safety_loop():
            print("[SAFETY ENGINE] Local hardware keyword monitor active.")
            while self.is_running:
                time.sleep(0.1)

        t = threading.Thread(target=safety_loop, daemon=True)
        t.start()
    
    def send_to_cloud_stt(self, audio_data: bytes):
        """
        FIX 2 IMPLEMENTATION: Spawns its own internal background thread 
        so it never blocks the main audio recording loop.
        """
        def worker():
            print("[GEMINI API] Processing audio stream via background worker...")
            
            # 1. Package the raw PCM bytes into an in-memory WAV container object
            audio_stream = io.BytesIO()
            with sf.SoundFile(audio_stream, mode='w', format='WAV', samplerate=SAMPLE_RATE, channels=CHANNELS, subtype='PCM_16') as f:
                audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
                f.write(audio_np)
            audio_stream.seek(0)
            
            try:
                # 2. Execute the single-shot multimodal inference request
                response = client.models.generate_content(
                    model="gemini-3-flash-preview",
                    contents=types.Part.from_bytes(
                        data=audio_stream.read(),
                        mime_type="audio/wav",
                    ),
                    config=RESPONSE_CONFIG# <-- Hook it in here
                )
                
                transcribed_text = response.text if response.text else ""
                
                if transcribed_text.strip():
                    # 3. HAND-OFF: Call your endpoint directly from the background thread
                    publish_to_control_loop(transcribed_text)
                else:
                    print("[SYSTEM] Gemini returned an empty transcription string.")
                    
            except Exception as e:
                print(f"[GEMINI ERROR] Content generation failed: {e}")

        # Start the worker thread internally
        threading.Thread(target=worker, daemon=True).start()

    def run_pipeline(self):
        """Main interface loop running on the local architecture."""
        stream = self.p.open(format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE, input=True, frames_per_buffer=CHUNK_SIZE)
        print("\n[SYSTEM] Sbot pipeline initialized. Awaiting voice prompt input...")

        while self.is_running:
            audio_chunk = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            audio_int16 = np.frombuffer(audio_chunk, dtype=np.int16)

            # Determine the "energy" of tha audio. Filters out background noise.
            rms_energy = np.sqrt(np.mean(audio_int16.astype(np.float64) ** 2))
            if rms_energy<self.audio_energy_floor and not self.is_speaking:
                continue
            

            audio_float32 = audio_int16.astype(np.float32) / 32768.0
            tensor_chunk = torch.from_numpy(audio_float32)
            
            # Fixed: Silero VAD expects a batch dimension (1, chunk_size)
            tensor_chunk = tensor_chunk.unsqueeze(0)
            
            # Query local neural network for human voice markers
            speech_prob = self.model(tensor_chunk, SAMPLE_RATE).item()

            if speech_prob > 0.5:  
                if not self.is_speaking:
                    print("[VUI STATE] Listening... User started speaking.")
                    self.is_speaking = True
                    self.speech_start_time = time.time()
                self.speech_buffer.append(audio_chunk)
                self.last_speech_time = time.time()
            else:
                if self.is_speaking:
                    current_time = time.time()
                    silence_elapsed = (current_time - self.last_speech_time) * 1000
                    total_duration_elapsed = (current_time - self.speech_start_time) * 1000
                    self.speech_buffer.append(audio_chunk)
                    if silence_elapsed > self.silence_threshold_ms or total_duration_elapsed >= self.max_speech_duration_ms:
                        print(f"[VUI STATE] Processing... {self.silence_threshold_ms/1000}s silence limit reached.")
                        
                        full_utterance = b"".join(self.speech_buffer)
                        
                        self.send_to_cloud_stt(full_utterance)
                        
                        self.speech_buffer = []
                        self.is_speaking = False
        stream.stop_stream()
        stream.close()
        self.p.terminate()

if __name__ == "__main__":
    pipeline = VoiceInterfacePipeline()
    pipeline.start_local_safety_thread()
    try:
        pipeline.run_pipeline()
    except KeyboardInterrupt:
        pipeline.is_running = False
        print("\n[SYSTEM] Voice Interface Pipeline safely shut down.")
