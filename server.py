from flask import Flask, request, render_template_string, redirect
import sqlite3, time, os, random
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

# ================= DATABASE =================
def init_db():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS shutdowns (
        shutdown_id TEXT PRIMARY KEY,
        feeder TEXT,
        lineman TEXT,
        mobile TEXT,
        reason TEXT,
        taken_time REAL,
        return_time REAL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shutdown_id TEXT,
        action TEXT,
        otp TEXT,
        otp_verified INTEGER,
        je_decision TEXT,
        je_time REAL,
        created_at REAL
    )
    """)

    con.commit()
    con.close()

init_db()

# ================= HELPERS =================
def now_str():
    return datetime.now().strftime("%d/%m/%Y %I:%M:%S %p")

def make_shutdown_id():
    return "SD-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(random.randint(100,999))

def duration(start, end):
    if not start:
        return "-"
    if not end:
        return "RUNNING"
    d = int(end - start)
    return f"{d//3600:02d}:{(d%3600)//60:02d}:{d%60:02d}"

# ================= UI =================
BASE_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>UPPCL Safety</title>
<style>
body{font-family:Arial;background:#f2f4f7}
.header{background:#003366;color:white;padding:15px;text-align:center;font-size:22px}
.container{width:98%;margin:20px auto;background:white;padding:20px;border-radius:6px}
table{width:100%;border-collapse:collapse}
th,td{border:1px solid #999;padding:6px;text-align:center}
th{background:#003366;color:white}
button{padding:6px 12px;border:none;border-radius:4px;color:white}
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

# ================= SSO =================
SSO_HTML = """
<h2>SSO – Shutdown Request</h2>

<form method="post">
<input type="hidden" name="step" value="send">

Feeder:
<select name="feeder"><option>1</option><option>2</option></select>

Action:
<select name="action">
<option value="TAKEN">TAKEN</option>
<option value="RETURN">RETURN</option>
</select>

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

{% if sid %}
<hr>
<b>Shutdown Code:</b> {{sid}}
<form method="post">
<input type="hidden" name="step" value="verify">
<input type="hidden" name="sid" value="{{sid}}">
OTP: <input name="otp" required>
<button class="approve">Verify OTP</button>
</form>
{% endif %}

<p class="{{cls}}">{{msg}}</p>
"""

# ================= JE =================
JE_HTML = """
<h2>JE – Shutdown Register</h2>

<table>
<tr>
<th>SHUTDOWN ID</th><th>FEEDER</th><th>LINEMAN</th>
<th>ACTION</th><th>REASON</th>
<th>TAKEN TIME</th><th>RETURN TIME</th>
<th>DURATION</th><th>JE STATUS</th>
</tr>

{% for r in rows %}
<tr>
<td>{{r.sid}}</td>
<td>{{r.feeder}}</td>
<td>{{r.lineman}}</td>
<td>{{r.action}}</td>
<td>{{r.reason}}</td>
<td>{{r.taken}}</td>
<td>{{r.return}}</td>
<td>{{r.duration}}</td>

<td>
{% if r.je %}
<span class="{{ 'badge-ok' if r.je=='APPROVE' else 'badge-no' }}">{{r.je}}</span>
{% else %}
<form method="post">
<input type="hidden" name="rid" value="{{r.req_id}}">
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
    msg=""; cls="err"; sid=None
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        if request.form["step"]=="send":
            sid = make_shutdown_id()
            feeder=request.form["feeder"]
            action=request.form["action"]
            reason=request.form["reason"]
            lm=LINEMEN[request.form["lineman"]]
            otp=str(random.randint(100000,999999))

            if action=="TAKEN":
                cur.execute("INSERT INTO shutdowns VALUES (?,?,?,?,?,?,?)",
                            (sid,feeder,lm["name"],lm["mobile"],reason,None,None))

            cur.execute("""
            INSERT INTO requests(shutdown_id,action,otp,otp_verified,created_at)
            VALUES (?,?,?,?,?)
            """,(sid,action,otp,0,time.time()))

            con.commit()
            requests.get(f"https://2factor.in/API/V1/{OTP_API_KEY}/SMS/{lm['mobile']}/{otp}")
            msg="OTP sent"; cls="ok"

        if request.form["step"]=="verify":
            sid=request.form["sid"]; otp=request.form["otp"]
            cur.execute("SELECT id FROM requests WHERE shutdown_id=? AND otp=?", (sid,otp))
            row=cur.fetchone()
            if row:
                cur.execute("UPDATE requests SET otp_verified=1 WHERE id=?", (row[0],))
                con.commit()
                msg="OTP verified. Waiting JE approval"; cls="ok"
            else:
                msg="Invalid OTP"

    con.close()
    return render_template_string(BASE_HTML,
        content=render_template_string(SSO_HTML,linemen=LINEMEN,msg=msg,cls=cls,sid=sid))

@app.route("/je", methods=["GET","POST"])
def je():
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        rid=request.form["rid"]; decision=request.form["decision"]

        cur.execute("SELECT shutdown_id, action FROM requests WHERE id=?", (rid,))
        sid, action = cur.fetchone()

        t=time.time()
        cur.execute("UPDATE requests SET je_decision=?, je_time=? WHERE id=?",
                    (decision,t,rid))

        if decision=="APPROVE":
            if action=="TAKEN":
                cur.execute("UPDATE shutdowns SET taken_time=? WHERE shutdown_id=?", (t,sid))
            else:
                cur.execute("UPDATE shutdowns SET return_time=? WHERE shutdown_id=?", (t,sid))

        con.commit()
        return redirect("/je")

    cur.execute("""
    SELECT r.id, s.shutdown_id, s.feeder, s.lineman, r.action,
           s.reason, s.taken_time, s.return_time, r.je_decision
    FROM requests r
    JOIN shutdowns s ON r.shutdown_id=s.shutdown_id
    ORDER BY r.created_at DESC
    """)
    data=cur.fetchall()
    con.close()

    rows=[]
    for d in data:
        rows.append({
            "req_id":d[0],
            "sid":d[1],
            "feeder":d[2],
            "lineman":d[3],
            "action":d[4],
            "reason":d[5],
            "taken": "-" if not d[6] else now_str(),
            "return":"-" if not d[7] else now_str(),
            "duration":duration(d[6],d[7]),
            "je":d[8]
        })

    return render_template_string(BASE_HTML,
        content=render_template_string(JE_HTML,rows=rows))

@app.route("/")
def home():
    return "UPPCL SAFETY SERVER RUNNING"

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)))
