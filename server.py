from flask import Flask, request, render_template_string, redirect
import random, os, time, sqlite3, requests
import paho.mqtt.client as mqtt
from datetime import datetime

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

# ================= TIME HELPERS =================
def ts_to_str(ts):
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%d-%m-%Y %H:%M:%S")

def duration_str(start, end):
    if not start or not end:
        return "-"
    sec = int(end - start)
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

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
        shutdown_start REAL,
        shutdown_end REAL,
        created_at REAL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT,
        feeder TEXT,
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
.container{width:85%;margin:20px auto;background:white;padding:20px;border-radius:6px}
h2{color:#003366}
label{font-weight:bold}
select,input{width:100%;padding:8px;margin-bottom:12px}
button{background:#003366;color:white;padding:10px;border:none;border-radius:4px;cursor:pointer}
.card{border:1px solid #ccc;padding:10px;border-radius:5px;margin-bottom:10px;background:#fafafa}
.ok{color:green;font-weight:bold}
.err{color:red;font-weight:bold}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid #aaa;padding:6px;text-align:center}
th{background:#003366;color:white}
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
<option value="TRIP">TRIP (Shutdown)</option>
<option value="CLOSE">CLOSE (Restore)</option>
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
<h2>Audit Log (Shutdown Register)</h2>

<table>
<tr>
<th>Request ID</th>
<th>Feeder</th>
<th>Role</th>
<th>Action</th>
<th>Date & Time</th>
</tr>
{% for r in rows %}
<tr>
<td>{{r.request_id}}</td>
<td>{{r.feeder}}</td>
<td>{{r.role}}</td>
<td>{{r.action}}</td>
<td>{{r.time}}</td>
</tr>
{% endfor %}
</table>

<hr>

<h2>Shutdown Duration Report</h2>
<table>
<tr>
<th>Request ID</th>
<th>Feeder</th>
<th>Shutdown Taken</th>
<th>Shutdown Returned</th>
<th>Duration</th>
</tr>
{% for d in durations %}
<tr>
<td>{{d.id}}</td>
<td>{{d.feeder}}</td>
<td>{{d.start}}</td>
<td>{{d.end}}</td>
<td><b>{{d.duration}}</b></td>
</tr>
{% endfor %}
</table>
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

            cur.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rid,feeder,action,reason,lineman["name"],lineman["mobile"],
                 otp,0,None,None,time.time()))

            cur.execute("INSERT INTO audit_log VALUES (NULL,?,?,?,?,?,?)",
                (rid,feeder,"SSO","OTP_SENT",f"OTP sent to {lineman['name']}",time.time()))

            con.commit()
            requests.get(f"https://2factor.in/API/V1/{OTP_API_KEY}/SMS/{lineman['mobile']}/{otp}")
            msg="OTP sent successfully"; cls="ok"

        if step=="verify":
            rid=request.form["rid"]; otp=request.form["otp"]
            cur.execute("SELECT otp FROM requests WHERE id=?", (rid,))
            row=cur.fetchone()
            if row and row[0]==otp:
                cur.execute("UPDATE requests SET otp_verified=1 WHERE id=?", (rid,))
                cur.execute("INSERT INTO audit_log VALUES (NULL,?,?,?,?,?,?)",
                    (rid,None,"SSO","OTP_VERIFIED","OTP verified",time.time()))
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
        cur.execute("SELECT feeder, action FROM requests WHERE id=?", (rid,))
        feeder, action = cur.fetchone()

        if decision=="APPROVE":
            if action=="TRIP":
                cur.execute("UPDATE requests SET shutdown_start=? WHERE id=?", (time.time(),rid))
            if action=="CLOSE":
                cur.execute("UPDATE requests SET shutdown_end=? WHERE id=?", (time.time(),rid))
            mqtt_client.publish(f"uppcl/feeder{feeder}/cmd", action)

        cur.execute("INSERT INTO audit_log VALUES (NULL,?,?,?,?,?,?)",
            (rid,feeder,"JE",decision,f"JE {decision} {action}",time.time()))
        con.commit()
        return redirect("/je")

    cur.execute("SELECT * FROM requests WHERE otp_verified=1")
    rows=cur.fetchall(); con.close()

    return render_template_string(BASE_HTML,
        content=render_template_string(JE_HTML,rows=rows))

@app.route("/audit")
def audit():
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    cur.execute("SELECT request_id, feeder, role, action, timestamp FROM audit_log ORDER BY timestamp DESC")
    rows_raw=cur.fetchall()

    rows=[]
    for r in rows_raw:
        rows.append({
            "request_id":r[0],
            "feeder":r[1],
            "role":r[2],
            "action":r[3],
            "time":ts_to_str(r[4])
        })

    cur.execute("SELECT id, feeder, shutdown_start, shutdown_end FROM requests WHERE shutdown_start IS NOT NULL")
    dur_raw=cur.fetchall()

    durations=[]
    for d in dur_raw:
        durations.append({
            "id":d[0],
            "feeder":d[1],
            "start":ts_to_str(d[2]),
            "end":ts_to_str(d[3]),
            "duration":duration_str(d[2],d[3])
        })

    con.close()

    return render_template_string(BASE_HTML,
        content=render_template_string(AUDIT_HTML,rows=rows,durations=durations))

@app.route("/")
def home():
    return "UPPCL SAFETY SERVER RUNNING"

# ================= RUN =================
if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0",port=port)
