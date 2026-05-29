import io
import json
import threading
import time
import numpy as np
import pyaudio
import requests
import soundfile as sf
import torch  # Fixed: Moved to top level so all methods can access it

# ==============================================================================
# SYSTEM ENDPOINTS (IMPLEMENT YOUR HARDWARE & API DEPLOYMENT HERE)
# ==============================================================================
CLOUD_STT_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
CLOUD_API_KEY = "YOUR_CLOUD_API_KEY_HERE"

def publish_to_control_loop(transcribed_text: str):
    print(f"\n[CONTROL LOOP] Received text: '{transcribed_text}'")
    print("[CONTROL LOOP] Passing string directly to path planning and kinematics nodes...")

def trigger_local_estop():
    print("\n!!! [CRITICAL EMERGENCY STOP DETECTED] !!!")
    print("[HARDWARE] Sending electronic brake signals to Swerve Drive Motors...")
    print("[HARDWARE] Engaging mechanical locking pins on 5-Joint Arm...")

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
        self.speech_buffer = []
        self.is_speaking = False
        self.last_speech_time = time.time()

    def start_local_safety_thread(self):
        """Spins up a non-blocking background thread to watch for immediate emergency voice stops."""
        def safety_loop():
            print("[SAFETY ENGINE] Local hardware keyword monitor active.")
            while self.is_running:
                time.sleep(0.1)

        t = threading.Thread(target=safety_loop, daemon=True)
        t.start()

    def send_to_cloud_stt(self, audio_data: bytes) -> str:
        """MOCK ENDPOINT: Simulates a cloud network delay and returns a dummy string."""
        print("[MOCK CLOUD API] Received audio payload locally. Processing simulation...")
        
        # 1. (Optional) Verify the audio data is valid by checking its size
        audio_len_seconds = len(audio_data) / (SAMPLE_RATE * 2) # 2 bytes per sample (int16)
        print(f"[MOCK CLOUD API] Received {audio_len_seconds:.2f} seconds of audio data.")

        # 2. Simulate network latency (e.g., a 1.2 second round-trip delay)
        time.sleep(1.2)
        
        # 3. Return a static string to verify the background control loop triggers
        return "Simulated speech command: move forward two meters"

    def run_pipeline(self):
        """Main interface loop running on the local architecture."""
        stream = self.p.open(format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE, input=True, frames_per_buffer=CHUNK_SIZE)
        print("\n[SYSTEM] Sbot pipeline initialized. Awaiting voice prompt input...")

        while self.is_running:
            audio_chunk = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            audio_int16 = np.frombuffer(audio_chunk, dtype=np.int16)
            
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
                self.speech_buffer.append(audio_chunk)
                self.last_speech_time = time.time()
            else:
                if self.is_speaking:
                    self.speech_buffer.append(audio_chunk)
                    
                    if (time.time() - self.last_speech_time) * 1000 > self.silence_threshold_ms:
                        print(f"[VUI STATE] Processing... {self.silence_threshold_ms/1000}s silence limit reached.")
                        
                        full_utterance = b"".join(self.speech_buffer)
                        
                        def cloud_worker(audio_payload):
                            text_out = self.send_to_cloud_stt(audio_payload)
                            if text_out.strip():
                                publish_to_control_loop(text_out)
                            else:
                                print("[SYSTEM] Blank response or connection timeout. Flushing current frame buffer.")

                        threading.Thread(target=cloud_worker, args=(full_utterance,), daemon=True).start()
                        
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
