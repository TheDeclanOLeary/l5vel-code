document.addEventListener("DOMContentLoaded", () => {
    const statusText = document.getElementById("connection-status");
    let conn;

    try {
        conn = new rtcbot.RTCConnection();
    } catch (err) {
        statusText.innerText = "WebRTC initialization failed.";
        return;
    }

    conn.subscribe((msg) => {
        console.log("Data from backend:", msg);
    });

    conn.video.subscribe(function (stream) {
        document.querySelector("video").srcObject = stream;
        statusText.innerText = "Connected & Streaming";
        statusText.style.color = "#198754";
    });

    // --- 2. Drive Controls ---
    let driveState = { x: 0, y: 0, yaw: 0 };

    const mainJoystick = nipplejs.create({
        zone: document.getElementById('joystick-zone'),
        mode: 'static',
        position: { left: '50%', top: '50%' }, 
        color: 'white',
        size: 200 // MATCHES CSS: 200px diameter (100px radius)
    });

    mainJoystick.on('move', (evt, data) => {
        if (data && data.vector) {
            let magnitude = data.distance / 100; // Normalize 0.0 to 1.0 based on radius
            driveState.x = magnitude * (data.vector.x || 0);
            driveState.y = magnitude * (data.vector.y || 0);
        }
    });

    mainJoystick.on('end', () => { driveState.x = 0; driveState.y = 0; });

    const yawManager = nipplejs.create({
        zone: document.getElementById('nipple-yaw'), 
        mode: 'static',
        position: { left: '50%', top: '20px' }, 
        color: '#0d6efd', 
        size: 140, // MATCHES CSS: 140px wide track (70px radius)
        lockX: true // RESTORED: Locks movement TO the X-axis (Horizontal)
    });

    yawManager.on('move', (evt, data) => {
        if (data && data.vector && typeof data.vector.x !== 'undefined') {
            let magnitude = data.distance / 70; 
            driveState.yaw = magnitude * data.vector.x;
            
            const displayVal = document.getElementById('val-yaw');
            if (displayVal) displayVal.innerText = (driveState.yaw > 0 ? "+" : "") + driveState.yaw.toFixed(2);
        }
    });

    yawManager.on('end', () => { 
        driveState.yaw = 0; 
        const displayVal = document.getElementById('val-yaw');
        if (displayVal) displayVal.innerText = "0.00";
    });

    // --- 3. Arm Controls ---
    const joints = ['base', 'shoulder', 'elbow', 'wpitch', 'wyaw', 'wroll'];
    let armImpulseState = { base: 0, shoulder: 0, elbow: 0, wpitch: 0, wyaw: 0, wroll: 0 };

    joints.forEach(joint => {
        const zoneEl = document.getElementById(`nipple-${joint}`);
        if (!zoneEl) return;
        
        const manager = nipplejs.create({
            zone: zoneEl, 
            mode: 'static',
            position: { left: '20px', top: '70px' }, // Dead center of 40x140 container
            color: '#0d6efd', 
            size: 140, // MATCHES CSS: 140px tall track (70px radius)
            lockY: true // RESTORED: Locks movement TO the Y-axis (Vertical)
        });

        manager.on('move', (evt, data) => {
            if (data && data.vector && typeof data.vector.y !== 'undefined') {
                let magnitude = data.distance / 70; 
                let val = magnitude * data.vector.y;
                
                armImpulseState[joint] = val;
                
                const displayVal = document.getElementById(`val-${joint}`);
                if (displayVal) displayVal.innerText = (val > 0 ? "+" : "") + val.toFixed(2);
            }
        });

        manager.on('end', () => { 
            armImpulseState[joint] = 0; 
            const displayVal = document.getElementById(`val-${joint}`);
            if (displayVal) displayVal.innerText = "0.00";
        });
    });

    // --- 4. Utility & Presets ---
    document.getElementById('btn-estop').addEventListener('click', () => {
        driveState = { x: 0, y: 0, yaw: 0 };
        joints.forEach(j => armImpulseState[j] = 0);
        conn.put_nowait({ type: "ESTOP", command: "HALT_ALL" });
        stopTelemetryLoop();
        alert("E-STOP TRIGGERED.");
    });

    // --- 5. Telemetry Transmission Loop ---
    let telemetryInterval = null;
    function startTelemetryLoop() {
        if (telemetryInterval !== null) return;
        
        telemetryInterval = setInterval(() => {
            conn.put_nowait({ type: "drive", ...driveState });
            setTimeout(() => {
                conn.put_nowait({ type: "arm_impulse", ...armImpulseState });
            }, 20);
        }, 50); 
    }
    function stopTelemetryLoop() { clearInterval(telemetryInterval); telemetryInterval = null; }

    // --- 6. WebRTC Handshake ---
    async function connect() {
        try {
            let offer = await conn.getLocalDescription();
            let response = await fetch("/connect", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(offer),
            });
            
            let rawText = await response.text();
            let sanitizedText = rawText.trim();
            let remoteDesc = JSON.parse(sanitizedText);
            await conn.setRemoteDescription(remoteDesc);
            
            startTelemetryLoop();
            if (statusText.innerText !== "Connected & Streaming") {
                statusText.innerText = "Data Channel Open";
                statusText.style.color = "#198754";
            }
        } catch (e) {
            console.error("WebRTC Handshake failed:", e);
            statusText.innerText = "Connection Failed.";
            statusText.style.color = "#dc3545";
        }
    }

    connect();
});
