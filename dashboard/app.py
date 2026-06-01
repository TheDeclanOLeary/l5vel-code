import asyncio
import os
from aiohttp import web
from rtcbot import RTCConnection, getRTCBotJS
from xarm.wrapper import XArmAPI

# --- Configuration ---
ROBOT_IP = '172.16.0.10'
UPDATE_RATE_HZ = 20
TICK_DURATION = 1.0 / UPDATE_RATE_HZ
MAX_DEG_PER_SEC = 20.0 
DEG_PER_TICK = MAX_DEG_PER_SEC * TICK_DURATION 

current_impulses = {
    'base': 0.0, 'shoulder': 0.0, 'elbow': 0.0, 
    'wpitch': 0.0, 'wyaw': 0.0, 'wroll': 0.0
}
target_joints = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
estop_active = False

JOINT_MAPPING = {
    'base': 0, 'shoulder': 1, 'elbow': 2, 
    'wpitch': 3, 'wyaw': 4, 'wroll': 5  
}

# --- Robot Hardware Init ---
print(f"Connecting to xArm at {ROBOT_IP}...")
try:
    arm = XArmAPI(ROBOT_IP)
    arm.clean_error()
    arm.motion_enable(enable=True)
    code, initial_joints = arm.get_servo_angle(is_radian=False)
    if code == 0:
        target_joints = initial_joints.copy()
    arm.set_mode(1) # Servoj Mode
    arm.set_state(0)
    print("Robot ready.")
except Exception as err:
    print(f"Robot connection skipped or failed: {err}. Running in simulation mode.")

# --- Asynchronous Control Loop ---
async def robot_control_loop():
    global target_joints, estop_active
    
    debug_tick = 0 
    print("[System] Robot control loop successfully started!")
    
    while True:
        try:
            if not estop_active:
                # 1. Calculate new targets
                for js_name, joint_idx in JOINT_MAPPING.items():
                    impulse = current_impulses[js_name]
                    target_joints[joint_idx] += impulse * DEG_PER_TICK
                
                # 2. --- DEBUG TRACKING (Moved UP to guarantee execution) ---
                debug_tick += 1
                if debug_tick >= UPDATE_RATE_HZ:  # Triggers once per second
                    print("\n--- DATA FLOW CHECK ---")
                    print(f"IN (Impulses): {[round(current_impulses[k], 2) for k in JOINT_MAPPING.keys()]}")
                    print(f"OUT (Targets): {[round(j, 2) for j in target_joints]}")
                    debug_tick = 0

                # 3. Hardware command (Safely checked)
                if 'arm' in globals():
                    # If arm exists, attempt to send the command
                    arm.set_servo_angle_j(target_joints, is_radian=False)
                    
        except Exception as e:
            # THIS is what was missing. If the SDK fails, it will now print exactly why.
            print(f"\n[CRITICAL ERROR] The control loop caught an exception: {e}")
            
        # Yield back to the event loop so the web server doesn't freeze
        await asyncio.sleep(TICK_DURATION)

# --- WebRTC Data Handler ---
conn = RTCConnection()

@conn.subscribe
def on_message(msg):
    """
    Called by rtcbot when a message arrives.
    'msg' is automatically parsed into a dict by rtcbot.
    """
    global current_impulses, estop_active
    try:
        msg_type = msg.get("type")
        if msg_type == "ESTOP":
            print("\n*** ESTOP TRIGGERED VIA WEBRTC ***\n")
            estop_active = True
            try: arm.set_state(4)
            except: pass
        elif msg_type == "arm_impulse" and not estop_active:
            for key in current_impulses.keys():
                if key in msg:
                    current_impulses[key] = float(msg[key])
    except Exception as e:
        print(f"Handler processing error: {e}")
# --- Web Server Routes ---
app = web.Application()

async def index_handler(request):
    with open("index.html", "r") as f:
        return web.Response(text=f.read(), content_type="text/html")

async def rtcbotjs_handler(request):
    return web.Response(content_type="application/javascript", text=getRTCBotJS())

async def connect_handler(request):
    try:
        print("\n[Handshake] Received SDP offer from client. Negotiating...")
        client_offer = await request.json()
        server_response = await conn.getLocalDescription(client_offer)
        print("[Handshake] Successfully generated local description. Sending to client.")
        return web.json_response(server_response)
    except Exception as e:
        print(f"[Handshake] Failed error: {e}")
        return web.json_response({"error": str(e)}, status=500)

app.router.add_get("/", index_handler)
app.router.add_get("/rtcbot.js", rtcbotjs_handler)
app.router.add_post("/connect", connect_handler)
app.router.add_static("/", path=os.path.dirname(os.path.abspath(__file__)), name='static')


# --- NEW: App Lifecycle Hooks ---
async def start_background_tasks(app):
    """Fired automatically by aiohttp when the server starts."""
    print("[System] Injecting robot control loop into active event loop...")
    # create_task ensures it runs on the web server's actual event loop
    app['robot_loop'] = asyncio.create_task(robot_control_loop())

# Register the startup hook
app.on_startup.append(start_background_tasks)


if __name__ == "__main__":
    print("Serving dashboard on http://localhost:8080")
    # run_app handles the rest automatically now
    web.run_app(app, port=8080)
