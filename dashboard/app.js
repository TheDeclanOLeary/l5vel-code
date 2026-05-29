const conn = new rtcbot.RTCConnection();
const statusText = document.getElementById("connection-status");

// --- 1. Video Handling ---
conn.video.subscribe(function (stream) {
    document.querySelector("video").srcObject = stream;
    statusText.innerText = "Connected & Streaming";
    statusText.style.color = "#198754";
});

// --- 2. Drive Controls (X/Y Joystick & Yaw Fader) ---
let driveState = { x: 0, y: 0, yaw: 0 };

const mainJoystick = nipplejs.create({
    zone: document.getElementById('joystick-zone'),
    mode: 'static',
    position: { left: '50%', top: '50%' },
    color: 'white'
});

mainJoystick.on('move', function (evt, data) {
    if (!data.vector) return;
    driveState.x = data.vector.x;
    driveState.y = data.vector.y;
});

mainJoystick.on('end', function () {
    driveState.x = 0;
    driveState.y = 0;
});

const yawManager = nipplejs.create({
    zone: document.getElementById('nipple-yaw'),
    mode: 'static',
    position: { left: '50%', top: '50%' },
    color: '#0d6efd',
    size: 80,
    lockX: true
});

yawManager.on('move', (evt, data) => {
    if (!data.vector) return;
    driveState.yaw = data.vector.x;

    const displayVal = document.getElementById('val-yaw');
    displayVal.innerText = (driveState.yaw > 0 ? "+" : "") + driveState.yaw.toFixed(2);
});

yawManager.on('end', () => {
    driveState.yaw = 0;
    document.getElementById('val-yaw').innerText = "0.00";
});

// --- 3. Arm Controls (1D Vertical Faders) ---
const joints = ['base', 'shoulder', 'elbow', 'wpitch', 'wroll'];
let armImpulseState = { base: 0, shoulder: 0, elbow: 0, wpitch: 0, wroll: 0 };

joints.forEach(joint => {
    const manager = nipplejs.create({
        zone: document.getElementById(`nipple-${joint}`),
        mode: 'static',
        position: { left: '50%', top: '50%' },
        color: '#0d6efd',
        size: 80,
        lockY: true
    });

    manager.on('move', (evt, data) => {
        if (!data.vector) return;

        let val = data.vector.y;
        armImpulseState[joint] = val;

        const displayVal = document.getElementById(`val-${joint}`);
        displayVal.innerText = (val > 0 ? "+" : "") + val.toFixed(2);
    });

    manager.on('end', () => {
        armImpulseState[joint] = 0;
        document.getElementById(`val-${joint}`).innerText = "0.00";
    });
});

// --- 4. Utility & Presets ---
document.getElementById('btn-home').addEventListener('click', () => {
    conn.put_nowait({ type: "arm_preset", target: "home" });
});

document.getElementById('btn-stow').addEventListener('click', () => {
    conn.put_nowait({ type: "arm_preset", target: "stow" });
});

document.getElementById('btn-estop').addEventListener('click', () => {
    // Zero all state immediately before sending the halt command
    driveState = { x: 0, y: 0, yaw: 0 };
    joints.forEach(j => armImpulseState[j] = 0);

    conn.put_nowait({ type: "ESTOP", command: "HALT_ALL" });
    stopTelemetryLoop();
    alert("E-STOP TRIGGERED. Requires manual backend reset.");
});

// --- 5. Telemetry Transmission Loop (20Hz) ---
let telemetryInterval = null;

function startTelemetryLoop() {
    if (telemetryInterval !== null) return; // already running
    telemetryInterval = setInterval(() => {
        // Send drive and arm state unconditionally every tick so the
        // backend always has current status, including when both are at zero.
        conn.put_nowait({ type: "drive", ...driveState });
        conn.put_nowait({ type: "arm_impulse", ...armImpulseState });
    }, 50);
}

function stopTelemetryLoop() {
    clearInterval(telemetryInterval);
    telemetryInterval = null;
}

// --- 6. WebRTC Initialization ---
async function connect() {
    try {
        let offer = await conn.getLocalDescription();
        let response = await fetch("/connect", {
            method: "POST",
            cache: "no-cache",
            body: JSON.stringify(offer),
        });
        await conn.setRemoteDescription(await response.json());
        // Only start sending telemetry once the connection is established
        startTelemetryLoop();
    } catch (e) {
        statusText.innerText = "Connection Failed.";
        statusText.style.color = "#dc3545";
    }
}

connect();
