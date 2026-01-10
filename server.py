from flask import Flask, request, render_template_string, redirect
import sqlite3, random, time, os
import paho.mqtt.client as mqtt
from datetime import datetime, timedelta, timezone

# ================== CONFIG ==================
DB_FILE = "safety.db"

MQTT_BROKER = "s871e161.ala.dedicated.gcp.emqxcloud.com"
MQTT_PORT = 1883
MQTT_USER = "UPPCL_SAFETY"
MQTT_PASS = "Lineman@safety123"

IST = timezone(timedelta(hours=5, minutes=30))

LINEMEN = {
    "L1": {"name": "KESHAV", "mobile": "919152225848"},
    "L2": {"name": "RAMESH", "mobile": "919520902397"}
}

# ================== APP ==================
app = Flask(__name__)

# ================== MQTT ==================
mqtt_client = mqtt.Client()
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt_client.loop_start()

# ================== TIME HELPERS ==================
def ist_now():
    return datetime.now(IST)

def ts_str(ts):
    return datetime.fromtimestamp(ts, IST).strftime("%d/%m/%Y %I:%M %p") if ts else ""

def duration(start, end):
    if not start or not end:
        return ""
    d = int(end - start)
    return f"{d//60} min {d%60} sec"

# ================== DATABASE ==================
def init_db():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS requests(
        id TEXT PRIMARY KEY,
        feeder TEXT,
        lineman_name TEXT,
        reason TEXT,
        action TEXT,
        otp TEXT,
        otp_verified INTEGER,
        shutdown_taken REAL,
        shutdown_return REAL,
        je_decision TEXT,
        created_at REAL
    )
    """)

    con.commit()
    con.close()

init_db()

# ================== BASE HTML ==================
BASE_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>UPPCL Safety System</title>
<style>
body{font-family:Arial;background:#f4f6f8}
.container{width:95%;margin:auto;background:white;padding:20px}
table{width:100%;border-collapse:collapse}
th,td{border:1px solid #666;padding:6px;text-align:center}
th{background:#dbe5f1}
.btn-approve{background:#00b050;color:white;padding:6px;border:none}
.btn-reject{background:#ff0000;color:white;padding:6px;border:none}
button:disabled{opacity:0.4}
</style>
</head>
<body>
<div class="container">
{{ content | safe }}
</div>
</body>
</html>
"""

# ================== SSO PAGE ==================
SSO_HTML = """
<h2>SSO – Shutdown Request</h2>
<form method="post">
<input type="hidden" name="step" value="send">
Feeder:
<select name="feeder"><option>1</option><option>2</option></select><br><br>

Action:
<select name="action"><option value="TRIP">TAKEN</option><option value="CLOSE">RETURN</option></select><br><br>

Lineman:
<select name="lineman">
{% for k,l in linemen.items() %}
<option value="{{k}}">{{l.name}}</option>
{% endfor %}
</select><br><br>

Reason:
<input name="reason" required><br><br>

<button type="submit">Send OTP</button>
</form>

{% if rid %}
<hr>
<b>Shutdown ID:</b> {{rid}}<br><br>
<form method="post">
<input type="hidden" name="step" value="verify">
<input type="hidden" name="rid" value="{{rid}}">
Enter OTP: <input name="otp" required>
<button type="submit">Verify OTP</button>
</form>
{% endif %}

<p>{{msg}}</p>
"""

# ================== JE DASHBOARD ==================
JE_HTML = """
<h2>JE – Approval Dashboard</h2>

<table>
<tr>
<th>DATE</th><th>TIME</th><th>FEEDER</th><th>LINEMAN NAME</th>
<th>SHUTDOWN TAKEN/RETURN</th><th>REASON</th>
<th>JE APPROVAL</th><th>JE REJECTION</th><th>DURATION</th>
</tr>

{% for r in rows %}
<tr>
<td>{{r.date}}</td>
<td>{{r.time}}</td>
<td>FEEDER {{r.feeder}}</td>
<td>{{r.lineman}}</td>
<td>{{r.status}}</td>
<td>{{r.reason}}</td>

<td>
<form method="post">
<input type="hidden" name="rid" value="{{r.id}}">
<button class="btn-approve" name="decision" value="APPROVE" {% if r.decided %}disabled{% endif %}>APPROVE</button>
</form>
</td>

<td>
<form method="post">
<input type="hidden" name="rid" value="{{r.id}}">
<button class="btn-reject" name="decision" value="REJECT" {% if r.decided %}disabled{% endif %}>REJECT</button>
</form>
</td>

<td>{{r.duration}}</td>
</tr>
{% endfor %}
</table>
"""

# ================== ROUTES ==================
@app.route("/sso", methods=["GET","POST"])
def sso():
    rid=None; msg=""
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        if request.form["step"]=="send":
            rid=str(random.randint(1000,9999))
            lin=LINEMEN[request.form["lineman"]]
            cur.execute("""
            INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,(rid,request.form["feeder"],lin["name"],request.form["reason"],
                 request.form["action"],"123456",0,None,None,None,time.time()))
            con.commit()
            msg="OTP Sent (Demo OTP: 123456)"

        if request.form["step"]=="verify":
            rid=request.form["rid"]
            otp=request.form["otp"]
            if otp=="123456":
                cur.execute("UPDATE requests SET otp_verified=1 WHERE id=?",(rid,))
                con.commit()
                msg="OTP Verified. Waiting for JE approval"

    con.close()
    return render_template_string(BASE_HTML,
        content=render_template_string(SSO_HTML,linemen=LINEMEN,rid=rid,msg=msg))

@app.route("/je", methods=["GET","POST"])
def je():
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        rid=request.form["rid"]
        decision=request.form["decision"]

        cur.execute("SELECT action,feeder FROM requests WHERE id=?",(rid,))
        action, feeder = cur.fetchone()

        now=time.time()

        if decision=="APPROVE":
            if action=="TRIP":
                cur.execute("UPDATE requests SET shutdown_taken=?,je_decision='APPROVED' WHERE id=?",(now,rid))
            else:
                cur.execute("UPDATE requests SET shutdown_return=?,je_decision='APPROVED' WHERE id=?",(now,rid))
            mqtt_client.publish(f"uppcl/feeder{feeder}/cmd",action)
        else:
            cur.execute("UPDATE requests SET je_decision='REJECTED' WHERE id=?",(rid,))

        con.commit()
        return redirect("/je")

    cur.execute("""
    SELECT * FROM requests WHERE otp_verified=1 ORDER BY created_at
    """)
    data=cur.fetchall()
    con.close()

    rows=[]
    for r in data:
        rows.append({
            "id":r[0],
            "feeder":r[1],
            "lineman":r[2],
            "reason":r[3],
            "status":"TAKEN" if r[4]=="TRIP" else "RETURN",
            "date":ts_str(r[10]).split(" ")[0],
            "time":ts_str(r[10]).split(" ")[1]+" "+ts_str(r[10]).split(" ")[2],
            "duration":duration(r[7],r[8]),
            "decided":r[9] is not None
        })

    return render_template_string(BASE_HTML,
        content=render_template_string(JE_HTML,rows=rows))

@app.route("/")
def home():
    return "UPPCL SAFETY SERVER RUNNING"

# ================== RUN ==================
if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0",port=port)
