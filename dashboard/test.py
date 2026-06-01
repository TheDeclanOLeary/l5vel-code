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
    while True:
        if not estop_active:
            for js_name, joint_idx in JOINT_MAPPING.items():
                impulse = current_impulses[js_name]
                target_joints[joint_idx] += impulse * DEG_PER_TICK
            try:
                arm.set_servo_angle_j(target_joints, is_radian=False)
            except NameError:
                pass 
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
            print("ESTOP TRIGGERED!")
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
    # Must use application/javascript content type to prevent browser blocking
    return web.Response(content_type="application/javascript", text=getRTCBotJS())

async def connect_handler(request):
    """Handles the WebRTC SDP offer from app.js natively using aiohttp utilities."""
    try:
        # Await the JSON object sent from the browser fetch request
        client_offer = await request.json()
        
        # Pass the offer to rtcbot to obtain the handshake layout
        server_response = await conn.getLocalDescription(client_offer)
        
        # Return cleanly using rtcbot's preferred aiohttp output type
        return web.json_response(server_response)
        
    except Exception as e:
        print(f"Handshake tracking failed error: {e}")
        return web.json_response({"error": str(e)}, status=500)

app.router.add_get("/", index_handler)
app.router.add_get("/rtcbot.js", rtcbotjs_handler)
app.router.add_post("/connect", connect_handler)
app.router.add_static("/", path=os.path.dirname(os.path.abspath(__file__)), name='static')

if __name__ == "__main__":
    asyncio.ensure_future(robot_control_loop())
    print("Serving dashboard on http://localhost:8080")
    web.run_app(app, port=8080)
