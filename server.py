from flask import Flask, jsonify, request, render_template_string
import time

app = Flask(__name__)

# Global state
state = {
    "esp32_online": False,
    "last_heartbeat": 0,
    "fall_detected": False,
    "fall_time": "",
    "fall_g_force": 0,
}

HEARTBEAT_TIMEOUT = 15  # seconds - if no heartbeat in 15s, mark offline

# ─── Dashboard HTML ───
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AstraCare — Fall Sentinel</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
    * { margin:0; padding:0; box-sizing:border-box; }
    body {
      font-family: 'Inter', sans-serif;
      background: #0a0e17;
      color: #e2e8f0;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 20px;
      transition: background 0.4s;
    }
    body.emergency {
      background: #1c0505;
      animation: pulse-bg 1s infinite alternate;
    }
    @keyframes pulse-bg {
      from { background: #1c0505; }
      to { background: #2d0a0a; }
    }

    .card {
      background: rgba(15, 23, 42, 0.8);
      border: 1px solid rgba(100, 116, 139, 0.2);
      border-radius: 20px;
      padding: 40px;
      max-width: 460px;
      width: 100%;
      text-align: center;
      backdrop-filter: blur(10px);
    }
    body.emergency .card {
      border-color: #ef4444;
      box-shadow: 0 0 40px rgba(239,68,68,0.3);
    }

    .title {
      font-size: 14px;
      font-weight: 600;
      color: #64748b;
      text-transform: uppercase;
      letter-spacing: 1.5px;
      margin-bottom: 28px;
    }

    /* ESP32 Status */
    .device-row {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 12px;
      margin-bottom: 36px;
      padding: 14px 20px;
      border-radius: 12px;
      background: rgba(0,0,0,0.3);
    }
    .dot {
      width: 12px; height: 12px;
      border-radius: 50%;
      background: #ef4444;
    }
    .dot.online {
      background: #10b981;
      box-shadow: 0 0 12px #10b981;
      animation: blink 2s infinite;
    }
    @keyframes blink {
      0%,100% { opacity:1; }
      50% { opacity:0.4; }
    }
    .device-text {
      font-size: 16px;
      font-weight: 600;
    }
    .device-text.online { color: #10b981; }
    .device-text.offline { color: #ef4444; }

    /* Big Status Orb */
    .orb {
      width: 140px; height: 140px;
      border-radius: 50%;
      margin: 0 auto 24px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 56px;
      border: 3px solid #10b981;
      background: rgba(16,185,129,0.1);
      box-shadow: 0 0 30px rgba(16,185,129,0.2);
      transition: all 0.3s;
    }
    .orb.danger {
      border-color: #ef4444;
      background: rgba(239,68,68,0.15);
      box-shadow: 0 0 40px rgba(239,68,68,0.4);
      animation: shake 0.3s infinite alternate;
    }
    @keyframes shake {
      from { transform: scale(1); }
      to { transform: scale(1.06); }
    }

    .status-text {
      font-size: 22px;
      font-weight: 800;
      margin-bottom: 8px;
    }
    .status-sub {
      font-size: 14px;
      color: #94a3b8;
      margin-bottom: 28px;
      line-height: 1.5;
    }

    .btn-reset {
      display: none;
      padding: 14px 32px;
      border-radius: 12px;
      background: #ef4444;
      color: #fff;
      font-size: 15px;
      font-weight: 700;
      border: none;
      cursor: pointer;
      box-shadow: 0 4px 20px rgba(239,68,68,0.4);
    }
    body.emergency .btn-reset { display: inline-block; }
    .btn-reset:hover { background: #dc2626; }

    .footer {
      margin-top: 20px;
      font-size: 11px;
      color: #475569;
    }
  </style>
</head>
<body id="body">
  <div class="card">
    <div class="title">AstraCare Fall Sentinel</div>

    <div class="device-row">
      <div class="dot" id="dot"></div>
      <div class="device-text" id="deviceText">ESP32 Watch: Checking...</div>
    </div>

    <div class="orb" id="orb">💚</div>
    <div class="status-text" id="statusText">Patient Safe</div>
    <div class="status-sub" id="statusSub">Monitoring active. No fall detected.</div>

    <button class="btn-reset" id="btnReset" onclick="resetAlert()">
      ✅ Acknowledge & Clear Alert
    </button>

    <div class="footer">Created by Ameen — AstraCare Sentinel Pro</div>
  </div>

  <script>
    let audioCtx = null;
    let sirenInterval = null;
    let currentlyAlert = false;

    function startSiren() {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (sirenInterval) return;
      let high = false;
      sirenInterval = setInterval(() => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(high ? 960 : 700, audioCtx.currentTime);
        gain.gain.setValueAtTime(0.4, audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.35);
        osc.connect(gain); gain.connect(audioCtx.destination);
        osc.start(); osc.stop(audioCtx.currentTime + 0.35);
        high = !high;
      }, 380);
    }

    function stopSiren() {
      if (sirenInterval) { clearInterval(sirenInterval); sirenInterval = null; }
    }

    async function poll() {
      try {
        const res = await fetch('/api/status');
        const d = await res.json();

        // ESP32 online/offline
        const dot = document.getElementById('dot');
        const dt = document.getElementById('deviceText');
        if (d.esp32_online) {
          dot.className = 'dot online';
          dt.className = 'device-text online';
          dt.textContent = 'ESP32 Watch: Online';
        } else {
          dot.className = 'dot';
          dt.className = 'device-text offline';
          dt.textContent = 'ESP32 Watch: Offline';
        }

        // Fall alert
        if (d.fall_detected && !currentlyAlert) {
          currentlyAlert = true;
          document.body.classList.add('emergency');
          document.getElementById('orb').className = 'orb danger';
          document.getElementById('orb').textContent = '🚨';
          document.getElementById('statusText').textContent = 'FALL DETECTED!';
          document.getElementById('statusSub').textContent =
            '⚠️ Impact: ' + d.fall_g_force + ' G — ' + d.fall_time;
          startSiren();
        }

        if (!d.fall_detected && currentlyAlert) {
          currentlyAlert = false;
          document.body.classList.remove('emergency');
          document.getElementById('orb').className = 'orb';
          document.getElementById('orb').textContent = '💚';
          document.getElementById('statusText').textContent = 'Patient Safe';
          document.getElementById('statusSub').textContent = 'Monitoring active. No fall detected.';
          stopSiren();
        }
      } catch(e) {}
    }

    async function resetAlert() {
      await fetch('/api/reset', { method: 'POST' });
      currentlyAlert = false;
      document.body.classList.remove('emergency');
      document.getElementById('orb').className = 'orb';
      document.getElementById('orb').textContent = '💚';
      document.getElementById('statusText').textContent = 'Patient Safe';
      document.getElementById('statusSub').textContent = 'Alert cleared by caregiver.';
      stopSiren();
    }

    setInterval(poll, 2000);
    poll();
  </script>
</body>
</html>
"""

# ─── Routes ───

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)

@app.route("/api/status")
def api_status():
    # Check if ESP32 is still alive (heartbeat within last 15 sec)
    now = time.time()
    if now - state["last_heartbeat"] > HEARTBEAT_TIMEOUT:
        state["esp32_online"] = False
    return jsonify(state)

@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    state["esp32_online"] = True
    state["last_heartbeat"] = time.time()
    return jsonify({"ok": True})

@app.route("/api/fall", methods=["POST"])
def api_fall():
    data = request.get_json(silent=True) or {}
    state["fall_detected"] = True
    state["fall_time"] = data.get("time", time.strftime("%H:%M:%S"))
    state["fall_g_force"] = data.get("g_force", 3.5)
    state["esp32_online"] = True
    state["last_heartbeat"] = time.time()
    return jsonify({"ok": True, "alert": "triggered"})

@app.route("/api/reset", methods=["POST"])
def api_reset():
    state["fall_detected"] = False
    state["fall_time"] = ""
    state["fall_g_force"] = 0
    return jsonify({"ok": True, "alert": "cleared"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
