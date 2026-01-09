from flask import Flask, request, render_template_string, redirect
import sqlite3, time, random, os
from datetime import datetime
import requests
import paho.mqtt.client as mqtt
import pytz

# ================= TIMEZONE =================
IST = pytz.timezone("Asia/Kolkata")

def ist_now():
    return datetime.now(IST)

def ts_to_ist(ts):
    return datetime.fromtimestamp(ts, IST).strftime("%d-%m-%Y %I:%M:%S %p")

# ================= APP =================
app = Flask(__name__)
DB_FILE = "safety.db"

# ================= MQTT =================
MQTT_BROKER = "s871e161.ala.dedicated.gcp.emqxcloud.com"
MQTT_PORT = 1883
MQTT_USER = "UPPCL_SAFETY"
MQTT_PASS = "Lineman@safety123"

mqtt_client = mqtt.Client()
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt_client.loop_start()

# ================= OTP =================
OTP_API_KEY = "f830a94b-ed93-11f0-a6b2-0200cd936042"

LINEMEN = {
    "L1": {"name": "KESHAV", "mobile": "919152225848"},
    "L2": {"name": "RAMESH", "mobile": "919520902397"}
}

# ================= DB INIT =================
def init_db():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS shutdowns (
        shutdown_id TEXT PRIMARY KEY,
        feeder TEXT,
        lineman TEXT,
        reason TEXT,
        trip_time REAL,
        close_time REAL,
        duration TEXT,
        je_status TEXT
    )
    """)

    con.commit()
    con.close()

init_db()

# ================= UI BASE =================
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
<div class="header">UPPCL OTP Based Shutdown Safety System (IST)</div>
<div class="container">
{{ content | safe }}
</div>
</body>
</html>
"""

# ================= SSO =================
SSO_HTML = """
<h2>SSO – Shutdown Request</h2>

<form method="post">
<input type="hidden" name="step" value="send">

Feeder:
<select name="feeder">
<option value="1">Feeder 1</option>
<option value="2">Feeder 2</option>
</select>

Action:
<select name="action">
<option value="TRIP">TAKEN</option>
<option value="CLOSE">RETURN</option>
</select>

Lineman:
<select name="lineman">
{% for k,l in linemen.items() %}
<option value="{{l.name}}">{{l.name}}</option>
{% endfor %}
</select>

Reason:
<input name="reason" required>

<button class="approve">Submit Request</button>
</form>

<p class="{{cls}}">{{msg}}</p>
"""

# ================= JE =================
JE_HTML = """
<h2>JE – Shutdown Register (IST)</h2>

<table>
<tr>
<th>SHUTDOWN ID</th>
<th>FEEDER</th>
<th>LINEMAN</th>
<th>TAKEN TIME</th>
<th>RETURN TIME</th>
<th>DURATION</th>
<th>REASON</th>
<th>JE STATUS</th>
</tr>

{% for r in rows %}
<tr>
<td>{{r.id}}</td>
<td>FEEDER {{r.feeder}}</td>
<td>{{r.lineman}}</td>
<td>{{r.trip}}</td>
<td>{{r.close}}</td>
<td><b>{{r.duration}}</b></td>
<td>{{r.reason}}</td>
<td>
{% if r.je_status %}
<span class="{{ 'badge-ok' if r.je_status=='APPROVED' else 'badge-no' }}">
{{r.je_status}}
</span>
{% else %}
<form method="post">
<input type="hidden" name="shutdown_id" value="{{r.id}}">
<button class="approve" name="decision" value="APPROVE">APPROVE</button>
<button class="reject" name="decision" value="REJECT">REJECT</button>
</form>
{% endif %}
</td>
</tr>
{% endfor %}
</table>
"""

# ================= ROUTES =================
@app.route("/sso", methods=["GET","POST"])
def sso():
    msg=""; cls="err"
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        feeder=request.form["feeder"]
        action=request.form["action"]
        lineman=request.form["lineman"]
        reason=request.form["reason"]

        if action=="TRIP":
            sid=f"SD-{int(time.time())}"
            cur.execute("""
            INSERT INTO shutdowns
            (shutdown_id, feeder, lineman, reason, trip_time)
            VALUES (?,?,?,?,?)
            """,(sid,feeder,lineman,reason,time.time()))
            msg=f"Shutdown TAKEN. ID: {sid}"
            cls="ok"

        if action=="CLOSE":
            cur.execute("""
            SELECT shutdown_id, trip_time FROM shutdowns
            WHERE feeder=? AND lineman=? AND close_time IS NULL
            ORDER BY trip_time DESC LIMIT 1
            """,(feeder,lineman))
            row=cur.fetchone()

            if row:
                sid, start=row
                end=time.time()
                diff=int(end-start)
                duration=f"{diff//3600:02d}:{(diff%3600)//60:02d}:{diff%60:02d}"
                cur.execute("""
                UPDATE shutdowns
                SET close_time=?, duration=?
                WHERE shutdown_id=?
                """,(end,duration,sid))
                msg=f"Shutdown RETURNED. ID: {sid}"
                cls="ok"
            else:
                msg="No active shutdown found"
                cls="err"

        con.commit()

    con.close()
    return render_template_string(BASE_HTML,
        content=render_template_string(SSO_HTML,linemen=LINEMEN,msg=msg,cls=cls))

@app.route("/je", methods=["GET","POST"])
def je():
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        sid=request.form["shutdown_id"]
        decision=request.form["decision"]
        cur.execute("UPDATE shutdowns SET je_status=? WHERE shutdown_id=?",
                    (decision,sid))
        con.commit()
        return redirect("/je")

    cur.execute("SELECT * FROM shutdowns ORDER BY trip_time DESC")
    raw=cur.fetchall()
    con.close()

    rows=[]
    for r in raw:
        rows.append({
            "id":r[0],
            "feeder":r[1],
            "lineman":r[2],
            "reason":r[3],
            "trip":ts_to_ist(r[4]) if r[4] else "-",
            "close":ts_to_ist(r[5]) if r[5] else "RUNNING",
            "duration":r[6] if r[6] else "RUNNING",
            "je_status":r[7]
        })

    return render_template_string(BASE_HTML,
        content=render_template_string(JE_HTML,rows=rows))

@app.route("/")
def home():
    return "UPPCL SAFETY SERVER RUNNING (IST)"

# ================= RUN =================
if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)))
