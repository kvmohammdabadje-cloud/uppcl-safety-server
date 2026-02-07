from flask import Flask, request, render_template_string, redirect
import sqlite3, random, time, requests
import paho.mqtt.client as mqtt
from datetime import datetime, timedelta, timezone

# ================= CONFIG =================
DB_FILE = "safety.db"
OTP_API_KEY = "f830a94b-ed93-11f0-a6b2-0200cd936042"

MQTT_BROKER = "s871e161.ala.dedicated.gcp.emqxcloud.com"
MQTT_PORT   = 1883
MQTT_USER   = "UPPCL_SAFETY"
MQTT_PASS   = "Lineman@safety123"

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

# ================= TIME =================
def ts(ts):
    return datetime.fromtimestamp(ts, IST).strftime("%d/%m/%Y %I:%M %p")

def duration(a,b):
    if not a or not b:
        return ""
    d=int(b-a)
    return f"{d//60} min {d%60} sec"

# ================= ACTIVE SHUTDOWNS =================
def ui_active_lineman(feeder):
    con=sqlite3.connect(DB_FILE)
    cur=con.cursor()
    cur.execute("""
        SELECT id, lineman_name FROM requests r1
        WHERE feeder=? AND otp_verified=1 AND action='TRIP'
          AND created_at=(
            SELECT MAX(created_at) FROM requests r2
            WHERE r2.lineman_name=r1.lineman_name
              AND r2.feeder=r1.feeder
              AND r2.otp_verified=1
          )
    """,(feeder,))
    data=cur.fetchall()
    con.close()
    return data  # [(id, name)]

def safety_active_lineman(feeder):
    con=sqlite3.connect(DB_FILE)
    cur=con.cursor()
    cur.execute("""
        SELECT lineman_name FROM requests r1
        WHERE feeder=? AND je_decision='APPROVED' AND action='TRIP'
          AND created_at=(
            SELECT MAX(created_at) FROM requests r2
            WHERE r2.lineman_name=r1.lineman_name
              AND r2.feeder=r1.feeder
              AND r2.je_decision='APPROVED'
          )
    """,(feeder,))
    data=[r[0] for r in cur.fetchall()]
    con.close()
    return data

# ================= UI =================
BASE = """
<!DOCTYPE html>
<html>
<head>
<title>UPPCL LINEMAN SAFETY SHUTDOWN</title>
<style>
body{font-family:Arial;background:#eef2f6}
.header{background:#003366;color:white;padding:15px;text-align:center;font-size:22px}
.container{width:95%;margin:auto;background:white;padding:20px}
table{width:100%;border-collapse:collapse}
th,td{border:1px solid #555;padding:6px;text-align:center}
th{background:#cfe2f3}
input,select{width:320px;padding:8px}
.btnA{background:#00b050;color:white;padding:6px;border:none}
.btnR{background:#ff0000;color:white;padding:6px;border:none}
button:disabled{opacity:0.4}
.lock{background:#fff3cd;border:1px solid #ffcc00;padding:10px;margin-bottom:10px}
.badge{background:red;color:white;padding:3px 8px;border-radius:10px}
</style>
</head>
<body>
<div class="header">PROJECT :- UPPCL LINEMAN SAFETY SHUTDOWN</div>
<div class="container">{{ body|safe }}</div>
</body>
</html>
"""

# ================= SSO =================
SSO = """
<h2>SSO DASHBOARD</h2>
<form method="post">
<input type="hidden" name="step" value="send">
SSO ID:<br><input name="sso_id" required><br><br>
Feeder:<br><select name="feeder"><option>1</option><option>2</option></select><br><br>
Action:<br><select name="action"><option value="TRIP">TAKEN</option><option value="CLOSE">RETURN</option></select><br><br>
Lineman:<br><select name="lineman">{% for k,l in linemen.items() %}<option value="{{k}}">{{l.name}}</option>{% endfor %}</select><br><br>
Reason:<br><input name="reason" required><br><br>
<button>SEND OTP</button>
</form>

{% if rid %}
<hr>
Shutdown ID: <b>{{rid}}</b><br><br>
<form method="post">
<input type="hidden" name="step" value="verify">
<input type="hidden" name="rid" value="{{rid}}">
OTP:<br><input name="otp" required>
<button>VERIFY OTP</button>
</form>
{% endif %}

<p><b>{{msg}}</b></p>
"""

# ================= JE =================
JE = """
<h2>JE DASHBOARD</h2>

{% for f,items in locks.items() %}
<div class="lock">
🔒 FEEDER {{f}} ACTIVE <span class="badge">{{items|length}}</span><br>
{% for sid,name in items %}
🟢 {{name}} | Shutdown ID: <b>{{sid}}</b><br>
{% endfor %}
</div>
{% endfor %}

<table>
<tr>
<th>DATE</th><th>TIME</th><th>SHUTDOWN ID</th><th>SSO</th><th>FEEDER</th>
<th>LINEMAN</th><th>STATUS</th><th>REASON</th>
<th>APPROVE</th><th>REJECT</th><th>DURATION</th>
</tr>

{% for r in rows %}
<tr>
<td>{{r.d}}</td><td>{{r.t}}</td><td>{{r.id}}</td><td>{{r.sso}}</td><td>{{r.f}}</td>
<td>{{r.l}}</td><td>{{r.st}}</td><td>{{r.rs}}</td>
<td>
<form method="post">
<input type="hidden" name="rid" value="{{r.id}}">
<button class="btnA" {% if r.done %}disabled{% endif %} name="decision" value="APPROVE">APPROVE</button>
</form>
</td>
<td>
<form method="post">
<input type="hidden" name="rid" value="{{r.id}}">
<button class="btnR" {% if r.done %}disabled{% endif %} name="decision" value="REJECT">REJECT</button>
</form>
</td>
<td>{{r.du}}</td>
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
        if request.form["step"]=="send":
            feeder=request.form["feeder"]
            lin=LINEMEN[request.form["lineman"]]

            if request.form["action"]=="TRIP" and lin["name"] in safety_active_lineman(feeder):
                msg=f"❌ {lin['name']} already has active shutdown"
            else:
                rid=str(random.randint(1000,9999))
                otp=str(random.randint(100000,999999))
                cur.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid,feeder,request.form["sso_id"],lin["name"],request.form["reason"],
                     request.form["action"],otp,0,None,None,None,time.time()))
                con.commit()
                requests.get(f"https://2factor.in/API/V1/{OTP_API_KEY}/SMS/{lin['mobile']}/{otp}")
                msg=f"OTP sent for JE approval. {lin['name']} requested {request.form['action']}."

        if request.form["step"]=="verify":
            cur.execute("SELECT otp FROM requests WHERE id=?",(request.form["rid"],))
            if cur.fetchone()[0]==request.form["otp"]:
                cur.execute("UPDATE requests SET otp_verified=1 WHERE id=?",(request.form["rid"],))
                con.commit()
                msg="OTP verified. Waiting JE approval"
            else:
                msg="Invalid OTP"

    con.close()
    return render_template_string(BASE,body=render_template_string(SSO,linemen=LINEMEN,rid=rid,msg=msg))

@app.route("/je", methods=["GET","POST"])
def je():
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        rid=request.form["rid"]
        decision=request.form["decision"]
        cur.execute("SELECT action,feeder,lineman_name FROM requests WHERE id=?",(rid,))
        action,feeder,lineman=cur.fetchone()
        topic=f"uppcl/feeder{feeder}/cmd"
        now=time.time()

        if decision=="APPROVE":
            if action=="TRIP":
                cur.execute("UPDATE requests SET shutdown_taken=?,je_decision='APPROVED' WHERE id=?",(now,rid))
                con.commit()
                mqtt_client.publish(topic,f"TRIP|{lineman}")
            else:
                cur.execute("UPDATE requests SET shutdown_return=?,je_decision='APPROVED' WHERE id=?",(now,rid))
                con.commit()
                if len(safety_active_lineman(feeder))==0:
                    mqtt_client.publish(topic,f"CLOSE|{lineman}")
        else:
            cur.execute("UPDATE requests SET je_decision='REJECTED' WHERE id=?",(rid,))
            con.commit()

        con.close()
        return redirect("/je")

    cur.execute("SELECT * FROM requests WHERE otp_verified=1 ORDER BY created_at")
    data=cur.fetchall()
    con.close()

    locks={}
    for f in ["1","2"]:
        a=ui_active_lineman(f)
        if a: locks[f]=a

    rows=[]
    for r in data:
        rows.append({
            "id":r[0],"f":r[1],"sso":r[2],"l":r[3],"rs":r[4],
            "st":"TAKEN" if r[5]=="TRIP" else "RETURN",
            "d":ts(r[11]).split(" ")[0],
            "t":" ".join(ts(r[11]).split(" ")[1:]),
            "du":duration(r[8],r[9]),
            "done":r[10] is not None
        })

    return render_template_string(BASE,body=render_template_string(JE,rows=rows,locks=locks))

@app.route("/")
def home():
    return "UPPCL LINEMAN SAFETY SHUTDOWN SERVER RUNNING"

if __name__=="__main__":
    app.run(host="0.0.0.0", port=10000)
