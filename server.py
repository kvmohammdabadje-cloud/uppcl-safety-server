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
        shutdown_start REAL,
        shutdown_end REAL,
        created_at REAL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rid TEXT,
        feeder TEXT,
        role TEXT,
        action TEXT,
        ts REAL
    )
    """)

    con.commit()
    con.close()

init_db()

# ================= HELPERS =================
def ts(ts):
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %I:%M %p")

def duration(start, end):
    if not start or not end:
        return "-"
    d = int(end - start)
    h = d // 3600
    m = (d % 3600) // 60
    s = d % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

# ================= BASE UI =================
BASE_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>UPPCL Safety</title>
<style>
body{font-family:Arial;background:#f2f4f7}
.header{background:#003366;color:white;padding:15px;text-align:center;font-size:22px}
.container{width:95%;margin:20px auto;background:white;padding:20px;border-radius:6px}
h2{color:#003366}
table{width:100%;border-collapse:collapse}
th,td{border:1px solid #999;padding:8px;text-align:center}
th{background:#003366;color:white}
button{padding:8px 14px;border:none;border-radius:4px;color:white;cursor:pointer}
.btn-approve{background:#28a745}
.btn-reject{background:#dc3545}
.btn-disabled{opacity:0.4;pointer-events:none}
input,select{padding:8px;width:100%;margin-bottom:10px}
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

# ================= SSO UI =================
SSO_HTML = """
<h2>SSO – Shutdown Request</h2>

<form method="post">
<input type="hidden" name="step" value="send">

<label>Feeder</label>
<select name="feeder">
<option value="1">Feeder 1</option>
<option value="2">Feeder 2</option>
</select>

<label>Action</label>
<select name="action">
<option value="TRIP">TAKEN</option>
<option value="CLOSE">RETURN</option>
</select>

<label>Lineman</label>
<select name="lineman">
{% for k,l in linemen.items() %}
<option value="{{k}}">{{l.name}} ({{l.mobile}})</option>
{% endfor %}
</select>

<label>Reason</label>
<input name="reason" required>

<button class="btn-approve" type="submit">Send OTP</button>
</form>

{% if rid %}
<hr>
<form method="post">
<input type="hidden" name="step" value="verify">
<input type="hidden" name="rid" value="{{rid}}">
<label>Enter OTP</label>
<input name="otp" required>
<button class="btn-approve">Verify OTP</button>
</form>
{% endif %}

<p class="{{cls}}">{{msg}}</p>
"""

# ================= JE UI (YOUR FORMAT) =================
JE_HTML = """
<h2>JE – Approval Dashboard</h2>

<table>
<tr>
<th>DATE</th>
<th>TIME</th>
<th>FEEDER</th>
<th>LINEMAN NAME</th>
<th>SHUTDOWN TAKEN / RETURN</th>
<th>REASON FOR SHUTDOWN</th>
<th>JE APPROVAL</th>
<th>JE REJECTION</th>
<th>DURATION OF SHUTDOWN</th>
</tr>

{% for r in rows %}
<tr>
<td>{{r.date}}</td>
<td>{{r.time}}</td>
<td>FEEDER {{r.feeder}}</td>
<td>{{r.lineman}}</td>
<td>{{r.action}}</td>
<td>{{r.reason}}</td>

<td>
<form method="post" onsubmit="lock(this)">
<input type="hidden" name="rid" value="{{r.id}}">
<button class="btn-approve" name="decision" value="APPROVE"
onclick="disableReject(this)">APPROVE</button>
</td>

<td>
<button class="btn-reject" name="decision" value="REJECT"
onclick="disableApprove(this)">REJECT</button>
</form>
</td>

<td><b>{{r.duration}}</b></td>
</tr>
{% endfor %}
</table>

<script>
function disableReject(btn){
btn.closest('tr').querySelector('.btn-reject').classList.add('btn-disabled');
}
function disableApprove(btn){
btn.closest('tr').querySelector('.btn-approve').classList.add('btn-disabled');
}
function lock(form){
form.querySelectorAll("button").forEach(b=>b.disabled=true);
return true;
}
</script>
"""

# ================= ROUTES =================
@app.route("/sso", methods=["GET","POST"])
def sso():
    msg=""; cls="err"; rid=None
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        if request.form["step"]=="send":
            feeder=request.form["feeder"]
            action=request.form["action"]
            reason=request.form["reason"]
            lm=LINEMEN[request.form["lineman"]]
            otp=str(random.randint(100000,999999))
            rid=str(random.randint(1000,9999))

            cur.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rid,feeder,action,reason,lm["name"],lm["mobile"],otp,0,None,None,time.time()))

            cur.execute("INSERT INTO audit_log VALUES(NULL,?,?,?,?,?)",
                (rid,feeder,"SSO","OTP_SENT",time.time()))

            con.commit()
            requests.get(f"https://2factor.in/API/V1/{OTP_API_KEY}/SMS/{lm['mobile']}/{otp}")
            msg="OTP sent successfully"; cls="ok"

        if request.form["step"]=="verify":
            rid=request.form["rid"]; otp=request.form["otp"]
            cur.execute("SELECT otp FROM requests WHERE id=?", (rid,))
            if cur.fetchone()[0]==otp:
                cur.execute("UPDATE requests SET otp_verified=1 WHERE id=?", (rid,))
                cur.execute("INSERT INTO audit_log VALUES(NULL,?,?,?,?,?)",
                    (rid,None,"SSO","OTP_VERIFIED",time.time()))
                con.commit()
                msg="OTP verified. Waiting for JE approval"; cls="ok"
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
        cur.execute("SELECT feeder, action FROM requests WHERE id=?", (rid,))
        feeder, action = cur.fetchone()

        if decision=="APPROVE":
            if action=="TRIP":
                cur.execute("UPDATE requests SET shutdown_start=? WHERE id=?", (time.time(),rid))
            else:
                cur.execute("UPDATE requests SET shutdown_end=? WHERE id=?", (time.time(),rid))
            mqtt_client.publish(f"uppcl/feeder{feeder}/cmd", action)

        cur.execute("INSERT INTO audit_log VALUES(NULL,?,?,?,?,?)",
            (rid,feeder,"JE",decision,time.time()))
        con.commit()
        return redirect("/je")

    cur.execute("""
    SELECT id, feeder, action, reason, lineman,
           shutdown_start, shutdown_end, created_at
    FROM requests WHERE otp_verified=1
    """)
    raw=cur.fetchall()
    con.close()

    rows=[]
    for r in raw:
        rows.append({
            "id":r[0],
            "feeder":r[1],
            "action":"TAKEN" if r[2]=="TRIP" else "RETURN",
            "reason":r[3],
            "lineman":r[4],
            "date":ts(r[7]).split()[0],
            "time":ts(r[7]).split()[1]+" "+ts(r[7]).split()[2],
            "duration":duration(r[5],r[6])
        })

    return render_template_string(BASE_HTML,
        content=render_template_string(JE_HTML,rows=rows))

@app.route("/audit")
def audit():
    con=sqlite3.connect(DB_FILE); cur=con.cursor()
    cur.execute("SELECT * FROM audit_log ORDER BY ts DESC")
    rows=cur.fetchall()
    con.close()
    return {"audit":rows}

@app.route("/")
def home():
    return "UPPCL SAFETY SERVER RUNNING"

# ================= RUN =================
if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0",port=port)
