from aiohttp import web
from rtcbot import RTCConnection, getRTCBotJS, CVCamera
from xarm.wrapper import XArmAPI
import ssl
import time

# --- Hardware Initialization & Safety Constraints ---
# Replace with the static IP assigned to the physical xArm control box
ROBOT_IP = '172.16.0.10'  

print(f"Connecting to physical xArm at {ROBOT_IP}...")
arm = XArmAPI(ROBOT_IP)

print("Running hardware safety protocol...")
arm.clean_error()
arm.motion_enable(enable=True)

# Set collision sensitivity (0-5, where 0 is off and 5 is highly sensitive)
# Setting this to 3 ensures the arm halts if it strikes an unexpected object.
arm.set_collision_sensitivity(3)

arm.set_mode(0)         # Position control mode
arm.set_state(state=0)  # Ready/Sport state
# --- Initialize State Tracker ---
# Query the physical arm for its starting position
code, init_angles = arm.get_servo_angle(is_radian=False)
if code == 0:
    current_target_angles = init_angles[:6]
else:
    current_target_angles = [-90.0, 0.0, 180.0, 0.0, 0.0, 90.0]

# Define how many degrees the arm should move per tick at full joystick deflection (1.0 or -1.0)
MAX_DEG_PER_TICK = 2.0

# Global throttle tracking for WebRTC inputs
last_cmd_time = 0.0

routes = web.RouteTableDef()
camera = CVCamera()
conn = RTCConnection()

conn.video.putSubscription(camera)

@conn.subscribe
def onMessage(msg):
    global last_cmd_time, current_target_angles
    msg_type = msg.get("type")
    
    if msg_type == "drive":
        print(f"Drive: X={msg['x']:.2f}, Y={msg['y']:.2f}, Yaw={msg['yaw']:.2f}")

    elif msg_type == "arm_impulse":
        current_time = time.time()
        
        if current_time - last_cmd_time > 0.1:
            # Map the -1 to 1 impulses from the UI
            impulses = [
                msg.get('base', 0.0), 
                msg.get('shoulder', 0.0), 
                msg.get('elbow', 0.0), 
                msg.get('wpitch', 0.0), 
                msg.get('wroll', 0.0), 
                0.0 # 6th joint padding
            ]
            
            # Integrate impulses into absolute target angles
            for i in range(6):
                current_target_angles[i] += impulses[i] * MAX_DEG_PER_TICK
                
            # Send the newly calculated absolute position to the arm
            arm.set_servo_angle(angles=current_target_angles, speed=20, wait=False)
            last_cmd_time = current_time
            
    elif msg_type == "arm_preset":
        target = msg['target']
        print(f"Moving physical arm to preset: {target}")
        
        if target == "home":
            current_target_angles = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            arm.set_servo_angle(angles=current_target_angles, speed=20, wait=False)
        elif target == "stow":
            current_target_angles = [0.0, -45.0, 0.0, 0.0, 45.0, 0.0]
            arm.set_servo_angle(angles=current_target_angles, speed=20, wait=False)
        else:
            print(f"Unknown preset: {target}")
            
    elif msg_type == "ESTOP":
        print("!!! HARDWARE EMERGENCY STOP ACTIVATED !!!")
        arm.emergency_stop()
        # Query real position after an ESTOP to resync the mathematical tracker
        time.sleep(0.1)
        code, safe_angles = arm.get_servo_angle(is_radian=False)
        if code == 0:
            current_target_angles = safe_angles[:6]

# --- Asset Routing Configuration ---

@routes.get("/")
async def index(request):
    # Explicitly serve index.html from the dist folder
    with open("dist/index.html", "r") as f:
        return web.Response(content_type="text/html", text=f.read())

@routes.get("/styles.css")
async def styles(request):
    # Explicitly serve styles.css from the dist folder
    with open("dist/styles.css", "r") as f:
        return web.Response(content_type="text/css", text=f.read())

@routes.get("/app.js")
async def javascript(request):
    # Explicitly serve app.js from the ROOT folder where it currently lives
    with open("app.js", "r") as f:
        return web.Response(content_type="application/javascript", text=f.read())

@routes.get("/rtcbot.js")
async def rtcbotjs(request):
    # Serves the generated rtcbot library
    return web.Response(content_type="application/javascript", text=getRTCBotJS())

# Fallback route for any other static assets (like images or icons) inside dist
routes.static("/", "dist/")

@routes.post("/connect")
async def connect(request):
    clientOffer = await request.json()
    serverResponse = await conn.getLocalDescription(clientOffer)
    return web.json_response(serverResponse)

async def cleanup(app=None):
    print("Safely shutting down hardware connections...")
    await conn.close()
    camera.close()
    
    # Ensure the arm is parked or errors are cleared before disconnecting
    arm.clean_error()
    arm.disconnect()

app = web.Application()
app.add_routes(routes)
app.on_shutdown.append(cleanup)

if __name__ == "__main__":
    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_context.load_cert_chain('cert.pem', 'key.pem')

    web.run_app(app, host="0.0.0.0", port=8080, ssl_context=ssl_context)
