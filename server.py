from flask import Flask, request, render_template_string, redirect
import random, os, time, requests
import paho.mqtt.client as mqtt

app = Flask(__name__)

# ================= MQTT CONFIG (EMQX) =================
MQTT_BROKER = "s871e161.ala.dedicated.gcp.emqxcloud.com"
MQTT_PORT = 1883
MQTT_USER = "UPPCL_SAFETY"
MQTT_PASS = "Lineman@safety123"

# ================= 2FACTOR CONFIG =================
OTP_API_KEY = "f830a94b-ed93-11f0-a6b2-0200cd936042"

# ================= LINEMAN DATABASE =================
# Add more linemen easily here
LINEMEN = {
    "L1": {"name": "KESHAV", "mobile": "919152225848"},
    "L2": {"name": "ANIL", "mobile": "919520902397"}
}

# ================= MQTT CLIENT =================
mqtt_client = mqtt.Client()
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt_client.loop_start()

# ================= IN-MEMORY STORE =================
# rid -> request data
requests_db = {}

# ================= HTML TEMPLATES =================

SSO_HTML = """
<h2>SSO – Shutdown Request</h2>
<form method="post">
<b>Feeder:</b>
<select name="feeder">
<option value="1">Feeder 1</option>
<option value="2">Feeder 2</option>
</select><br><br>

<b>Action:</b>
<select name="action">
<option value="TRIP">TRIP</option>
<option value="CLOSE">CLOSE</option>
</select><br><br>

<b>Lineman:</b>
<select name="lineman">
{% for key, l in linemen.items() %}
<option value="{{key}}">{{l['name']}} ({{l['mobile']}})</option>
{% endfor %}
</select><br><br>

<b>Reason:</b><br>
<input name="reason" required><br><br>

<button type="submit">Send OTP</button>
</form>

{% if rid %}
<hr>
<b>Request ID:</b> {{rid}}<br>
OTP sent to selected lineman.
{% endif %}
"""

VERIFY_HTML = """
<h2>OTP Verification (SSO)</h2>
<form method="post">
<b>Request ID:</b><br>
<input name="rid" required><br><br>

<b>Enter OTP:</b><br>
<input name="otp" required><br><br>

<button type="submit">Verify OTP</button>
</form>

<p style="color:{{color}}">{{msg}}</p>
"""

JE_HTML = """
<h2>JE – Approval Panel</h2>

{% for rid, r in db.items() if r['otp_verified'] %}
<hr>
<b>Request ID:</b> {{rid}}<br>
Feeder: {{r['feeder']}}<br>
Action: {{r['action']}}<br>
Lineman: {{r['lineman_name']}}<br>
Reason: {{r['reason']}}<br>

<form method="post">
<input type="hidden" name="rid" value="{{rid}}">
<button name="decision" value="APPROVE">APPROVE</button>
<button name="decision" value="REJECT">REJECT</button>
</form>
{% endfor %}
"""

# ================= ROUTES =================

@app.route("/")
def home():
    return "UPPCL MULTI-LINEMAN OTP SAFETY SERVER LIVE"

@app.route("/sso", methods=["GET","POST"])
def sso():
    rid = None
    if request.method == "POST":
        feeder = request.form["feeder"]
        action = request.form["action"]
        lineman_key = request.form["lineman"]
        reason = request.form["reason"]

        lineman = LINEMEN[lineman_key]
        otp = str(random.randint(100000, 999999))
        rid = str(random.randint(1000, 9999))

        requests_db[rid] = {
            "feeder": feeder,
            "action": action,
            "reason": reason,
            "otp": otp,
            "otp_verified": False,
            "lineman_name": lineman["name"],
            "created_at": time.time()
        }

        # ---- SEND OTP via 2Factor ----
        url = f"https://2factor.in/API/V1/{OTP_API_KEY}/SMS/{lineman['mobile']}/{otp}"
        requests.get(url)

    return render_template_string(SSO_HTML, rid=rid, linemen=LINEMEN)

@app.route("/verify", methods=["GET","POST"])
def verify():
    msg = ""
    color = "red"

    if request.method == "POST":
        rid = request.form["rid"]
        otp = request.form["otp"]

        if rid in requests_db and requests_db[rid]["otp"] == otp:
            requests_db[rid]["otp_verified"] = True
            msg = "OTP verified successfully. JE can approve now."
            color = "green"
        else:
            msg = "Invalid OTP. Please retry."

    return render_template_string(VERIFY_HTML, msg=msg, color=color)

@app.route("/je", methods=["GET","POST"])
def je():
    if request.method == "POST":
        rid = request.form["rid"]
        decision = request.form["decision"]

        if rid in requests_db and decision == "APPROVE":
            r = requests_db[rid]
            topic = f"uppcl/feeder{r['feeder']}/cmd"
            mqtt_client.publish(topic, r["action"])
            print("MQTT SENT:", topic, r["action"])

        # Remove request after decision
        requests_db.pop(rid, None)
        return redirect("/je")

    return render_template_string(JE_HTML, db=requests_db)

# ================= RUN (CLOUD SAFE) =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

