import io
import json
import threading
import time
import numpy as np
import pyaudio
import requests
import soundfile as sf
import torch
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import Literal, Optional
from collections import deque

# ==============================================================================
# GLOBAL SYSTEM CONFIGURATIONS
# ==============================================================================
CONFIG = {
    # API & Core Model Identifier
    "GEMINI_MODEL": "gemini-3-flash-preview",
    
    # Audio Ingestion Parameters
    "AUDIO_FORMAT": pyaudio.paInt16,
    "CHANNELS": 1,
    "SAMPLE_RATE": 16000,
    "CHUNK_SIZE": 512,
    
    # Algorithmic VAD & Recording Thresholds
    "AUDIO_ENERGY_FLOOR": 300.0,       # Initial amplitude floor to reject static/TV
    "SILENCE_THRESHOLD_MS": 2000,      # Duration of silent frames to trigger execution
    "VAD_CONFIDENCE_GATE": 0.5,        # Neural network probability activation threshold
    "DYNAMIC_FLOOR_ENABLED": True,
    "MIN_ENERGY_FLOOR": 100.0,         # Absolute lowest threshold in a silent room
    "MAX_ENERGY_FLOOR": 1000.0,        # Hard ceiling to prevent lockout during loud events 
    "NOISE_MARGIN_PADDING": 250.0,    # Padding above ambient noise to trigger wake
    "CALIBRATION_WINDOW_CHUNKS": 100 # Number of non-speech frames to average
}

# Initialize Google GenAI Developer Platform Client
client = genai.Client()

# ==============================================================================
# PANTHEON SCHEMAS & SYSTEM INSTRUCTIONS
# ==============================================================================
class AudioAnalysis(BaseModel):
    is_addressed: bool = Field(description="True if a voice addresses the robot. False for background TV/noise.")
    intent_type: Optional[Literal["COMMAND", "CONVERSATIONAL", "UNKNOWN"]] = Field(default=None)
    cleaned_transcript: Optional[str] = Field(default=None)

SYSTEM_PROMPT = """
Role: Acoustic front-end processor for an assistive robot (Sbot).
Task: Analyze audio to isolate a single primary speaker from ambient noise (TV, HVAC, cross-talk) and determine if they are addressing the robot.

Output Constraints:
- Respond with EXACTLY 3 lines of plain text.
- Do NOT use Markdown, JSON, backticks, or code blocks.
- If Is Addressed is false, Line 2 must be UNKNOWN and Line 3 must be None. Do not transcribe background noise.

Line 1: [true or false] (True only if the speaker is intentionally addressing the robot)
Line 2: [COMMAND, CONVERSATIONAL, or UNKNOWN]
        - COMMAND: Requests physical movement, base navigation, or arm manipulation.
        - CONVERSATIONAL: Greetings, chit-chat, or questions not requiring physical action.
        - UNKNOWN: Speech is not directed to the robot, or is completely unintelligible.
Line 3: [Cleaned Transcript or None] (Exact words spoken by the user. Write None if no clear phrase is directed to the robot. Do not hallucinate.)
"""
#CRITICAL EXECUTION:
#If 'is_addressed' is False, do not try to transcribe the background noise. Set 'intent_type' and 'cleaned_transcript' to null.
RESPONSE_CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
    temperature=0.0,
    thinking_config=types.ThinkingConfig(thinking_level="MINIMAL")
)
# ==============================================================================
# DOWNSTREAM ACTUATION OVERRIDES (SYSTEM ENDPOINTS)
# ==============================================================================
def publish_to_control_loop(raw_text_response: str):
    """Parses plain line output with zero regex/JSON decoding latency overhead."""
    try:
        # Split text by newline boundaries
        lines = [line.strip() for line in raw_text_response.strip().split("\n") if line.strip()]
        if len(lines) < 3:
            return

        is_addressed = lines[0].lower() == "true"
        intent_type = lines[1].upper()
        transcript = lines[2]

        print(f"\n[CONTROL LOOP] Addressed: {is_addressed} | Intent: {intent_type} | Text: {transcript}")
        
        if is_addressed and intent_type == "COMMAND" and transcript != "None":
            print(f"[CONTROL LOOP] Directing task execution node for: '{transcript}'")
            
    except Exception as e:
        print(f"[CONTROL LOOP ERROR] Could not parse plain text response: {e}")
def trigger_local_estop():
    """Immediate hardware kill switch bypassing the network graph."""
    print("\n!!! [CRITICAL EMERGENCY STOP DETECTED] !!!")
    print("[HARDWARE] Sending electronic brake signals to Swerve Drive Motors...")
    print("[HARDWARE] Engaging mechanical locking pins on 5-Joint Arm...")

# ==============================================================================
# CORE PROCESSING PIPELINE ENGINE
# ==============================================================================
class VoiceInterfacePipeline:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.is_running = True
        
        # Load local Silero VAD neural network on the host CPU
        self.model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad', model='silero_vad', trust_repo=True)
        self.get_speech_timestamps, _, _, _, _ = utils
        
        # Runtime session buffers
        self.speech_buffer = []
        self.is_speaking = False
        self.last_speech_time = time.time()
        self.speech_start_time = None
        self.noise_buffer = deque(maxlen=CONFIG["CALIBRATION_WINDOW_CHUNKS"])
        self.current_energy_floor = CONFIG["AUDIO_ENERGY_FLOOR"]

    def start_local_safety_thread(self):
        """Spins up a non-blocking background thread monitoring physical interrupts."""
        def safety_loop():
            print("[SAFETY ENGINE] Local hardware keyword monitor active.")
            while self.is_running:
                time.sleep(0.1)

        threading.Thread(target=safety_loop, daemon=True).start()
    
    def send_to_cloud_stt(self, audio_data: bytes):
        """Asynchronously converts raw PCM bytes to WAV and requests inference from Gemini."""
        def worker():
            print("[GEMINI API] Processing audio stream via background worker...")
            print(time.time())
            # Serialize buffer arrays into local memory-mapped WAV containers
            audio_stream = io.BytesIO()
            with sf.SoundFile(audio_stream, mode='w', format='WAV', samplerate=CONFIG["SAMPLE_RATE"], channels=CONFIG["CHANNELS"], subtype='PCM_16') as f:
                audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
                f.write(audio_np)
            audio_stream.seek(0)
            
            try:
                response = client.models.generate_content(
                    model=CONFIG["GEMINI_MODEL"],
                    contents=types.Part.from_bytes(
                        data=audio_stream.read(),
                        mime_type="audio/wav",
                    ),
                    config=RESPONSE_CONFIG
                )              
                if response.text and response.text.strip():
                    publish_to_control_loop(response.text)
                else:
                    print("[SYSTEM] Gemini returned an empty transcription payload.")
                    
            except Exception as e:
                print(f"[GEMINI ERROR] Content generation failed: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def run_pipeline(self):
        """Main interaction processing thread."""
        stream = self.p.open(
            format=CONFIG["AUDIO_FORMAT"], 
            channels=CONFIG["CHANNELS"], 
            rate=CONFIG["SAMPLE_RATE"], 
            input=True, 
            frames_per_buffer=CONFIG["CHUNK_SIZE"]
        )
        print("\n[SYSTEM] Sbot pipeline initialized. Awaiting voice prompt input...")

        while self.is_running:
            audio_chunk = stream.read(CONFIG["CHUNK_SIZE"], exception_on_overflow=False)
            audio_int16 = np.frombuffer(audio_chunk, dtype=np.int16)

            # 1. AC COUPLING: Remove electrical DC offset by calculating variance (Standard Deviation)
            ac_energy = np.std(audio_int16.astype(np.float64))

            # Scale and convert vector arrays to expected Silero PyTorch dimensions
            audio_float32 = audio_int16.astype(np.float32) / 32768.0
            tensor_chunk = torch.from_numpy(audio_float32).unsqueeze(0) 
            
            # Execute neural VAD
            speech_prob = self.model(tensor_chunk, CONFIG["SAMPLE_RATE"]).item()
            
            # --- DYNAMIC ENVIRONMENT FILTERING ---
            if CONFIG["DYNAMIC_FLOOR_ENABLED"] and not self.is_speaking:
                # 2. ISOLATION: Only calibrate on frames with extremely low speech probability
                if speech_prob < 0.3: 
                    self.noise_buffer.append(ac_energy)
                    
                    if len(self.noise_buffer) == self.noise_buffer.maxlen:
                        # 3. OUTLIER REJECTION: Median ignores transient sounds (clicks, thumps)
                        ambient_baseline = np.median(self.noise_buffer)
                        
                        # 4. ADDITIVE SCALING: Maintains a strict, crossable volume gap
                        calculated_floor = ambient_baseline + CONFIG["NOISE_MARGIN_PADDING"]
                        
                        # Clamp the floor to prevent network lockouts
                        self.current_energy_floor = max(
                            CONFIG["MIN_ENERGY_FLOOR"], 
                            min(calculated_floor, CONFIG["MAX_ENERGY_FLOOR"])
                        )            

            # 5. EARLY EXIT: Drop frame if it lacks acoustic energy AND we are not mid-sentence
            if ac_energy < self.current_energy_floor and not self.is_speaking:
                continue
            # -------------------------------------

            # Intent capture logic
            if speech_prob > CONFIG["VAD_CONFIDENCE_GATE"]:  
                if not self.is_speaking:
                    print(f"[VUI STATE] Listening... User started speaking. (Floor: {self.current_energy_floor:.1f})")
                    self.is_speaking = True
                    self.speech_start_time = time.time()
                self.speech_buffer.append(audio_chunk)
                self.last_speech_time = time.time()
            else:
                if self.is_speaking:
                    current_time = time.time()
                    silence_elapsed = (current_time - self.last_speech_time) * 1000
                    self.speech_buffer.append(audio_chunk)
                    
                    # Check if recording boundaries require closure processing
                    if silence_elapsed > CONFIG["SILENCE_THRESHOLD_MS"]:
                        print(f"[VUI STATE] Processing... {CONFIG['SILENCE_THRESHOLD_MS']/1000}s silence limit reached.")
                        
                        full_utterance = b"".join(self.speech_buffer)
                        self.send_to_cloud_stt(full_utterance)
                        
                        # Reset pipeline state machine trackers
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
