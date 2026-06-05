<script>
    import { onMount } from 'svelte';
    import nipplejs from 'nipplejs';

    // --- State Management ---
    let currentMode = 'ARM'; 
    let statusText = "Connecting...";
    let statusColor = "text-yellow-400 bg-yellow-400/10";
    let videoElement;
    let conn;

    let cartState = { x: 0, y: 0, z: 0, roll: 0, pitch: 0, yaw: 0 };
    let baseState = { x: 0, yaw: 0 };
    let telemetryInterval = null;

    // --- Svelte Action for NippleJS ---
    // This perfectly cleans up the joysticks when switching tabs
    function joystick(node, config) {
        const manager = nipplejs.create({ zone: node, mode: 'static', ...config.options });
        manager.on('move', config.onMove);
        manager.on('end', config.onEnd);
        return {
            destroy() { manager.destroy(); }
        };
    }

    // --- WebRTC Setup ---
    onMount(() => {
        // Assuming rtcbot.js is loaded globally via index.html
        conn = new rtcbot.RTCConnection();

        conn.subscribe((msg) => {
            if (msg && msg.type === "ROBOT_FAULT") {
                alert(`[ROBOT FAULT] ${msg.message}\n\nPlease clear the workspace and reset.`);
                statusText = "FAULTED / STOPPED";
                statusColor = "text-red-500 bg-red-500/10 border-red-500";
            }
        });

        conn.video.subscribe((stream) => {
            if (videoElement) videoElement.srcObject = stream;
            statusText = "Connected & Streaming";
            statusColor = "text-green-500 bg-green-500/10 border-green-500";
        });

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
                console.error(e);
                statusText = "Connection Failed";
                statusColor = "text-red-500 bg-red-500/10";
            }
        }
        connect();
    });

    function startTelemetryLoop() {
        if (telemetryInterval !== null) return;
        telemetryInterval = setInterval(() => {
            if (currentMode === 'ARM') {
                conn.put_nowait({ type: "cartesian_impulse", ...cartState });
            } else if (currentMode === 'BASE') {
                conn.put_nowait({ type: "base_impulse", ...baseState });
            }
        }, 50);
    }

    // --- Hardware Commands ---
    function triggerEStop() {
        cartState = { x: 0, y: 0, z: 0, roll: 0, pitch: 0, yaw: 0 };
        baseState = { x: 0, yaw: 0 };
        conn.put_nowait({ type: "ESTOP" });
        statusText = "E-STOP ENGAGED";
        statusColor = "text-red-500 bg-red-500/10 border-red-500";
    }

    function clearError() {
        conn.put_nowait({ type: "CLEAR_ERROR" });
        statusText = "Connected & Streaming";
        statusColor = "text-green-500 bg-green-500/10 border-green-500";
    }

    function triggerPreset(action) {
        fetch("/voice_in", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "COMMAND", transcript: action })
        }).catch(err => console.error("Preset failed", err));
    }
</script>

<main class="min-h-screen bg-gray-900 text-gray-100 p-6 font-sans flex flex-col">
    <header class="flex justify-between items-center mb-6 px-4">
        <h1 class="text-2xl font-bold tracking-widest text-gray-400">ROBOT CONTROL CENTER</h1>
        <div class={`px-4 py-2 rounded-full border font-semibold ${statusColor}`}>
            {statusText}
        </div>
    </header>

    <div class="flex-1 grid grid-cols-1 lg:grid-cols-12 gap-6 min-h-0">
        
        <aside class="col-span-3 bg-gray-800 rounded-xl p-6 flex flex-col gap-8 overflow-y-auto">
            <section>
                <h2 class="text-sm uppercase tracking-wider text-gray-400 mb-4">Control Mode</h2>
                <div class="flex bg-gray-900 rounded-lg p-1">
                    <button 
                        class={`flex-1 py-3 rounded-md font-semibold transition-colors ${currentMode === 'ARM' ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'}`}
                        on:click={() => currentMode = 'ARM'}>
                        Arm Control
                    </button>
                    <button 
                        class={`flex-1 py-3 rounded-md font-semibold transition-colors ${currentMode === 'BASE' ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'}`}
                        on:click={() => currentMode = 'BASE'}>
                        Base Drive
                    </button>
                </div>
            </section>

            <section>
                <h2 class="text-sm uppercase tracking-wider text-gray-400 mb-4">Preset Actions</h2>
                <div class="grid grid-cols-2 gap-3">
                    <button class="bg-gray-700 hover:bg-gray-600 hover:border-blue-500 border border-transparent py-3 rounded-lg font-medium transition-all" on:click={() => triggerPreset('home')}>Home / Reset</button>
                    <button class="bg-gray-700 hover:bg-gray-600 hover:border-blue-500 border border-transparent py-3 rounded-lg font-medium transition-all" on:click={() => triggerPreset('wave')}>Wave Hello</button>
                    <button class="bg-gray-700 hover:bg-gray-600 hover:border-blue-500 border border-transparent py-3 rounded-lg font-medium transition-all" on:click={() => triggerPreset('look down')}>Look Down</button>
                    <button class="bg-gray-700 hover:bg-gray-600 hover:border-blue-500 border border-transparent py-3 rounded-lg font-medium transition-all" on:click={() => triggerPreset('stow')}>Stow Arm</button>
                </div>
            </section>
        </aside>

        <section class="col-span-6 bg-gray-800 rounded-xl flex flex-col overflow-hidden border border-gray-700">
            <div class="relative flex-1 bg-black flex items-center justify-center">
                <video bind:this={videoElement} autoplay playsinline class="w-full h-full object-cover"></video>
                <div class="absolute w-10 h-10 border-2 border-white/30 rounded-full pointer-events-none flex items-center justify-center">
                    <div class="w-1 h-1 bg-white/50 rounded-full"></div>
                </div>
            </div>
            
            <div class="p-6 flex gap-4 bg-gray-800">
                <button class="flex-2 w-2/3 bg-red-600 hover:bg-red-500 text-white py-4 rounded-lg font-bold text-xl tracking-wider shadow-[0_0_15px_rgba(220,38,38,0.5)] transition-all active:scale-95" on:click={triggerEStop}>
                    EMERGENCY STOP
                </button>
                <button class="flex-1 w-1/3 bg-yellow-500 hover:bg-yellow-400 text-black py-4 rounded-lg font-bold text-lg transition-all active:scale-95" on:click={clearError}>
                    Clear Fault
                </button>
            </div>
        </section>

        <aside class="col-span-3 bg-gray-800 rounded-xl p-6 overflow-y-auto">
            
            {#if currentMode === 'ARM'}
                <div class="flex flex-col gap-8 animate-fade-in">
                    <div>
                        <h2 class="text-sm uppercase tracking-wider text-gray-400 mb-4">Translation (X, Y)</h2>
                        <div class="w-full h-48 bg-gray-900 rounded-xl border border-gray-700 relative"
                             use:joystick={{
                                 options: { position: { left: '50%', top: '50%' }, color: '#3b82f6', size: 140 },
                                 onMove: ({ data }) => { cartState.x = (data.distance/70) * (data.vector.y||0); cartState.y = (data.distance/70) * (data.vector.x||0); },
                                 onEnd: () => { cartState.x = 0; cartState.y = 0; }
                             }}>
                        </div>
                    </div>

                    <div>
                        <h2 class="text-sm uppercase tracking-wider text-gray-400 mb-4">Z-Height</h2>
                        <div class="w-full h-14 bg-gray-900 rounded-full border border-gray-700 relative"
                             use:joystick={{
                                 options: { position: { left: '50%', top: '28px' }, color: '#10b981', size: 100, lockX: true },
                                 onMove: ({ data }) => { cartState.z = (data.distance/50) * (data.vector.x||0); },
                                 onEnd: () => { cartState.z = 0; }
                             }}>
                        </div>
                        <div class="text-center mt-2 font-mono text-emerald-500">{cartState.z.toFixed(2)}</div>
                    </div>

                    <div>
                        <h2 class="text-sm uppercase tracking-wider text-gray-400 mb-4">Head Orientation</h2>
                        <div class="flex justify-around">
                            <div class="flex flex-col items-center gap-2">
                                <span class="text-xs text-gray-500 uppercase">Pitch</span>
                                <div class="w-14 h-40 bg-gray-900 rounded-full border border-gray-700 relative"
                                     use:joystick={{
                                         options: { position: { left: '27px', top: '50%' }, color: '#f59e0b', size: 100, lockY: true },
                                         onMove: ({ data }) => { cartState.pitch = (data.distance/50) * (data.vector.y||0); },
                                         onEnd: () => { cartState.pitch = 0; }
                                     }}>
                                </div>
                                <span class="font-mono text-amber-500">{cartState.pitch.toFixed(2)}</span>
                            </div>
                            <div class="flex flex-col items-center gap-2">
                                <span class="text-xs text-gray-500 uppercase">Roll</span>
                                <div class="w-14 h-40 bg-gray-900 rounded-full border border-gray-700 relative"
                                     use:joystick={{
                                         options: { position: { left: '27px', top: '50%' }, color: '#f59e0b', size: 100, lockY: true },
                                         onMove: ({ data }) => { cartState.roll = (data.distance/50) * (data.vector.y||0); },
                                         onEnd: () => { cartState.roll = 0; }
                                     }}>
                                </div>
                                <span class="font-mono text-amber-500">{cartState.roll.toFixed(2)}</span>
                            </div>
                        </div>
                    </div>
                </div>
            {/if}

            {#if currentMode === 'BASE'}
                <div class="flex flex-col gap-8 animate-fade-in mt-10">
                    <div>
                        <h2 class="text-sm uppercase tracking-wider text-gray-400 mb-4">Base Drive (Fwd/Rev/Turn)</h2>
                        <div class="w-full h-64 bg-gray-900 rounded-xl border border-gray-700 relative"
                             use:joystick={{
                                 options: { position: { left: '50%', top: '50%' }, color: '#8b5cf6', size: 180 },
                                 onMove: ({ data }) => { baseState.x = (data.distance/90) * (data.vector.y||0); baseState.yaw = (data.distance/90) * (data.vector.x||0); },
                                 onEnd: () => { baseState.x = 0; baseState.yaw = 0; }
                             }}>
                        </div>
                        <p class="text-xs text-gray-500 text-center mt-6 leading-relaxed">
                            Push UP/DOWN to drive forward and reverse.<br>
                            Push LEFT/RIGHT to rotate the chassis.
                        </p>
                    </div>
                </div>
            {/if}

        </aside>
    </div>
</main>

<style>
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(5px); }
        to { opacity: 1; transform: translateY(0); }
    }
    .animate-fade-in {
        animation: fadeIn 0.3s ease-out forwards;
    }
</style>
