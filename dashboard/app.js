document.addEventListener("DOMContentLoaded", () => {
    const statusText = document.getElementById("connection-status");
    let conn;

    try { conn = new rtcbot.RTCConnection(); } 
    catch (err) { statusText.innerText = "WebRTC Failed."; return; }

    conn.subscribe((msg) => { console.log(msg); });
    conn.video.subscribe(function (stream) {
        document.querySelector("video").srcObject = stream;
        statusText.innerText = "Connected & Streaming";
    });

    // --- Unified Cartesian State ---
    let cartState = { x: 0, y: 0, z: 0, roll: 0, pitch: 0, yaw: 0 };

    // 1. XY Joystick (Translation)
    const xyJoy = nipplejs.create({
        zone: document.getElementById('joystick-zone'), mode: 'static', position: { left: '50%', top: '50%' }, color: 'white', size: 200
    });
    xyJoy.on('move', (evt, data) => {
        if (data && data.vector) {
            let mag = data.distance / 100; 
            cartState.x = mag * (data.vector.y || 0); // Pushing UP (+y on joystick) maps to +X Forward on robot base
            cartState.y = mag * (data.vector.x || 0); // Pushing RIGHT (+x on joystick) maps to -Y Right on robot base (adjust signs if inverted)
        }
    });
    xyJoy.on('end', () => { cartState.x = 0; cartState.y = 0; });

    // 2. Z-Height Slider
    const zJoy = nipplejs.create({
        zone: document.getElementById('nipple-z'), mode: 'static', position: { left: '50%', top: '20px' }, color: '#0d6efd', size: 140, lockX: true
    });
    zJoy.on('move', (evt, data) => {
        if (data && data.vector && typeof data.vector.x !== 'undefined') {
            cartState.z = (data.distance / 70) * data.vector.x;
            document.getElementById('val-z').innerText = cartState.z.toFixed(2);
        }
    });
    zJoy.on('end', () => { cartState.z = 0; document.getElementById('val-z').innerText = "0.00"; });

    // 3. Head Pitch & Roll (Vertical Faders)
    ['pitch', 'roll'].forEach(orient => {
        const joy = nipplejs.create({
            zone: document.getElementById(`nipple-${orient}`), mode: 'static', position: { left: '20px', top: '70px' }, color: '#0d6efd', size: 140, lockY: true
        });
        joy.on('move', (evt, data) => {
            if (data && data.vector && typeof data.vector.y !== 'undefined') {
                cartState[orient] = (data.distance / 70) * data.vector.y;
                document.getElementById(`val-${orient}`).innerText = cartState[orient].toFixed(2);
            }
        });
        joy.on('end', () => { cartState[orient] = 0; document.getElementById(`val-${orient}`).innerText = "0.00"; });
    });

    // 4. Utility & Telemetry
    document.getElementById('btn-estop').addEventListener('click', () => {
        cartState = { x: 0, y: 0, z: 0, roll: 0, pitch: 0, yaw: 0 };
        conn.put_nowait({ type: "ESTOP" });
        clearInterval(telemetryInterval);
        alert("E-STOP TRIGGERED.");
    });
	// Add this right below your ESTOP event listener
document.getElementById('btn-reset').addEventListener('click', () => {
    // Send the manual reset command to the backend
    conn.put_nowait({ type: "RESET_ERROR" });
    
    // Ensure the joysticks are logically zeroed out on the frontend
    cartState = { x: 0, y: 0, z: 0, roll: 0, pitch: 0, yaw: 0 };
    
    // Restart the telemetry loop if it was killed by an ESTOP
    startTelemetryLoop();
    
    alert("Clear Error command sent. Ensure the workspace is clear.");
});

    let telemetryInterval = null;
    function startTelemetryLoop() {
        if (telemetryInterval !== null) return;
        telemetryInterval = setInterval(() => {
            // Send the unified Cartesian object
            conn.put_nowait({ type: "cartesian_impulse", ...cartState });
        }, 50); 
    }

// --- 5. Connection ---
    async function connect() {
        try {
            let offer = await conn.getLocalDescription();
            
            let response = await fetch("/connect", { 
                method: "POST", 
                headers: { "Content-Type": "application/json" }, 
                body: JSON.stringify(offer) 
            });
            
            await conn.setRemoteDescription(JSON.parse((await response.text()).trim()));
            
            // Start pumping data
            startTelemetryLoop();
            
            // RESTORED: Update the UI to reflect the successful network state
            if (statusText.innerText !== "Connected & Streaming") {
                statusText.innerText = "Data Channel Open";
                statusText.style.color = "#198754"; // Green
            }
            console.log("Handshake completed successfully! Connected to robot loop.");
            
        } catch (e) {
            console.error("WebRTC Handshake failed:", e);
            // RESTORED: Update the UI to reflect a true network failure
            statusText.innerText = "Connection Failed.";
            statusText.style.color = "#dc3545"; // Red
        }
    }
    
    connect();
});
