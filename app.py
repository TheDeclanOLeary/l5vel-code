import asyncio
import math
import os
import time

from aiohttp import web
from rtcbot import RTCConnection, getRTCBotJS

from xarm_code import XArmHandler

# --- Configuration ---
ROBOT_IP = "172.16.0.10"
UPDATE_RATE_HZ = 20
TICK_DURATION = 1.0 / UPDATE_RATE_HZ

X_MIN, X_MAX = -520.0, 520.0
Y_MIN, Y_MAX = -520.0, 0.0
Z_MIN, Z_MAX = 50.0, 680.0

MAX_REACH_FOLDED = 420.0
MAX_REACH_EXTENDED = 520.0
SHOULDER_Z_OFFSET = 266.0

MAX_MM_PER_SEC = 100.0
MAX_DEG_PER_SEC = 30.0
MM_PER_TICK = MAX_MM_PER_SEC * TICK_DURATION
DEG_PER_TICK = MAX_DEG_PER_SEC * TICK_DURATION

current_impulses = {"x": 0.0, "y": 0.0, "z": 0.0, "roll": 0.0, "pitch": 0.0, "yaw": 0.0}
target_pose = [90.0, 0.0, 260.0, 180.0, 0.0, 0.0]
estop_active = False
last_msg_time = time.time()
is_playing_trajectory = False

trajectories = {"wave": ("wave2.traj", (76.5, -53.5, 726.8, -8.2, 25.4, -62))}

# --- Robot Hardware Init via XArmHandler ---
print(f"Connecting to xArm at {ROBOT_IP}...")
try:
    robot = XArmHandler(robot_ip=ROBOT_IP, gripper=None, print_tag="Web_Dash")
    robot.xarm_init()

    if robot.is_startup_done():
        # WE NO LONGER STOP THE ERROR MONITOR HERE.
        # The backend handler will now autonomously catch errors,
        # clear them, and run its reset sequence.

        # Define the 3D bounding box for the obstacle
        # [x, y, z] in millimeters relative to the robot's base
        box_min = [0.0, 100.0, 0.0]
        box_max = [-500.0, 500.0, 1000.0]

        # Add the region. This automatically starts the background monitoring thread.
        robot.add_avoidance_region(box_min, box_max)

        # To verify, you can print the active regions to the console
        robot.list_avoidance_regions()

        code, initial_pose = robot.api_get_position(is_radian=False)
        if code == 0:
            target_pose = initial_pose.copy()

        robot.api_set_mode(1)
        robot.api_set_state(0)
        print("Robot initialized in Cartesian Streaming Mode via XArmHandler.")
    else:
        print("Robot startup failed.")
except Exception as err:
    print(f"Hardware init skipped or failed: {err}. Running in simulation mode.")


# --- Asynchronous Cartesian Control Loop ---
async def robot_control_loop():
    global target_pose, estop_active, current_impulses, is_playing_trajectory

    debug_tick = 0
    print("[System] Cartesian control loop started!")

    while True:
        try:
            if not estop_active and not is_playing_trajectory:
                # Check if the backend is actively handling an error
                if (
                    "robot" in globals()
                    and robot.is_connected()
                    and robot.is_resetting()
                ):
                    # Zero out impulses so it doesn't jump after recovery
                    for key in current_impulses.keys():
                        current_impulses[key] = 0.0

                    # Re-sync target pose to wherever the robot safely recovered to
                    code, recovered_pose = robot.api_get_position(is_radian=False)
                    if code == 0:
                        target_pose = recovered_pose.copy()

                    # Skip the rest of the loop to yield control to the backend recovery
                    await asyncio.sleep(0.5)
                    continue

                # 0. DEAD-MAN SWITCH (WATCHDOG)
                if time.time() - last_msg_time > 0.25:
                    for key in current_impulses.keys():
                        current_impulses[key] = 0.0

                # 1. PREDICTIVE BOUNDARY FILTER
                next_x = target_pose[0] + current_impulses["x"] * MM_PER_TICK
                next_y = target_pose[1] + current_impulses["y"] * MM_PER_TICK
                next_z = target_pose[2] + current_impulses["z"] * MM_PER_TICK

                if (next_x > X_MAX and current_impulses["x"] > 0) or (
                    next_x < X_MIN and current_impulses["x"] < 0
                ):
                    current_impulses["x"] = 0.0
                if (next_y > Y_MAX and current_impulses["y"] > 0) or (
                    next_y < Y_MIN and current_impulses["y"] < 0
                ):
                    current_impulses["y"] = 0.0
                if (next_z > Z_MAX and current_impulses["z"] > 0) or (
                    next_z < Z_MIN and current_impulses["z"] < 0
                ):
                    current_impulses["z"] = 0.0

                # Dynamic Spherical Leash
                next_rel_z = next_z - SHOULDER_Z_OFFSET
                radius = math.sqrt(next_x**2 + next_y**2 + next_rel_z**2)
                pitch_rads = math.radians(target_pose[4])
                extension_ratio = abs(math.sin(pitch_rads))
                dynamic_max_reach = MAX_REACH_FOLDED + (
                    (MAX_REACH_EXTENDED - MAX_REACH_FOLDED) * extension_ratio
                )

                if radius > dynamic_max_reach:
                    current_rel_z = target_pose[2] - SHOULDER_Z_OFFSET
                    dot_product = (
                        (target_pose[0] * current_impulses["x"])
                        + (target_pose[1] * current_impulses["y"])
                        + (current_rel_z * current_impulses["z"])
                    )
                    if dot_product > 0:
                        current_impulses["x"] = current_impulses[
                            "y"
                        ] = current_impulses["z"] = 0.0

                # 2. APPLY FILTERED IMPULSES
                target_pose[0] += current_impulses["x"] * MM_PER_TICK
                target_pose[1] += current_impulses["y"] * MM_PER_TICK
                target_pose[2] += current_impulses["z"] * MM_PER_TICK
                target_pose[3] += current_impulses["roll"] * DEG_PER_TICK
                target_pose[4] += current_impulses["pitch"] * DEG_PER_TICK
                target_pose[5] += current_impulses["yaw"] * DEG_PER_TICK

                # 3. HARDWARE COMMAND
                if "robot" in globals() and robot.is_connected():
                    # We no longer poll for errors here; the backend thread handles it.
                    robot.arm.set_servo_cartesian(
                        target_pose, is_tool_coord=False, is_radian=False
                    )

                debug_tick += 1
                if debug_tick >= UPDATE_RATE_HZ:
                    print(
                        f"Streaming Target XYZ: [{target_pose[0]:.1f}, {target_pose[1]:.1f}, {target_pose[2]:.1f}]"
                    )
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
            if "robot" in globals():
                robot.api_set_state(4)

        elif msg_type == "CLEAR_ERROR":
            print("\n[System] Manual error clear triggered. Waking arm safely...")
            if "robot" in globals() and robot.is_connected():
                robot.xarm_clean_error()
                robot.api_set_mode(1)
                robot.api_set_state(0)

                code, current_hardware_pos = robot.api_get_position(is_radian=False)
                if code == 0:
                    target_pose = current_hardware_pos.copy()
                    print(
                        f"[System] Trajectory re-synchronized safely at: {[round(x, 1) for x in target_pose]}"
                    )

            estop_active = False

        elif msg_type == "cartesian_impulse" and not estop_active:
            global last_msg_time
            last_msg_time = time.time()
            for key in current_impulses.keys():
                if key in msg:
                    current_impulses[key] = float(msg[key])

    except Exception as e:
        print(f"Handler processing error: {e}")


# --- Web Server Routes & Lifecycle Hooks ---
app = web.Application()


async def index_handler(request):
    with open("dashboard/index.html", "r") as f:
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


def execute_arch_move(target_pose, speed=50):
    """Safely paths to a coordinate by arching Z upwards to avoid the robot's base/singularity zones."""
    if "robot" not in globals() or not robot.is_connected():
        return

    code, current_pos = robot.api_get_position(is_radian=False)
    if code != 0:
        return

    def move_j(pose):
        # Uses the handler's built-in IK solver which returns the array directly
        ik_sol = robot.api_get_ik_sol(pose)
        if ik_sol is not None:
            return robot.api_set_servo_angle(
                angle=ik_sol, speed=40, is_radian=False, wait=True
            )
        print(f"[ARCH MOVE] IK Failed for pose: {pose}")
        return -1

    xy_dist = math.sqrt(
        (target_pose[0] - current_pos[0]) ** 2 + (target_pose[1] - current_pos[1]) ** 2
    )
    z_dist = abs(target_pose[2] - current_pos[2])

    if xy_dist > 150.0 or z_dist > 150.0:
        traverse_z = max(550.0, current_pos[2], target_pose[2])

        if traverse_z - current_pos[2] > 1.0:
            ret = move_j(
                [
                    current_pos[0],
                    current_pos[1],
                    traverse_z,
                    current_pos[3],
                    current_pos[4],
                    current_pos[5],
                ]
            )
            if ret != 0:
                print(f"[ARCH MOVE] Step 1 Failed: Error {ret}")

        ret = move_j(
            [
                target_pose[0],
                target_pose[1],
                traverse_z,
                target_pose[3],
                target_pose[4],
                target_pose[5],
            ]
        )
        if ret != 0:
            print(f"[ARCH MOVE] Step 2 Failed: Error {ret}")

        ret = move_j(target_pose)
        if ret != 0:
            print(f"[ARCH MOVE] Step 3 Failed: Error {ret}")

    else:
        # Uses the handler's safe set_position API wrapper
        ret = robot.api_set_position(
            x=target_pose[0],
            y=target_pose[1],
            z=target_pose[2],
            roll=target_pose[3],
            pitch=target_pose[4],
            yaw=target_pose[5],
            speed=speed,
            wait=True,
        )
        if ret != 0:
            print(f"[ARCH MOVE] Linear Move Failed: Error {ret}")


async def play_trajectory(traj):
    global is_playing_trajectory, trajectories, target_pose, current_impulses
    if "robot" not in globals() or is_playing_trajectory or not robot.is_connected():
        return

    is_playing_trajectory = True
    filename = trajectories.get(traj)[0]
    start_pose = trajectories.get(traj)[1]

    def blocking_play():
        robot.api_set_mode(0)
        robot.api_set_state(0)
        time.sleep(0.1)

        if robot.get_error_code() != 0:
            robot.xarm_clean_error()
            time.sleep(0.1)

        execute_arch_move(start_pose, speed=50)
        # Handler lacks a trajectory wrapper, so we access the arm directly
        robot.arm.playback_trajectory(times=1, filename=filename, wait=True)
        return robot.api_get_position(is_radian=False)

    code, current_hardware_pos = await asyncio.to_thread(blocking_play)

    if code == 0:
        target_pose = current_hardware_pos.copy()

    for key in current_impulses.keys():
        current_impulses[key] = 0.0

    robot.api_set_mode(1)
    robot.api_set_state(0)
    is_playing_trajectory = False


async def move_robot_discrete(dx=0.0, dy=0.0, dz=0.0, absolute_pose=None):
    global is_playing_trajectory, target_pose, current_impulses
    if "robot" not in globals() or is_playing_trajectory or not robot.is_connected():
        return

    is_playing_trajectory = True

    if absolute_pose:
        new_pose = absolute_pose
    else:
        new_pose = target_pose.copy()
        new_pose[0] = max(X_MIN, min(X_MAX, new_pose[0] + dx))
        new_pose[1] = max(Y_MIN, min(Y_MAX, new_pose[1] + dy))
        new_pose[2] = max(Z_MIN, min(Z_MAX, new_pose[2] + dz))

    def blocking_move():
        robot.api_set_mode(0)
        robot.api_set_state(0)
        time.sleep(0.1)

        if robot.get_error_code() != 0:
            robot.xarm_clean_error()
            time.sleep(0.1)

        execute_arch_move(new_pose, speed=50)
        return robot.api_get_position(is_radian=False)

    code, current_hardware_pos = await asyncio.to_thread(blocking_move)

    if code == 0:
        target_pose = current_hardware_pos.copy()

    for key in current_impulses.keys():
        current_impulses[key] = 0.0

    robot.api_set_mode(1)
    robot.api_set_state(0)
    is_playing_trajectory = False


# --- Integration with voice control ---
async def voice_command_handler(request):
    global estop_active, current_impulses, target_pose, last_msg_time
    try:
        data = await request.json()
        action = data.get("action")

        if action == "ESTOP":
            print("\n*** ESTOP TRIGGERED VIA VOICE ***\n")
            estop_active = True
            if "robot" in globals():
                robot.api_set_state(4)
            return web.json_response({"status": "estop engaged"})

        elif action == "COMMAND":
            transcript = data.get("transcript", "").lower()
            print(f"[VOICE INGEST] Parsing command: '{transcript}'")

            last_msg_time = time.time()

            for key in current_impulses.keys():
                current_impulses[key] = 0.0

            if "wave" in transcript:
                asyncio.create_task(play_trajectory("wave"))
            elif "home" in transcript or "reset" in transcript:
                asyncio.create_task(
                    move_robot_discrete(
                        absolute_pose=[90.0, 0.0, 260.0, 180.0, 0.0, 0.0]
                    )
                )
            elif "forward" in transcript:
                asyncio.create_task(move_robot_discrete(dx=100.0))
            elif "backward" in transcript or "back" in transcript:
                asyncio.create_task(move_robot_discrete(dx=-100.0))
            elif "left" in transcript:
                asyncio.create_task(move_robot_discrete(dy=100.0))
            elif "right" in transcript:
                asyncio.create_task(move_robot_discrete(dy=-100.0))
            elif "up" in transcript:
                asyncio.create_task(move_robot_discrete(dz=100.0))
            elif "down" in transcript:
                asyncio.create_task(move_robot_discrete(dz=-100.0))

            return web.json_response({"status": "command executed"})

    except Exception as e:
        print(f"[VOICE INGEST ERROR] {e}")
        return web.json_response({"error": str(e)}, status=500)


app.router.add_get("/", index_handler)
app.router.add_get("/rtcbot.js", rtcbotjs_handler)
app.router.add_post("/connect", connect_handler)
app.router.add_post("/voice_in", voice_command_handler)
app.router.add_static(
    "/", path=os.path.dirname(os.path.abspath(__file__)), name="static"
)


async def start_background_tasks(app):
    print("[System] Injecting robot control loop into active event loop...")
    app["robot_loop"] = asyncio.create_task(robot_control_loop())


app.on_startup.append(start_background_tasks)

if __name__ == "__main__":
    print("Serving dashboard on http://localhost:8080")
    web.run_app(app, port=8080)
