from aiohttp import web
from rtcbot import RTCConnection, getRTCBotJS, CVCamera
import ssl

routes = web.RouteTableDef()
camera = CVCamera()
conn = RTCConnection()

# Route the camera stream to the WebRTC connection
conn.video.putSubscription(camera)

# Listen for incoming data (joysticks, buttons) from the web app
@conn.subscribe
def onMessage(msg):
    msg_type = msg.get("type")
    
    if msg_type == "drive":
        # Access msg["x"], msg["y"], msg["yaw"]
        print(f"Drive: X={msg['x']:.2f}, Y={msg['y']:.2f}, Yaw={msg['yaw']:.2f}")

    elif msg_type == "arm_impulse":
        
        print(f"Arm Angles: {msg['base']:.2f}, {msg['shoulder']:.2f}, {msg['elbow']:.2f},{msg['wpitch']:.2f},{msg['wroll']:.2f},")
        
    elif msg_type == "arm_preset":
        print(f"Moving to preset: {msg['target']}")
        
    elif msg_type == "ESTOP":
        print("!!! EMERGENCY STOP ACTIVATED !!!")
        # Immediately kill motor power / set velocities to 0
    else print("Unknown message type: {msg}")


@routes.get("/")
async def index(request):
    with open("index.html", "r") as f:
        return web.Response(content_type="text/html", text=f.read())

@routes.get("/rtcbot.js")
async def rtcbotjs(request):
    # Serve the required rtcbot javascript library directly from the python module
    return web.Response(content_type="application/javascript", text=getRTCBotJS())
@routes.get("/styles.css")
async def styles(request):
    with open("styles.css", "r") as f:
        return web.Response(content_type="text/css", text=f.read())

@routes.get("/app.js")
async def javascript(request):
    with open("app.js", "r") as f:
        return web.Response(content_type="application/javascript", text=f.read())

@routes.post("/connect")
async def connect(request):
    # Establish the WebRTC peer connection
    clientOffer = await request.json()
    serverResponse = await conn.getLocalDescription(clientOffer)
    return web.json_response(serverResponse)

async def cleanup(app=None):
    await conn.close()
    camera.close()

app = web.Application()
app.add_routes(routes)
app.on_shutdown.append(cleanup)

if __name__ == "__main__":
    # Create an SSL context
    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_context.load_cert_chain('cert.pem', 'key.pem')

    # Run the app with the SSL context
    web.run_app(app, host="0.0.0.0", port=8080, ssl_context=ssl_context)
