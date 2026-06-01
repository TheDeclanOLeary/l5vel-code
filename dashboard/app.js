document.addEventListener("DOMContentLoaded", () => {
    const statusText = document.getElementById("connection-status");
    let conn;

    try {
        conn = new rtcbot.RTCConnection();
    } catch (err) {
        statusText.innerText = "WebRTC initialization failed.";
        return;
    }

    // CRITICAL: You must subscribe to generic messages before generating the local 
    // description, or the WebRTC engine will not open a data channel.
    conn.subscribe((msg) => {
        console.log("Data from backend:", msg);
    });

    conn.video.subscribe(function (stream) {
        document.querySelector("video").srcObject = stream;
        statusText.innerText = "Connected & Streaming";
        statusText.style.color = "#198754";
    });

    // --- State & NippleJS Initialization ---
    let driveState = { x: 0, y: 0, yaw: 0 };
    const joints = ['base', 'shoulder', 'elbow', 'wpitch', 'wyaw', 'wroll'];
    let armImpulseState = { base: 0, shoulder: 0, elbow: 0, wpitch: 0, wyaw: 0, wroll: 0 };

    const mainJoystick = nipplejs.create({
        zone: document.getElementById('joystick-zone'),
        mode: 'static', position: { left: '50%', top: '50%' }, color: 'white'
    });

    mainJoystick.on('move', (evt, data) => { if(data.vector){ driveState.x = data.vector.x; driveState.y = data.vector.y; }});
    mainJoystick.on('end', () => { driveState.x = 0; driveState.y = 0; });

    const yawManager = nipplejs.create({
        zone: document.getElementById('nipple-yaw'), mode: 'static', position: { left: '50%', top: '50%' }, color: '#0d6efd', size: 80, lockX: true
    });
    yawManager.on('move', (evt, data) => { if(data.vector){ driveState.yaw = data.vector.x; }});
    yawManager.on('end', () => { driveState.yaw = 0; });

    joints.forEach(joint => {
        const zoneEl = document.getElementById(`nipple-${joint}`);
        if (!zoneEl) return;
        const manager = nipplejs.create({
            zone: zoneEl, mode: 'static', position: { left: '50%', top: '50%' }, color: '#0d6efd', size: 80, lockY: true
        });
        manager.on('move', (evt, data) => { if(data.vector) armImpulseState[joint] = data.vector.y; });
        manager.on('end', () => { armImpulseState[joint] = 0; });
    });

    // --- Utility & Telemetry ---
    document.getElementById('btn-estop').addEventListener('click', () => {
        driveState = { x: 0, y: 0, yaw: 0 };
        joints.forEach(j => armImpulseState[j] = 0);
        conn.put_nowait({ type: "ESTOP", command: "HALT_ALL" });
        stopTelemetryLoop();
        alert("E-STOP TRIGGERED.");
    });

    let telemetryInterval = null;
    function startTelemetryLoop() {
        if (telemetryInterval !== null) return;
        telemetryInterval = setInterval(() => {
            conn.put_nowait({ type: "drive", ...driveState });
            conn.put_nowait({ type: "arm_impulse", ...armImpulseState });
        }, 50);
    }
    function stopTelemetryLoop() { clearInterval(telemetryInterval); telemetryInterval = null; }

// --- 6. Corrected WebRTC Handshake ---
    async function connect() {
        try {
            let offer = await conn.getLocalDescription();
            
            let response = await fetch("/connect", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(offer),
            });
            
            // Get response as raw text first
            let rawText = await response.text();
            
            // Clean any trailing newlines or whitespace that rtcbot/aiortc appends
            let sanitizedText = rawText.trim();
            
            // Parse the fully intact, clean JSON string
            let remoteDesc = JSON.parse(sanitizedText);
            
            // Pass the uncorrupted description to the WebRTC engine
            await conn.setRemoteDescription(remoteDesc);
            
            startTelemetryLoop();
            if (statusText.innerText !== "Connected & Streaming") {
                statusText.innerText = "Data Channel Open";
                statusText.style.color = "#198754";
            }
            console.log("Handshake completed successfully! Connected to robot loop.");
        } catch (e) {
            console.error("WebRTC Handshake failed:", e);
            statusText.innerText = "Connection Failed.";
            statusText.style.color = "#dc3545";
        }
    }

    connect();

});


