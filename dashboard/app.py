import asyncio
import os
from aiohttp import web
from rtcbot import RTCConnection, getRTCBotJS
from xarm.wrapper import XArmAPI

# --- Configuration ---
ROBOT_IP = '172.16.0.10' # Change this to match your physical xArm IP
UPDATE_RATE_HZ = 20
TICK_DURATION = 1.0 / UPDATE_RATE_HZ

# Max speeds: mm/s for XYZ, degrees/s for Roll/Pitch/Yaw
MAX_MM_PER_SEC = 100.0  
MAX_DEG_PER_SEC = 30.0  
MM_PER_TICK = MAX_MM_PER_SEC * TICK_DURATION
DEG_PER_TICK = MAX_DEG_PER_SEC * TICK_DURATION

current_impulses = {'x': 0.0, 'y': 0.0, 'z': 0.0, 'roll': 0.0, 'pitch': 0.0, 'yaw': 0.0}
target_pose = [90.0, 0.0, 260.0, 180.0, 0.0, 0.0] 
estop_active = False

# --- Robot Hardware Init ---
print(f"Connecting to xArm at {ROBOT_IP}...")
try:
    arm = XArmAPI(ROBOT_IP)
    arm.clean_error()
    arm.motion_enable(enable=True)
    
    # Read initial Cartesian Position to seed the trajectory
    code, initial_pose = arm.get_position(is_radian=False)
    if code == 0:
        target_pose = initial_pose.copy()
        
    arm.set_mode(1) # Mode 1 supports continuous Cartesian streaming
    arm.set_state(0)
    print("Robot initialized in Cartesian Streaming Mode.")
except Exception as err:
    print(f"Hardware init skipped or failed: {err}. Running in simulation mode.")

# --- Asynchronous Cartesian Control Loop ---
async def robot_control_loop():
    global target_pose, estop_active, current_impulses
    
    debug_tick = 0 
    print("[System] Cartesian control loop started!")
    
    while True:
        try:
            if not estop_active:
                # 1. Apply Impulses
                target_pose[0] += current_impulses['x'] * MM_PER_TICK
                target_pose[1] += current_impulses['y'] * MM_PER_TICK
                target_pose[2] += current_impulses['z'] * MM_PER_TICK
                target_pose[3] += current_impulses['roll'] * DEG_PER_TICK
                target_pose[4] += current_impulses['pitch'] * DEG_PER_TICK
                target_pose[5] += current_impulses['yaw'] * DEG_PER_TICK

                # 2. Safety Boundary: Prevent crashing into the table
                if target_pose[2] < 50.0:
                    target_pose[2] = 50.0

                # 3. Hardware Command
                if 'arm' in globals():
                    arm.set_servo_cartesian(target_pose, is_tool_coord=False, is_radian=False)
                
                # 4. Debug tracking
                debug_tick += 1
                if debug_tick >= UPDATE_RATE_HZ:
                    print(f"Streaming Target XYZ: [{target_pose[0]:.1f}, {target_pose[1]:.1f}, {target_pose[2]:.1f}]")
                    debug_tick = 0

        except Exception as e:
            print(f"\n[CRITICAL ERROR] IK/Cartesian Fault: {e}")
            
        await asyncio.sleep(TICK_DURATION)

# --- WebRTC Data Handler ---
conn = RTCConnection()

@conn.subscribe
def on_message(msg):
    global current_impulses, estop_active
    try:
        msg_type = msg.get("type")
        
        if msg_type == "ESTOP":
            print("\n*** ESTOP TRIGGERED VIA WEBRTC ***\n")
            estop_active = True
            try: arm.set_state(4)
            except: pass

        elif msg_type == "CLEAR_ERROR":
            print("\n[System] Manual error clear triggered. Waking arm...")
            if 'arm' in globals():
                arm.clean_error()
                arm.motion_enable(enable=True)
                arm.set_mode(1)
                arm.set_state(0)
            
        elif msg_type == "cartesian_impulse" and not estop_active:
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
