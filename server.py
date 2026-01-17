from flask import Flask, request, render_template_string, redirect
import sqlite3, random, time, requests
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
    "L2": {"name": "MUKESH", "mobile": "919520902397"}
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
    s = int(end - start)
    return f"{s//60} min {s%60} sec"

# ================= DATABASE =================
def init_db():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS requests(
        id TEXT,
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

# ================= UI ACTIVE LINEMAN =================
def ui_active_lineman_details(feeder):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
        SELECT lineman_name, action
        FROM requests r1
        WHERE feeder=? AND otp_verified=1
          AND created_at = (
            SELECT MAX(created_at)
            FROM requests r2
            WHERE r2.lineman_name=r1.lineman_name
              AND r2.feeder=r1.feeder
              AND r2.otp_verified=1
          )
    """, (feeder,))
    active=[n for n,a in cur.fetchall() if a=="TRIP"]
    con.close()
    return active, len(active)

# ================= SAFETY ACTIVE LINEMAN =================
def safety_active_lineman_details(feeder):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
        SELECT lineman_name, action
        FROM requests r1
        WHERE feeder=? AND je_decision='APPROVED'
          AND created_at = (
            SELECT MAX(created_at)
            FROM requests r2
            WHERE r2.lineman_name=r1.lineman_name
              AND r2.feeder=r1.feeder
              AND r2.je_decision='APPROVED'
          )
    """, (feeder,))
    active=[n for n,a in cur.fetchall() if a=="TRIP"]
    con.close()
    return active, len(active)

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
input,select{width:350px;padding:8px}
.btn-approve{background:#00b050;color:white;padding:6px;border:none}
.btn-reject{background:#ff0000;color:white;padding:6px;border:none}
button:disabled{opacity:0.4}
.badge{background:#dc3545;color:white;padding:4px 8px;border-radius:12px}
.lock{background:#fff3cd;border:1px solid #ffcc00;padding:10px;margin-bottom:10px}
</style>
</head>
<body>
<div class="header">PROJECT :- UPPCL LINEMAN SAFETY SHUTDOWN</div>
<div class="container">{{ content | safe }}</div>
</body>
</html>
"""

# ================= SSO PAGE =================
SSO_HTML = """
<h2>SSO DASHBOARD</h2>
<form method="post">
<input type="hidden" name="step" value="send">
SSO ID:<br><input name="sso_id" required><br><br>
Feeder:<br>
<select name="feeder"><option value="1">FEEDER 1</option><option value="2">FEEDER 2</option></select><br><br>
Action:<br>
<select name="action"><option value="TRIP">TAKEN</option><option value="CLOSE">RETURN</option></select><br><br>
Lineman:<br>
<select name="lineman">{% for k,l in linemen.items() %}<option value="{{k}}">{{l.name}}</option>{% endfor %}</select><br><br>
Reason:<br><input name="reason" required><br><br>
<button type="submit">SEND OTP</button>
</form>
{% if rid %}
<hr>
Shutdown ID: <b>{{rid}}</b><br><br>
<form method="post">
<input type="hidden" name="step" value="verify">
<input type="hidden" name="rid" value="{{rid}}">
OTP:<br><input name="otp" required>
<button type="submit">VERIFY OTP</button>
</form>
{% endif %}
<p><b>{{msg}}</b></p>
"""

# ================= JE DASHBOARD =================
JE_HTML = """
<h2>JE DASHBOARD</h2>
{% for feeder,info in lock_info.items() %}
<div class="lock">
🔒 FEEDER {{feeder}} ACTIVE <span class="badge">{{info.count}}</span><br>
{% for n in info.names %}🟢 {{n}}<br>{% endfor %}
</div>
{% endfor %}
<table>
<tr><th>DATE</th><th>TIME</th><th>SSO</th><th>FEEDER</th><th>LINEMAN</th><th>STATUS</th><th>REASON</th><th>APPROVE</th><th>REJECT</th><th>DURATION</th></tr>
{% for r in rows %}
<tr>
<td>{{r.date}}</td><td>{{r.time}}</td><td>{{r.sso_id}}</td><td>{{r.feeder}}</td>
<td>{{r.lineman}}</td><td>{{r.status}}</td><td>{{r.reason}}</td>
<td><form method="post"><input type="hidden" name="rid" value="{{r.id}}">
<button class="btn-approve" {% if r.decided %}disabled{% endif %} name="decision" value="APPROVE">APPROVE</button></form></td>
<td><form method="post"><input type="hidden" name="rid" value="{{r.id}}">
<button class="btn-reject" {% if r.decided %}disabled{% endif %} name="decision" value="REJECT">REJECT</button></form></td>
<td>{{r.duration}}</td>
</tr>{% endfor %}
</table>
"""

# ================= ROUTES =================
@app.route("/sso", methods=["GET","POST"])
def sso():
    rid=None; msg=""
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        if request.form["step"]=="send":
            feeder=request.form["feeder"]
            lineman_key=request.form["lineman"]
            lineman_name=LINEMEN[lineman_key]["name"]

            active,_=safety_active_lineman_details(feeder)
            if request.form["action"]=="TRIP" and lineman_name in active:
                msg=f"❌ Lineman {lineman_name} already has active shutdown."
                con.close()
                return render_template_string(BASE_HTML,content=render_template_string(SSO_HTML,linemen=LINEMEN,msg=msg))

            rid=str(random.randint(1000,9999))
            otp=str(random.randint(100000,999999))
            lin=LINEMEN[lineman_key]

            cur.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid,feeder,request.form["sso_id"],lin["name"],request.form["reason"],
                 request.form["action"],otp,0,None,None,None,time.time()))
            con.commit()

            requests.get(f"https://2factor.in/API/V1/{OTP_API_KEY}/SMS/{lin['mobile']}/{otp}")
            msg=f"OTP sent. Lineman {lin['name']} requested {request.form['action']}."

        if request.form["step"]=="verify":
            rid=request.form["rid"]
            otp=request.form["otp"]
            cur.execute("SELECT otp FROM requests WHERE id=?",(rid,))
            if cur.fetchone()[0]==otp:
                cur.execute("UPDATE requests SET otp_verified=1 WHERE id=?",(rid,))
                con.commit()
                msg="OTP verified. Waiting JE approval"
            else:
                msg="Invalid OTP"

    con.close()
    return render_template_string(BASE_HTML,content=render_template_string(SSO_HTML,linemen=LINEMEN,rid=rid,msg=msg))

@app.route("/je", methods=["GET","POST"])
def je():
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        rid=request.form["rid"]
        decision=request.form["decision"]
        cur.execute("SELECT action,feeder FROM requests WHERE id=?",(rid,))
        action,feeder=cur.fetchone()
        now=time.time()

        if decision=="APPROVE":
            if action=="TRIP":
                cur.execute("UPDATE requests SET shutdown_taken=?,je_decision='APPROVED' WHERE id=?",(now,rid))
                mqtt_client.publish(f"uppcl/feeder{feeder}/cmd","TRIP")
            else:
               # APPROVE RETURN FIRST
cur.execute(
    "UPDATE requests SET shutdown_return=?, je_decision='APPROVED' WHERE id=?",
    (now, rid)
)
con.commit()   # 🔑 FORCE DB COMMIT

# RECHECK ACTIVE LINEMEN AFTER APPROVAL
active_names, active_count = safety_active_lineman_details(feeder)

if active_count == 0:
    mqtt_client.publish(f"uppcl/feeder{feeder}/cmd", "CLOSE")
    
        else:
            cur.execute("UPDATE requests SET je_decision='REJECTED' WHERE id=?",(rid,))
        con.commit()
        return redirect("/je")

    cur.execute("SELECT * FROM requests WHERE otp_verified=1 ORDER BY created_at")
    data=cur.fetchall()
    con.close()

    rows=[]
    lock_info={}
    for f in ["1","2"]:
        n,c=ui_active_lineman_details(f)
        if c>0: lock_info[f]={"names":n,"count":c}

    for r in data:
        rows.append({
            "id":r[0],"feeder":r[1],"sso_id":r[2],"lineman":r[3],"reason":r[4],
            "status":"TAKEN" if r[5]=="TRIP" else "RETURN",
            "date":ts_str(r[11]).split(" ")[0],
            "time":ts_str(r[11]).split(" ")[1]+" "+ts_str(r[11]).split(" ")[2],
            "duration":duration(r[8],r[9]),"decided":r[10] is not None
        })

    return render_template_string(BASE_HTML,content=render_template_string(JE_HTML,rows=rows,lock_info=lock_info))

@app.route("/")
def home():
    return "UPPCL LINEMAN SAFETY SHUTDOWN SERVER RUNNING"

if __name__=="__main__":
    app.run(host="0.0.0.0", port=10000)

