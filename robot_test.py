import time

# Import the handler from your xarm_code.py file
from xarm_code import XArmHandler

# Replace with your Lite 6's actual IP address
ROBOT_IP = "172.16.0.10"


def main():
    # 1. Instantiate the Handler
    # Set gripper to None unless you have the specific Lite 6 gripper attached
    robot = XArmHandler(robot_ip=ROBOT_IP, gripper=None, print_tag="Lite6_Controller")

    try:
        print("Connecting to the robot...")

        # 2. Initialize the Arm
        # This clears errors, enables motion, and moves to the safe/init pose
        robot.xarm_init()

        if not robot.is_startup_done():
            print("Failed to initialize the robot. Exiting.")
            return

        # Define the 3D bounding box for the obstacle
        # [x, y, z] in millimeters relative to the robot's base
        box_min = [0.0, 100.0, 0.0]
        box_max = [-500.0, 500.0, 1000.0]

        # Add the region. This automatically starts the background monitoring thread.
        robot.add_avoidance_region(box_min, box_max)

        # To verify, you can print the active regions to the console
        robot.list_avoidance_regions()

        print("Robot initialized successfully!")

        # ---------------------------------------------------------
        # 3. Execution Logic (Call your API functions here)
        # ---------------------------------------------------------

        # Example A: Get the current Cartesian position
        code, current_pos = robot.api_get_position(is_radian=False)
        if code == 0:
            print(f"Current Position (x, y, z, roll, pitch, yaw): {current_pos}")

        # Example B: Move using Cartesian coordinates (api_set_position)
        # NOTE: These coordinates are scaled down for the Lite 6's smaller reach.
        print("Moving to target Cartesian position...")
        target_x = 150.0
        target_y = 100.0
        target_z = 450.0
        # Wait=True ensures the script pauses until the motion is complete
        code = robot.api_set_position(
            x=target_x, y=target_y, z=target_z, wait=True, speed=20
        )

        if code == 0:
            print("Cartesian move complete.")
        else:
            print(f"Move failed with error code: {code}")

        time.sleep(1)  # Brief pause between commands

        # Example C: Move using Joint angles (api_set_servo_angle)
        print("Moving to target Joint angles...")
        # A standard "look forward" pose for a 6-DOF arm
        target_joints = [-90.0, 0.0, 90.0, 0.0, 00.0, 90.0]
        code = robot.api_set_servo_angle(
            angle=target_joints, is_radian=False, wait=True
        )

        if code == 0:
            print("Joint move complete.")
        else:
            print(f"Move failed with error code: {code}")

    except KeyboardInterrupt:
        # Catches Ctrl+C cleanly
        print("\nProcess interrupted by user. Stopping robot...")
        robot.api_set_state(4)  # State 4 is Emergency Stop

    except Exception as e:
        # Catches standard Python errors
        print(f"\nAn error occurred: {e}")
        robot.api_set_state(4)

    finally:
        # 4. Graceful Shutdown
        # This block ALWAYS runs, ensuring threads are killed and the socket is closed.
        print("Disconnecting from robot...")
        robot.disconnect()
        print("Shutdown complete.")


if __name__ == "__main__":
    main()
