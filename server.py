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

# ================= DATABASE =================
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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT,
        role TEXT,
        action TEXT,
        details TEXT,
        timestamp REAL
    )
    """)

    con.commit()
    con.close()

init_db()

# ================= BASE UI =================
BASE_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>UPPCL Safety System</title>
<style>
body{font-family:Arial;background:#f4f6f8;margin:0}
.header{background:#003366;color:white;padding:15px;text-align:center;font-size:22px}
.container{width:80%;margin:20px auto;background:white;padding:20px;border-radius:6px}
h2{color:#003366}
label{font-weight:bold}
select,input{width:100%;padding:8px;margin-bottom:12px}
button{background:#003366;color:white;padding:10px;border:none;border-radius:4px}
.card{border:1px solid #ccc;padding:10px;border-radius:5px;margin-bottom:10px}
.ok{color:green;font-weight:bold}
.err{color:red;font-weight:bold}
.footer{text-align:center;font-size:12px;color:#777;margin:10px}
</style>
</head>
<body>
<div class="header">UPPCL OTP Based Shutdown Safety System</div>
<div class="container">
{{ content | safe }}
</div>
<div class="footer">Academic & Safety Demonstration</div>
</body>
</html>
"""

# ================= UI =================
SSO_HTML = """
<h2>SSO – Shutdown Request & OTP Verification</h2>

<form method="post">
<input type="hidden" name="step" value="send">

<label>Feeder</label>
<select name="feeder">
<option value="1">Feeder 1</option>
<option value="2">Feeder 2</option>
</select>

<label>Action</label>
<select name="action">
<option value="TRIP">TRIP</option>
<option value="CLOSE">CLOSE</option>
</select>

<label>Lineman</label>
<select name="lineman">
{% for k,l in linemen.items() %}
<option value="{{k}}">{{l.name}} ({{l.mobile}})</option>
{% endfor %}
</select>

<label>Reason</label>
<input name="reason" required>

<button type="submit">Send OTP</button>
</form>

{% if rid %}
<hr>
<div class="card">
<b>Request ID:</b> {{rid}}

<form method="post">
<input type="hidden" name="step" value="verify">
<input type="hidden" name="rid" value="{{rid}}">
<label>Enter OTP</label>
<input name="otp" required>
<button type="submit">Verify OTP</button>
</form>
</div>
{% endif %}

<p class="{{cls}}">{{msg}}</p>
"""

JE_HTML = """
<h2>JE – Approval Panel</h2>

{% for r in rows %}
<div class="card">
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
</div>
{% endfor %}
"""

AUDIT_HTML = """
<h2>Audit Log</h2>
{% for r in rows %}
<div class="card">
<b>Request ID:</b> {{r[1]}}<br>
<b>Role:</b> {{r[2]}}<br>
<b>Action:</b> {{r[3]}}<br>
<b>Details:</b> {{r[4]}}<br>
<b>Time:</b> {{r[5] | int}}
</div>
{% endfor %}
"""

# ================= ROUTES =================
@app.route("/sso", methods=["GET","POST"])
def sso():
    msg=""; cls="err"; rid=None
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        step=request.form["step"]

        if step=="send":
            feeder=request.form["feeder"]
            action=request.form["action"]
            reason=request.form["reason"]
            lineman=LINEMEN[request.form["lineman"]]

            otp=str(random.randint(100000,999999))
            rid=str(random.randint(1000,9999))

            cur.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?)",
                (rid,feeder,action,reason,lineman["name"],lineman["mobile"],otp,0,time.time()))

            cur.execute("INSERT INTO audit_log VALUES (NULL,?,?,?,?,?)",
                (rid,"SSO","OTP_SENT",f"OTP sent to {lineman['name']}",time.time()))

            con.commit()
            requests.get(f"https://2factor.in/API/V1/{OTP_API_KEY}/SMS/{lineman['mobile']}/{otp}")
            msg="OTP sent successfully"; cls="ok"

        if step=="verify":
            rid=request.form["rid"]; otp=request.form["otp"]
            cur.execute("SELECT otp FROM requests WHERE id=?", (rid,))
            row=cur.fetchone()
            if row and row[0]==otp:
                cur.execute("UPDATE requests SET otp_verified=1 WHERE id=?", (rid,))
                cur.execute("INSERT INTO audit_log VALUES (NULL,?,?,?,?,?)",
                    (rid,"SSO","OTP_VERIFIED","OTP verified",time.time()))
                con.commit()
                msg="OTP verified. Waiting for JE approval"; cls="ok"
            else:
                msg="Invalid OTP"

    con.close()
    return render_template_string(BASE_HTML,
        content=render_template_string(SSO_HTML,linemen=LINEMEN,rid=rid,msg=msg,cls=cls))

@app.route("/je", methods=["GET","POST"])
def je():
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        rid=request.form["rid"]; decision=request.form["decision"]

        if decision=="APPROVE":
            cur.execute("SELECT feeder,action FROM requests WHERE id=?", (rid,))
            f,a=cur.fetchone()
            mqtt_client.publish(f"uppcl/feeder{f}/cmd",a)

        cur.execute("INSERT INTO audit_log VALUES (NULL,?,?,?,?,?)",
            (rid,"JE",decision,f"JE {decision}",time.time()))
        cur.execute("DELETE FROM requests WHERE id=?", (rid,))
        con.commit()
        return redirect("/je")

    cur.execute("SELECT * FROM requests WHERE otp_verified=1")
    rows=cur.fetchall(); con.close()

    return render_template_string(BASE_HTML,
        content=render_template_string(JE_HTML,rows=rows))

@app.route("/audit")
def audit():
    con=sqlite3.connect(DB_FILE); cur=con.cursor()
    cur.execute("SELECT * FROM audit_log ORDER BY timestamp DESC")
    rows=cur.fetchall(); con.close()

    return render_template_string(BASE_HTML,
        content=render_template_string(AUDIT_HTML,rows=rows))

@app.route("/")
def home():
    return "UPPCL SAFETY SERVER RUNNING"

# ================= RUN =================
if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0",port=port)
