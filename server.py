from flask import Flask, request, render_template_string, redirect
import sqlite3, random, time, os, requests
import paho.mqtt.client as mqtt
from datetime import datetime, timedelta, timezone

# ================= CONFIG =================
DB_FILE = "safety.db"
OTP_API_KEY = "f830a94b-ed93-11f0-a6b2-0200cd936042"

MQTT_BROKER = "s871e161.ala.dedicated.gcp.emqxcloud.com"
MQTT_PORT = 1883
MQTT_USER = "UPPCL_SAFETY"
MQTT_PASS = "Lineman@safety123"

IST = timezone(timedelta(hours=5, minutes=30))

LINEMEN = {
    "L1": {"name": "KESHAV", "mobile": "919152225848"},
    "L2": {"name": "RAMESH", "mobile": "919520902397"}
}

# ================= APP =================
app = Flask(__name__)

# ================= MQTT =================
mqtt_client = mqtt.Client()
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt_client.loop_start()

# ================= TIME HELPERS =================
def ts_str(ts):
    return datetime.fromtimestamp(ts, IST).strftime("%d/%m/%Y %I:%M %p") if ts else ""

def duration(start, end):
    if not start or not end:
        return ""
    sec = int(end - start)
    return f"{sec//60} min {sec%60} sec"

# ================= DATABASE =================
def init_db():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS requests(
        id TEXT PRIMARY KEY,
        feeder TEXT,
        sso_id TEXT,
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

# ================= BASE HTML =================
BASE_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>UPPCL Lineman Safety Shutdown</title>
<style>
body{font-family:Arial;background:#eef2f6}
.header{background:#003366;color:white;padding:15px;text-align:center;font-size:22px}
.container{width:95%;margin:auto;background:white;padding:20px}
table{width:100%;border-collapse:collapse}
th,td{border:1px solid #555;padding:6px;text-align:center}
th{background:#cfe2f3}
.btn-approve{background:#00b050;color:white;padding:6px;border:none}
.btn-reject{background:#ff0000;color:white;padding:6px;border:none}
button:disabled{opacity:0.4}
h2{color:#003366}
</style>
</head>
<body>
<div class="header">PROJECT :- UPPCL LINEMAN SAFETY SHUTDOWN</div>
<div class="container">
{{ content | safe }}
</div>
</body>
</html>
"""

# ================= SSO PAGE =================
SSO_HTML = """
<h2>SSO DASHBOARD</h2>

<form method="post">
<input type="hidden" name="step" value="send">

<b>SSO ID:</b>
<input name="sso_id" required><br><br>

Feeder:
<select name="feeder">
<option value="1">FEEDER 1</option>
<option value="2">FEEDER 2</option>
</select><br><br>

Action:
<select name="action">
<option value="TRIP">TAKEN (Shutdown)</option>
<option value="CLOSE">RETURN (Restore)</option>
</select><br><br>

Lineman:
<select name="lineman">
{% for k,l in linemen.items() %}
<option value="{{k}}">{{l.name}} ({{l.mobile}})</option>
{% endfor %}
</select><br><br>

Reason:
<input name="reason" required><br><br>

<button type="submit">SEND OTP</button>
</form>

{% if rid %}
<hr>
<b>Shutdown ID:</b> {{rid}}<br><br>
<form method="post">
<input type="hidden" name="step" value="verify">
<input type="hidden" name="rid" value="{{rid}}">
Enter OTP:
<input name="otp" required>
<button type="submit">VERIFY OTP</button>
</form>
{% endif %}

<p><b>{{msg}}</b></p>
"""

# ================= JE DASHBOARD =================
JE_HTML = """
<h2>JE DASHBOARD</h2>

<table>
<tr>
<th>DATE</th>
<th>TIME</th>
<th>SSO ID</th>
<th>FEEDER</th>
<th>LINEMAN NAME</th>
<th>SHUTDOWN TAKEN / RETURN</th>
<th>REASON</th>
<th>JE APPROVAL</th>
<th>JE REJECTION</th>
<th>DURATION</th>
</tr>

{% for r in rows %}
<tr>
<td>{{r.date}}</td>
<td>{{r.time}}</td>
<td>{{r.sso_id}}</td>
<td>FEEDER {{r.feeder}}</td>
<td>{{r.lineman}}</td>
<td>{{r.status}}</td>
<td>{{r.reason}}</td>

<td>
<form method="post">
<input type="hidden" name="rid" value="{{r.id}}">
<button class="btn-approve" name="decision" value="APPROVE"
{% if r.decided %}disabled{% endif %}>APPROVE</button>
</form>
</td>

<td>
<form method="post">
<input type="hidden" name="rid" value="{{r.id}}">
<button class="btn-reject" name="decision" value="REJECT"
{% if r.decided %}disabled{% endif %}>REJECT</button>
</form>
</td>

<td>{{r.duration}}</td>
</tr>
{% endfor %}
</table>
"""

# ================= ROUTES =================
@app.route("/sso", methods=["GET","POST"])
def sso():
    rid=None; msg=""
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        step=request.form["step"]

        if step=="send":
            rid=str(random.randint(1000,9999))
            lin=LINEMEN[request.form["lineman"]]
            otp=str(random.randint(100000,999999))

            cur.execute("""
            INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,(rid,request.form["feeder"],request.form["sso_id"],
                 lin["name"],request.form["reason"],request.form["action"],
                 otp,0,None,None,None,time.time()))
            con.commit()

            requests.get(
                f"https://2factor.in/API/V1/{OTP_API_KEY}/SMS/{lin['mobile']}/{otp}"
            )
            msg="OTP sent successfully"

        if step=="verify":
            rid=request.form["rid"]
            otp=request.form["otp"]
            cur.execute("SELECT otp FROM requests WHERE id=?",(rid,))
            if cur.fetchone()[0]==otp:
                cur.execute("UPDATE requests SET otp_verified=1 WHERE id=?",(rid,))
                con.commit()
                msg="OTP verified. Waiting for JE approval"
            else:
                msg="Invalid OTP"

    con.close()
    return render_template_string(
        BASE_HTML,
        content=render_template_string(SSO_HTML,
        linemen=LINEMEN,rid=rid,msg=msg)
    )

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

    cur.execute("SELECT * FROM requests WHERE otp_verified=1 ORDER BY created_at")
    data=cur.fetchall()
    con.close()

    rows=[]
    for r in data:
        rows.append({
            "id":r[0],
            "feeder":r[1],
            "sso_id":r[2],
            "lineman":r[3],
            "reason":r[4],
            "status":"TAKEN" if r[5]=="TRIP" else "RETURN",
            "date":ts_str(r[11]).split(" ")[0],
            "time":ts_str(r[11]).split(" ")[1]+" "+ts_str(r[11]).split(" ")[2],
            "duration":duration(r[8],r[9]),
            "decided":r[10] is not None
        })

    return render_template_string(
        BASE_HTML,
        content=render_template_string(JE_HTML,rows=rows)
    )

@app.route("/")
def home():
    return "UPPCL LINEMAN SAFETY SHUTDOWN SERVER RUNNING"

# ================= RUN =================
if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0",port=port)
