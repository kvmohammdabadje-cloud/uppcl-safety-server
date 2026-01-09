from flask import Flask, request, render_template_string, redirect
import random
import paho.mqtt.client as mqtt
import os

app = Flask(__name__)

# ================= MQTT CONFIG (EMQX) =================
MQTT_BROKER = "s871e161.ala.dedicated.gcp.emqxcloud.com"
MQTT_PORT = 1883
MQTT_USER = "UPPCL_SAFETY"
MQTT_PASS = "Lineman@safety123"

mqtt_client = mqtt.Client()
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt_client.loop_start()

# ================= IN-MEMORY STORE =================
requests_db = {}

# ================= HTML =================
SSO_HTML = """
<h2>SSO – Shutdown Request</h2>
<form method="post">
Feeder:
<select name="feeder">
<option value="1">Feeder 1</option>
<option value="2">Feeder 2</option>
</select><br><br>

Action:
<select name="action">
<option value="TRIP">TRIP</option>
<option value="CLOSE">CLOSE</option>
</select><br><br>

Reason:<br>
<input name="reason" required><br><br>

<button type="submit">Generate OTP</button>
</form>

{% if otp %}
<hr>
<b>OTP:</b> {{otp}}<br>
<b>Request ID:</b> {{rid}}
{% endif %}
"""

JE_HTML = """
<h2>JE – Approval Panel</h2>

{% for rid, r in db.items() %}
<hr>
<b>Request ID:</b> {{rid}}<br>
Feeder: {{r['feeder']}}<br>
Action: {{r['action']}}<br>
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
    return "UPPCL SAFETY OTP SERVER RUNNING"

@app.route("/sso", methods=["GET", "POST"])
def sso():
    otp = None
    rid = None

    if request.method == "POST":
        feeder = request.form["feeder"]
        action = request.form["action"]
        reason = request.form["reason"]

        otp = str(random.randint(100000, 999999))
        rid = str(random.randint(1000, 9999))

        requests_db[rid] = {
            "feeder": feeder,
            "action": action,
            "reason": reason,
            "otp": otp
        }

    return render_template_string(SSO_HTML, otp=otp, rid=rid)

@app.route("/je", methods=["GET", "POST"])
def je():
    if request.method == "POST":
        rid = request.form["rid"]
        decision = request.form["decision"]

        if rid in requests_db and decision == "APPROVE":
            r = requests_db[rid]
            topic = f"uppcl/feeder{r['feeder']}/cmd"
            mqtt_client.publish(topic, r["action"])
            print("MQTT SENT:", topic, r["action"])

        requests_db.pop(rid, None)
        return redirect("/je")

    return render_template_string(JE_HTML, db=requests_db)

# ================= CLOUD RUN =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
