from flask import Flask, request, render_template_string, redirect
import sqlite3, time, random, os
from datetime import datetime
import requests
import paho.mqtt.client as mqtt

app = Flask(__name__)

# ================= CONFIG =================
DB_FILE = "safety.db"

MQTT_BROKER = "s871e161.ala.dedicated.gcp.emqxcloud.com"
MQTT_PORT = 1883
MQTT_USER = "UPPCL_SAFETY"
MQTT_PASS = "Lineman@safety123"

OTP_API_KEY = "f830a94b-ed93-11f0-a6b2-0200cd936042"

LINEMEN = {
    "L1": {"name": "KESHAV", "mobile": "919152225848"},
    "L2": {"name": "RAMESH", "mobile": "919520902397"}
}

# ================= MQTT =================
mqtt_client = mqtt.Client()
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt_client.loop_start()

# ================= DB INIT =================
def init_db():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS requests(
        id TEXT PRIMARY KEY,
        feeder TEXT,
        action TEXT,
        reason TEXT,
        lineman TEXT,
        mobile TEXT,
        otp TEXT,
        otp_verified INTEGER,
        je_decision TEXT,
        shutdown_time REAL,
        created_at REAL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rid TEXT,
        feeder TEXT,
        lineman TEXT,
        action TEXT,
        ts REAL
    )
    """)

    con.commit()
    con.close()

init_db()

# ================= HELPERS =================
def fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %I:%M:%S %p")

def get_shutdown_duration(con, feeder, lineman):
    cur = con.cursor()
    cur.execute("""
    SELECT shutdown_time FROM requests
    WHERE feeder=? AND lineman=? AND action='TRIP' AND je_decision='APPROVE'
    ORDER BY shutdown_time DESC LIMIT 1
    """, (feeder, lineman))
    row = cur.fetchone()
    if not row:
        return "-"
    start = row[0]

    cur.execute("""
    SELECT shutdown_time FROM requests
    WHERE feeder=? AND lineman=? AND action='CLOSE' AND je_decision='APPROVE'
    ORDER BY shutdown_time DESC LIMIT 1
    """, (feeder, lineman))
    row2 = cur.fetchone()
    if not row2:
        return "RUNNING"

    diff = int(row2[0] - start)
    h = diff // 3600
    m = (diff % 3600) // 60
    s = diff % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

# ================= UI =================
BASE_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>UPPCL Safety</title>
<style>
body{font-family:Arial;background:#f2f4f7}
.header{background:#003366;color:white;padding:15px;text-align:center;font-size:22px}
.container{width:96%;margin:20px auto;background:white;padding:20px;border-radius:6px}
table{width:100%;border-collapse:collapse}
th,td{border:1px solid #999;padding:6px;text-align:center}
th{background:#003366;color:white}
button{padding:6px 12px;border:none;border-radius:4px;color:white;cursor:pointer}
.approve{background:#28a745}
.reject{background:#dc3545}
.badge-ok{background:#28a745;color:white;padding:4px 8px;border-radius:4px}
.badge-no{background:#dc3545;color:white;padding:4px 8px;border-radius:4px}
input,select{padding:6px;width:100%}
.ok{color:green;font-weight:bold}
.err{color:red;font-weight:bold}
</style>
</head>
<body>
<div class="header">UPPCL OTP Based Shutdown Safety System</div>
<div class="container">
{{ content | safe }}
</div>
</body>
</html>
"""

SSO_HTML = """
<h2>SSO – Shutdown Request</h2>

<form method="post">
<input type="hidden" name="step" value="send">

Feeder:
<select name="feeder"><option>1</option><option>2</option></select>

Action:
<select name="action"><option value="TRIP">TAKEN</option><option value="CLOSE">RETURN</option></select>

Lineman:
<select name="lineman">
{% for k,l in linemen.items() %}
<option value="{{k}}">{{l.name}}</option>
{% endfor %}
</select>

Reason:
<input name="reason" required>

<button class="approve">Send OTP</button>
</form>

{% if rid %}
<hr>
<b>Request ID:</b> {{rid}}
<form method="post">
<input type="hidden" name="step" value="verify">
<input type="hidden" name="rid" value="{{rid}}">
OTP: <input name="otp" required>
<button class="approve">Verify OTP</button>
</form>
{% endif %}

<p class="{{cls}}">{{msg}}</p>
"""

JE_HTML = """
<h2>JE – Approval Dashboard</h2>

<table>
<tr>
<th>REQ ID</th><th>DATE</th><th>TIME</th><th>FEEDER</th>
<th>LINEMAN</th><th>TAKEN / RETURN</th>
<th>REASON</th><th>JE STATUS</th><th>DURATION</th>
</tr>

{% for r in rows %}
<tr>
<td>{{r.id}}</td>
<td>{{r.date}}</td>
<td>{{r.time}}</td>
<td>{{r.feeder}}</td>
<td>{{r.lineman}}</td>
<td>{{r.action}}</td>
<td>{{r.reason}}</td>

<td>
{% if r.je_decision %}
<span class="{{ 'badge-ok' if r.je_decision=='APPROVE' else 'badge-no' }}">
{{ r.je_decision }}
</span>
{% else %}
<form method="post">
<input type="hidden" name="rid" value="{{r.id}}">
<button class="approve" name="decision" value="APPROVE">APPROVE</button>
<button class="reject" name="decision" value="REJECT">REJECT</button>
</form>
{% endif %}
</td>

<td>{{r.duration}}</td>
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
        if request.form["step"]=="send":
            rid=str(random.randint(1000,9999))
            feeder=request.form["feeder"]
            action=request.form["action"]
            reason=request.form["reason"]
            lm=LINEMEN[request.form["lineman"]]
            otp=str(random.randint(100000,999999))

            cur.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rid,feeder,action,reason,lm["name"],lm["mobile"],otp,0,None,None,time.time()))
            con.commit()
            requests.get(f"https://2factor.in/API/V1/{OTP_API_KEY}/SMS/{lm['mobile']}/{otp}")
            msg="OTP sent"; cls="ok"

        if request.form["step"]=="verify":
            rid=request.form["rid"]; otp=request.form["otp"]
            cur.execute("SELECT otp FROM requests WHERE id=?", (rid,))
            if cur.fetchone()[0]==otp:
                cur.execute("UPDATE requests SET otp_verified=1 WHERE id=?", (rid,))
                con.commit()
                msg="OTP verified, waiting JE"; cls="ok"
            else:
                msg="Invalid OTP"

    con.close()
    return render_template_string(BASE_HTML,
        content=render_template_string(SSO_HTML,linemen=LINEMEN,msg=msg,cls=cls,rid=rid))

@app.route("/je", methods=["GET","POST"])
def je():
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        rid=request.form["rid"]; decision=request.form["decision"]
        cur.execute("SELECT feeder, action, lineman FROM requests WHERE id=?", (rid,))
        feeder, action, lineman = cur.fetchone()

        cur.execute("UPDATE requests SET je_decision=?, shutdown_time=? WHERE id=?",
                    (decision, time.time(), rid))

        if decision=="APPROVE":
            mqtt_client.publish(f"uppcl/feeder{feeder}/cmd", action)

        con.commit()
        return redirect("/je")

    cur.execute("""
    SELECT id, feeder, action, reason, lineman,
           je_decision, created_at
    FROM requests WHERE otp_verified=1
    """)
    raw=cur.fetchall()

    rows=[]
    for r in raw:
        rows.append({
            "id":r[0],
            "feeder":r[1],
            "action":"TAKEN" if r[2]=="TRIP" else "RETURN",
            "reason":r[3],
            "lineman":r[4],
            "je_decision":r[5],
            "date":fmt(r[6]).split()[0],
            "time":fmt(r[6]).split()[1]+" "+fmt(r[6]).split()[2],
            "duration":get_shutdown_duration(con,r[1],r[4])
        })

    con.close()
    return render_template_string(BASE_HTML,
        content=render_template_string(JE_HTML,rows=rows))

@app.route("/")
def home():
    return "UPPCL SAFETY SERVER RUNNING"

# ================= RUN =================
if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
