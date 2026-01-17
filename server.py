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
mqtt = mqtt.Client()
mqtt.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt.loop_start()

# ================= DB =================
def init_db():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS shutdowns(
        id TEXT PRIMARY KEY,
        feeder TEXT,
        sso_id TEXT,
        lineman TEXT,
        action TEXT,
        state TEXT,
        reason TEXT,
        otp TEXT,
        otp_verified INTEGER,
        je_decision TEXT,
        taken_time REAL,
        return_time REAL,
        created REAL
    )
    """)
    con.commit()
    con.close()

init_db()

# ================= HELPERS =================
def now():
    return time.time()

def ts(t):
    return datetime.fromtimestamp(t, IST).strftime("%d/%m/%Y %I:%M %p")

def duration(a, b):
    if not a or not b:
        return ""
    d = int(b - a)
    return f"{d//60} min {d%60} sec"

def lineman_active(feeder, lineman):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
        SELECT state FROM shutdowns
        WHERE feeder=? AND lineman=?
        ORDER BY created DESC LIMIT 1
    """, (feeder, lineman))
    r = cur.fetchone()
    con.close()
    return r and r[0] == "TAKEN"

def feeder_active_count(feeder):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM shutdowns
        WHERE feeder=? AND state='TAKEN' AND je_decision='APPROVED'
    """, (feeder,))
    c = cur.fetchone()[0]
    con.close()
    return c

# ================= UI =================
BASE = """
<!DOCTYPE html>
<html>
<head>
<style>
body{font-family:Arial;background:#eef2f6}
.header{background:#003366;color:white;padding:15px;text-align:center;font-size:22px}
.container{width:95%;margin:auto;background:white;padding:20px}
table{width:100%;border-collapse:collapse}
th,td{border:1px solid #555;padding:6px;text-align:center}
th{background:#cfe2f3}
button{padding:6px}
button:disabled{opacity:.4;filter:blur(1px)}
</style>
<script>
function lock(){document.querySelectorAll("button").forEach(b=>b.disabled=true);}
</script>
</head>
<body>
<div class="header">UPPCL LINEMAN SAFETY SHUTDOWN</div>
<div class="container">{{c|safe}}</div>
</body>
</html>
"""

# ================= SSO =================
SSO = """
<h2>SSO DASHBOARD</h2>
<form method="post">
SSO ID:<br><input name="sso"><br><br>
Feeder:<br><select name="feeder"><option>1</option><option>2</option></select><br><br>
Lineman:<br><select name="lineman">{% for l in linemen %}<option>{{l}}</option>{% endfor %}</select><br><br>
Action:<br><select name="action"><option>TAKEN</option><option>RETURN</option></select><br><br>
Reason:<br><input name="reason"><br><br>
<button>REQUEST</button>
</form>
<p>{{msg}}</p>
"""

@app.route("/sso", methods=["GET","POST"])
def sso():
    msg=""
    if request.method=="POST":
        feeder=request.form["feeder"]
        lineman=request.form["lineman"]
        action=request.form["action"]

        if action=="TAKEN" and lineman_active(feeder, lineman):
            msg="❌ Lineman already has active shutdown"
        else:
            rid=str(random.randint(1000,9999))
            otp=str(random.randint(100000,999999))
            con=sqlite3.connect(DB_FILE)
            cur=con.cursor()
            cur.execute("""
                INSERT INTO shutdowns VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,(rid,feeder,request.form["sso"],lineman,action,
                 "TAKEN" if action=="TAKEN" else "RETURNED",
                 request.form["reason"],otp,1,None,None,None,now()))
            con.commit(); con.close()
            msg="Request sent to JE"

    return render_template_string(BASE,
        c=render_template_string(SSO,linemen=[l["name"] for l in LINEMEN.values()],msg=msg))

# ================= JE =================
JE = """
<h2>JE DASHBOARD</h2>
<table>
<tr><th>ID</th><th>Feeder</th><th>Lineman</th><th>State</th><th>Approve</th><th>Reject</th></tr>
{% for r in rows %}
<tr>
<td>{{r[0]}}</td><td>{{r[1]}}</td><td>{{r[3]}}</td><td>{{r[5]}}</td>
<td>
<form method="post" onsubmit="lock()">
<input type="hidden" name="id" value="{{r[0]}}">
<button {% if r[9] %}disabled{% endif %} name="d" value="A">APPROVE</button>
</form>
</td>
<td>
<form method="post" onsubmit="lock()">
<input type="hidden" name="id" value="{{r[0]}}">
<button {% if r[9] %}disabled{% endif %} name="d" value="R">REJECT</button>
</form>
</td>
</tr>
{% endfor %}
</table>
"""

@app.route("/je", methods=["GET","POST"])
def je():
    con=sqlite3.connect(DB_FILE)
    cur=con.cursor()

    if request.method=="POST":
        rid=request.form["id"]
        d=request.form["d"]

        cur.execute("SELECT feeder,action FROM shutdowns WHERE id=?", (rid,))
        feeder,action=cur.fetchone()

        if d=="A":
            cur.execute("""
                UPDATE shutdowns SET je_decision='APPROVED',
                taken_time=CASE WHEN action='TAKEN' THEN ? ELSE taken_time END,
                return_time=CASE WHEN action='RETURN' THEN ? ELSE return_time END
                WHERE id=? AND je_decision IS NULL
            """,(now(),now(),rid))

            if action=="TAKEN":
                mqtt.publish(f"uppcl/feeder{feeder}/cmd","TRIP")
            else:
                if feeder_active_count(feeder)==0:
                    mqtt.publish(f"uppcl/feeder{feeder}/cmd","CLOSE")
        else:
            cur.execute("UPDATE shutdowns SET je_decision='REJECTED' WHERE id=?", (rid,))

        con.commit()

    cur.execute("SELECT * FROM shutdowns ORDER BY created DESC")
    rows=cur.fetchall()
    con.close()

    return render_template_string(BASE,
        c=render_template_string(JE,rows=rows))

@app.route("/")
def home():
    return "SERVER RUNNING"

if __name__=="__main__":
    app.run(host="0.0.0.0", port=10000)
