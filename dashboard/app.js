document.addEventListener("DOMContentLoaded", () => {
	const statusText = document.getElementById("connection-status");
	let conn;

	try { conn = new rtcbot.RTCConnection(); }
	catch (err) { setStatus("WebRTC Failed.", "danger"); return; }

	function setStatus(text, state) {
		statusText.innerText = text;
		statusText.className = `status-badge status-${state}`;
	}

	// --- WebRTC Incoming Message Listener ---
	conn.subscribe((msg) => {
		if (msg && msg.type === "ROBOT_FAULT") {
			alert(`[ROBOT FAULT] ${msg.message}\n\nPlease verify workspace is clear, let go of joysticks, and click 'Clear Fault'.`);
			setStatus("FAULTED / STOPPED", "danger");
		}
	});

	conn.video.subscribe(function(stream) {
		document.querySelector("video").srcObject = stream;
		setStatus("Connected & Streaming", "success");
	});

	// --- Unified State & Telemetry ---
	let cartState = { x: 0, y: 0, z: 0, roll: 0, pitch: 0, yaw: 0 };
	let baseState = { x: 0, yaw: 0 }; // Forward/Back, Turn L/R
	let currentMode = 'ARM'; // 'ARM' or 'BASE'
	let telemetryInterval = null;

	function startTelemetryLoop() {
		if (telemetryInterval !== null) return;
		telemetryInterval = setInterval(() => {
			if (currentMode === 'ARM') {
				conn.put_nowait({ type: "cartesian_impulse", ...cartState });
			} else if (currentMode === 'BASE') {
				// Ensure your backend handles 'base_impulse' when you implement it
				conn.put_nowait({ type: "base_impulse", ...baseState });
			}
		}, 50);
	}

	// --- Nipple.js Joystick Management ---
	let joysticks = {};

	function initArmJoysticks() {
		// 1. Translation XY
		joysticks.xy = nipplejs.create({ zone: document.getElementById('joystick-xy'), mode: 'static', position: { left: '50%', top: '50%' }, color: '#3b82f6', size: 140 });
		joysticks.xy.on('move', (evt, data) => {
			if (data && data.vector) {
				let mag = data.distance / 70;
				cartState.x = mag * (data.vector.y || 0);
				cartState.y = mag * (data.vector.x || 0);
			}
		}).on('end', () => { cartState.x = 0; cartState.y = 0; });

		// 2. Z-Height
		joysticks.z = nipplejs.create({ zone: document.getElementById('nipple-z'), mode: 'static', position: { left: '50%', top: '25px' }, color: '#10b981', size: 100, lockX: true });
		joysticks.z.on('move', (evt, data) => {
			if (data && data.vector && typeof data.vector.x !== 'undefined') {
				cartState.z = (data.distance / 50) * data.vector.x;
				document.getElementById('val-z').innerText = cartState.z.toFixed(2);
			}
		}).on('end', () => { cartState.z = 0; document.getElementById('val-z').innerText = "0.00"; });

		// 3. Pitch & Roll
		['pitch', 'roll'].forEach(orient => {
			joysticks[orient] = nipplejs.create({ zone: document.getElementById(`nipple-${orient}`), mode: 'static', position: { left: '25px', top: '50%' }, color: '#f59e0b', size: 100, lockY: true });
			joysticks[orient].on('move', (evt, data) => {
				if (data && data.vector && typeof data.vector.y !== 'undefined') {
					cartState[orient] = (data.distance / 50) * data.vector.y;
					document.getElementById(`val-${orient}`).innerText = cartState[orient].toFixed(2);
				}
			}).on('end', () => { cartState[orient] = 0; document.getElementById(`val-${orient}`).innerText = "0.00"; });
		});
	}

	function initBaseJoysticks() {
		joysticks.base = nipplejs.create({ zone: document.getElementById('joystick-base'), mode: 'static', position: { left: '50%', top: '50%' }, color: '#8b5cf6', size: 180 });
		joysticks.base.on('move', (evt, data) => {
			if (data && data.vector) {
				let mag = data.distance / 90;
				baseState.x = mag * (data.vector.y || 0); // Forward/Reverse
				baseState.yaw = mag * (data.vector.x || 0); // Turn Left/Right
			}
		}).on('end', () => { baseState.x = 0; baseState.yaw = 0; });
	}

	function destroyAllJoysticks() {
		Object.values(joysticks).forEach(manager => manager.destroy());
		joysticks = {};
		// Zero states
		cartState = { x: 0, y: 0, z: 0, roll: 0, pitch: 0, yaw: 0 };
		baseState = { x: 0, yaw: 0 };
		document.querySelectorAll('.telemetry-val').forEach(el => el.innerText = "0.00");
	}

	// --- Mode Switching Logic ---
	document.getElementById('mode-arm').addEventListener('click', (e) => switchMode('ARM', e.target));
	document.getElementById('mode-base').addEventListener('click', (e) => switchMode('BASE', e.target));

	function switchMode(newMode, btnElement) {
		if (currentMode === newMode) return;
		currentMode = newMode;

		// Update UI Tabs
		document.querySelectorAll('.mode-btn').forEach(btn => {
			btn.classList.remove('active');
			btn.setAttribute('aria-pressed', 'false');
		});
		btnElement.classList.add('active');
		btnElement.setAttribute('aria-pressed', 'true');

		// Toggle View Visibility
		document.getElementById('arm-controls').classList.toggle('active-view', newMode === 'ARM');
		document.getElementById('base-controls').classList.toggle('active-view', newMode === 'BASE');

		// Rebuild Joysticks to prevent rendering bugs
		destroyAllJoysticks();
		if (newMode === 'ARM') initArmJoysticks();
		else initBaseJoysticks();
	}

	// Initialize default mode
	initArmJoysticks();

	// --- Presets (Using existing /voice_in endpoint) ---
	document.querySelectorAll('.btn-preset').forEach(btn => {
		btn.addEventListener('click', (e) => {
			const action = e.target.getAttribute('data-action');
			fetch("/voice_in", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ action: "COMMAND", transcript: action })
			}).then(() => console.log(`Triggered preset: ${action}`))
				.catch(err => console.error("Preset failed", err));
		});
	});

	// --- Safety & Utility ---
	document.getElementById('btn-estop').addEventListener('click', () => {
		cartState = { x: 0, y: 0, z: 0, roll: 0, pitch: 0, yaw: 0 };
		baseState = { x: 0, yaw: 0 };
		conn.put_nowait({ type: "ESTOP" });
		setStatus("E-STOP ENGAGED", "danger");
	});

	document.getElementById('btn-reset').addEventListener('click', () => {
		conn.put_nowait({ type: "CLEAR_ERROR" });
		cartState = { x: 0, y: 0, z: 0, roll: 0, pitch: 0, yaw: 0 };
		baseState = { x: 0, yaw: 0 };
		startTelemetryLoop();
		setStatus("Connected & Streaming", "success");
	});

	// --- Connection ---
	async function connect() {
		try {
			let offer = await conn.getLocalDescription();
			let response = await fetch("/connect", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(offer)
			});
			await conn.setRemoteDescription(JSON.parse((await response.text()).trim()));
			startTelemetryLoop();
		} catch (e) {
			console.error("WebRTC Handshake failed:", e);
			setStatus("Connection Failed.", "danger");
		}
	}

	connect();
});
