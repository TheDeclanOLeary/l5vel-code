import asyncio
import math
import os
import time
from aiohttp import web
from rtcbot import RTCConnection, getRTCBotJS
from xarm.wrapper import XArmAPI

# --- Configuration ---
ROBOT_IP = '172.16.0.10'
UPDATE_RATE_HZ = 20
TICK_DURATION = 1.0 / UPDATE_RATE_HZ

X_MIN, X_MAX = -520.0, 520.0
Y_MIN, Y_MAX = -520.0, 0.0   # LOCKED: Workspace restricted entirely to -Y
Z_MIN, Z_MAX = 50.0, 680.0   

# Dynamic Reach Variables
MAX_REACH_FOLDED = 420.0     # Safe radius when head is pointing straight down
MAX_REACH_EXTENDED = 520.0   # Safe radius when head is pointing straight outward
SHOULDER_Z_OFFSET = 266.0

MAX_MM_PER_SEC = 100.0  
MAX_DEG_PER_SEC = 30.0  
MM_PER_TICK = MAX_MM_PER_SEC * TICK_DURATION
DEG_PER_TICK = MAX_DEG_PER_SEC * TICK_DURATION

current_impulses = {'x': 0.0, 'y': 0.0, 'z': 0.0, 'roll': 0.0, 'pitch': 0.0, 'yaw': 0.0}
target_pose = [90.0, 0.0, 260.0, 180.0, 0.0, 0.0] 
estop_active = False
last_msg_time = time.time()

# --- Robot Hardware Init ---
print(f"Connecting to xArm at {ROBOT_IP}...")
try:
    arm = XArmAPI(ROBOT_IP)
    arm.clean_error()
    arm.motion_enable(enable=True)
    
    code, initial_pose = arm.get_position(is_radian=False)
    if code == 0:
        target_pose = initial_pose.copy()
        
    arm.set_mode(1)
    arm.set_state(0)
    print("Robot initialized in Cartesian Streaming Mode.")
except Exception as err:
    print(f"Hardware init skipped or failed: {err}. Running in simulation mode.")

# --- Asynchronous Cartesian Control Loop ---
async def robot_control_loop():
    global target_pose, estop_active, current_impulses
    
    debug_tick = 0 
    has_sent_error = False
    print("[System] Cartesian control loop started!")
    
    while True:
        try:
            if not estop_active:
                # ==========================================
                # 0. DEAD-MAN SWITCH (WATCHDOG)
                # If no data is received for 0.25 seconds, zero the inputs
                # ==========================================
                if time.time() - last_msg_time > 0.25:
                    for key in current_impulses.keys():
                        current_impulses[key] = 0.0
                # ==========================================
                # 1. PREDICTIVE BOUNDARY FILTER
                # ==========================================
                next_x = target_pose[0] + current_impulses['x'] * MM_PER_TICK
                next_y = target_pose[1] + current_impulses['y'] * MM_PER_TICK
                next_z = target_pose[2] + current_impulses['z'] * MM_PER_TICK

                if (next_x > X_MAX and current_impulses['x'] > 0) or (next_x < X_MIN and current_impulses['x'] < 0):
                    current_impulses['x'] = 0.0
                if (next_y > Y_MAX and current_impulses['y'] > 0) or (next_y < Y_MIN and current_impulses['y'] < 0):
                    current_impulses['y'] = 0.0
                if (next_z > Z_MAX and current_impulses['z'] > 0) or (next_z < Z_MIN and current_impulses['z'] < 0):
                    current_impulses['z'] = 0.0
                
                # --- Dynamic Spherical Leash ---
                next_rel_z = next_z - SHOULDER_Z_OFFSET
                radius = math.sqrt(next_x**2 + next_y**2 + next_rel_z**2)
                
                # Calculate how "extended" the head is based on Pitch
                # math.sin(0) = 0.0 (Folded) | math.sin(90 degrees) = 1.0 (Extended)
                pitch_rads = math.radians(target_pose[4])
                extension_ratio = abs(math.sin(pitch_rads))
                
                # Expand the safe sphere dynamically based on the orientation
                dynamic_max_reach = MAX_REACH_FOLDED + ((MAX_REACH_EXTENDED - MAX_REACH_FOLDED) * extension_ratio)
                
                if radius > dynamic_max_reach:
                    current_rel_z = target_pose[2] - SHOULDER_Z_OFFSET
                    dot_product = (target_pose[0] * current_impulses['x']) + \
                                  (target_pose[1] * current_impulses['y']) + \
                                  (current_rel_z * current_impulses['z'])
                    
                    if dot_product > 0: 
                        current_impulses['x'] = 0.0
                        current_impulses['y'] = 0.0
                        current_impulses['z'] = 0.0

                # ==========================================
                # 2. APPLY FILTERED IMPULSES
                # ==========================================
                target_pose[0] += current_impulses['x'] * MM_PER_TICK
                target_pose[1] += current_impulses['y'] * MM_PER_TICK
                target_pose[2] += current_impulses['z'] * MM_PER_TICK
                target_pose[3] += current_impulses['roll'] * DEG_PER_TICK
                target_pose[4] += current_impulses['pitch'] * DEG_PER_TICK
                target_pose[5] += current_impulses['yaw'] * DEG_PER_TICK

                # ==========================================
                # 3. HARDWARE COMMAND & FAULT DETECTION
                # ==========================================
                if 'arm' in globals():
                    code, state = arm.get_state()
                    
                    if state == 4 and not has_sent_error:
                        err_code = arm.get_err_code()
                        msg_text = f"Robot Faulted (State 4). Error Code: {err_code}."
                        if err_code in [11, 12, 14]:
                            msg_text = "Kinematic Singularity or Joint Limit reached! The arm cannot execute this straight path."
                        
                        conn.put_nowait({"type": "ROBOT_FAULT", "message": msg_text})
                        has_sent_error = True
                        print(f"[ALERT] Sent fault notice to web app: {msg_text}")
                        
                    elif state == 0:
                        has_sent_error = False

                    arm.set_servo_cartesian(target_pose, is_tool_coord=False, is_radian=False)
                
                debug_tick += 1
                if debug_tick >= UPDATE_RATE_HZ:
                    print(f"Streaming Target XYZ: [{target_pose[0]:.1f}, {target_pose[1]:.1f}, {target_pose[2]:.1f}]")
                    debug_tick = 0

        except Exception as e:
            print(f"\n[CRITICAL ERROR] Control Loop Fault: {e}")
            
        await asyncio.sleep(TICK_DURATION)


# --- WebRTC Data Handler ---
conn = RTCConnection()

@conn.subscribe
def on_message(msg):
    global current_impulses, estop_active, target_pose
    try:
        msg_type = msg.get("type")
        
        if msg_type == "ESTOP":
            print("\n*** ESTOP TRIGGERED VIA WEBRTC ***\n")
            estop_active = True
            try: arm.set_state(4)
            except: pass
            
        elif msg_type == "CLEAR_ERROR":
            print("\n[System] Manual error clear triggered. Waking arm safely...")
            if 'arm' in globals():
                arm.clean_error()
                arm.motion_enable(enable=True)
                arm.set_mode(1)
                arm.set_state(0)
                
                code, current_hardware_pos = arm.get_position(is_radian=False)
                if code == 0:
                    target_pose = current_hardware_pos.copy()
                    print(f"[System] Trajectory re-synchronized safely at: {[round(x,1) for x in target_pose]}")
            
            estop_active = False 
        
        elif msg_type == "cartesian_impulse" and not estop_active:
            global last_msg_time
            last_msg_time = time.time()
            # Data reception only. No math or hardware commands executed here.
            for key in current_impulses.keys():
                if key in msg:
                    current_impulses[key] = float(msg[key])

    except Exception as e:
        print(f"Handler processing error: {e}")


# --- Web Server Routes & Lifecycle Hooks ---
app = web.Application()

async def index_handler(request):
    with open("index.html", "r") as f:
        return web.Response(text=f.read(), content_type="text/html")

async def rtcbotjs_handler(request):
    return web.Response(content_type="application/javascript", text=getRTCBotJS())

async def connect_handler(request):
    try:
        client_offer = await request.json()
        server_response = await conn.getLocalDescription(client_offer)
        return web.json_response(server_response)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

app.router.add_get("/", index_handler)
app.router.add_get("/rtcbot.js", rtcbotjs_handler)
app.router.add_post("/connect", connect_handler)
app.router.add_static("/", path=os.path.dirname(os.path.abspath(__file__)), name='static')

async def start_background_tasks(app):
    print("[System] Injecting robot control loop into active event loop...")
    app['robot_loop'] = asyncio.create_task(robot_control_loop())

app.on_startup.append(start_background_tasks)

if __name__ == "__main__":
    print("Serving dashboard on http://localhost:8080")
    web.run_app(app, port=8080)
