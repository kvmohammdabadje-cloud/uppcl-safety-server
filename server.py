from flask import Flask, request, render_template_string, redirect
import random, os, time, sqlite3, requests
import paho.mqtt.client as mqtt

app = Flask(__name__)

# ================= CONFIG =================
MQTT_BROKER = "s871e161.ala.dedicated.gcp.emqxcloud.com"
MQTT_PORT = 1883
MQTT_USER = "UPPCL_SAFETY"
MQTT_PASS = "Lineman@safety123"

OTP_API_KEY = "f830a94b-ed93-11f0-a6b2-0200cd936042"
DB_FILE = "safety.db"

# ================= LINEMEN =================
LINEMEN = {
    "L1": {"name": "KESHAV", "mobile": "919152225848"},
    "L2": {"name": "RAMESH", "mobile": "919520902397"}
}

# ================= MQTT =================
mqtt_client = mqtt.Client()
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt_client.loop_start()

# ================= DATABASE INIT =================
def init_db():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS requests (
        id TEXT PRIMARY KEY,
        feeder TEXT,
        action TEXT,
        reason TEXT,
        lineman_name TEXT,
        lineman_mobile TEXT,
        otp TEXT,
        otp_verified INTEGER,
        created_at REAL
    )
    """)
    con.commit()
    con.close()

init_db()

# ================= HTML =================
SSO_HTML = """
<h2>SSO – Shutdown Request</h2>

<form method="post">
<input type="hidden" name="step" value="send">

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
{% for k,l in linemen.items() %}
<option value="{{k}}">{{l.name}} ({{l.mobile}})</option>
{% endfor %}
</select><br><br>

<b>Reason:</b><br>
<input name="reason" required><br><br>

<button type="submit">Send OTP</button>
</form>

{% if rid %}
<hr>
<b>Request ID:</b> {{rid}} <br>
OTP sent to lineman.

<h3>Enter OTP</h3>
<form method="post">
<input type="hidden" name="step" value="verify">
<input type="hidden" name="rid" value="{{rid}}">

<input name="otp" placeholder="Enter OTP" required>
<button type="submit">Verify OTP</button>
</form>
{% endif %}

<p style="color:{{color}}">{{msg}}</p>
"""

JE_HTML = """
<h2>JE – Approval Panel</h2>

{% for r in rows %}
<hr>
<b>Request ID:</b> {{r[0]}}<br>
Feeder: {{r[1]}}<br>
Action: {{r[2]}}<br>
Lineman: {{r[4]}}<br>
Reason: {{r[3]}}<br>

<form method="post">
<input type="hidden" name="rid" value="{{r[0]}}">
<button name="decision" value="APPROVE">APPROVE</button>
<button name="decision" value="REJECT">REJECT</button>
</form>
{% endfor %}
"""

# ================= ROUTES =================

@app.route("/sso", methods=["GET","POST"])
def sso():
    msg = ""
    color = "red"
    rid = None

    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    if request.method == "POST":
        step = request.form["step"]

        # ---- SEND OTP ----
        if step == "send":
            feeder = request.form["feeder"]
            action = request.form["action"]
            reason = request.form["reason"]
            lineman_key = request.form["lineman"]

            lineman = LINEMEN[lineman_key]
            otp = str(random.randint(100000,999999))
            rid = str(random.randint(1000,9999))

            cur.execute("""
            INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                rid, feeder, action, reason,
                lineman["name"], lineman["mobile"],
                otp, 0, time.time()
            ))
            con.commit()

            url = f"https://2factor.in/API/V1/{OTP_API_KEY}/SMS/{lineman['mobile']}/{otp}"
            requests.get(url)

            msg = "OTP sent successfully."
            color = "green"

        # ---- VERIFY OTP ----
        if step == "verify":
            rid = request.form["rid"]
            otp_entered = request.form["otp"]

            cur.execute("SELECT otp FROM requests WHERE id=?", (rid,))
            row = cur.fetchone()

            if row and row[0] == otp_entered:
                cur.execute("UPDATE requests SET otp_verified=1 WHERE id=?", (rid,))
                con.commit()
                msg = "OTP verified. Waiting for JE approval."
                color = "green"
            else:
                msg = "Invalid OTP."

    con.close()
    return render_template_string(SSO_HTML, linemen=LINEMEN, rid=rid, msg=msg, color=color)

@app.route("/je", methods=["GET","POST"])
def je():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    if request.method == "POST":
        rid = request.form["rid"]
        decision = request.form["decision"]

        if decision == "APPROVE":
            cur.execute("SELECT feeder, action FROM requests WHERE id=?", (rid,))
            r = cur.fetchone()
            topic = f"uppcl/feeder{r[0]}/cmd"
            mqtt_client.publish(topic, r[1])

        cur.execute("DELETE FROM requests WHERE id=?", (rid,))
        con.commit()
        return redirect("/je")

    cur.execute("SELECT * FROM requests WHERE otp_verified=1")
    rows = cur.fetchall()
    con.close()

    return render_template_string(JE_HTML, rows=rows)

@app.route("/")
def home():
    return "UPPCL SAFETY SERVER WITH DATABASE RUNNING"

# ================= RUN =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
