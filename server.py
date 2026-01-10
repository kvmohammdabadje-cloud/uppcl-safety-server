from flask import Flask, request, redirect, render_template_string
import sqlite3, time, random, os
from datetime import datetime
import requests
import paho.mqtt.client as mqtt

# ================= CONFIG =================
DB = "safety.db"

MQTT_BROKER = "s871e161.ala.dedicated.gcp.emqxcloud.com"
MQTT_PORT = 1883
MQTT_USER = "UPPCL_SAFETY"
MQTT_PASS = "Lineman@safety123"

OTP_API_KEY = "f830a94b-ed93-11f0-a6b2-0200cd936042"
OTP_URL = f"https://2factor.in/API/V1/{OTP_API_KEY}/SMS"

LINEMEN = {
    "L1": {"name": "KESHAV", "mobile": "919152225848"},
    "L2": {"name": "RAMESH", "mobile": "919520902397"}
}

# ================= APP =================
app = Flask(__name__)

# ================= MQTT =================
mqttc = mqtt.Client()
mqttc.username_pw_set(MQTT_USER, MQTT_PASS)
mqttc.connect(MQTT_BROKER, MQTT_PORT, 60)
mqttc.loop_start()

# ================= DATABASE =================
def db():
    return sqlite3.connect(DB)

def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS shutdowns(
        sso_id INTEGER PRIMARY KEY AUTOINCREMENT,
        feeder TEXT,
        lineman TEXT,
        reason TEXT,
        taken_time REAL,
        return_time REAL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sso_id INTEGER,
        action TEXT,
        otp TEXT,
        otp_verified INTEGER DEFAULT 0,
        je_decision TEXT,
        created_at REAL
    )
    """)
    con.commit()
    con.close()

init_db()

# ================= HELPERS =================
def ts(t):
    return "-" if not t else datetime.fromtimestamp(t).strftime("%d-%m-%Y %H:%M:%S")

def duration(a, b):
    if not a:
        return "-"
    if not b:
        return "RUNNING"
    d = int(b - a)
    return f"{d//3600:02d}:{(d%3600)//60:02d}:{d%60:02d}"

# ================= UI =================
BASE = """
<!DOCTYPE html><html><head>
<style>
body{font-family:Arial;background:#eef}
table{width:100%;border-collapse:collapse}
th,td{border:1px solid #444;padding:6px;text-align:center}
th{background:#003366;color:white}
.approve{background:green;color:white;padding:6px}
.reject{background:red;color:white;padding:6px}
.disabled{opacity:0.5;pointer-events:none}
</style></head><body>
<h2 align=center>UPPCL OTP Based Shutdown Safety System</h2>
{{content|safe}}
</body></html>
"""

# ================= SSO =================
@app.route("/sso", methods=["GET","POST"])
def sso():
    msg=""
    con=db(); cur=con.cursor()

    if request.method=="POST":
        feeder=request.form["feeder"]
        action=request.form["action"]
        lineman=LINEMEN[request.form["lineman"]]
        reason=request.form["reason"]
        otp=str(random.randint(100000,999999))

        if action=="TAKEN":
            cur.execute(
                "INSERT INTO shutdowns(feeder,lineman,reason) VALUES (?,?,?)",
                (feeder,lineman["name"],reason)
            )
            sso_id = cur.lastrowid
        else:
            cur.execute("""
            SELECT sso_id FROM shutdowns
            WHERE feeder=? AND lineman=? AND return_time IS NULL
            ORDER BY sso_id DESC LIMIT 1
            """,(feeder,lineman["name"]))
            row=cur.fetchone()
            if not row:
                return "NO ACTIVE SHUTDOWN FOUND"
            sso_id=row[0]

        cur.execute("""
        INSERT INTO requests(sso_id,action,otp,created_at)
        VALUES (?,?,?,?)
        """,(sso_id,action,otp,time.time()))
        con.commit()

        requests.get(f"{OTP_URL}/{lineman['mobile']}/{otp}")
        msg=f"OTP SENT | SSO ID: {sso_id:04d}"

    con.close()

    return render_template_string(BASE, content=f"""
    <form method=post>
    Feeder:<select name=feeder><option>1</option><option>2</option></select>
    Action:<select name=action><option>TAKEN</option><option>RETURN</option></select>
    Lineman:<select name=lineman>
    {''.join(f"<option value={k}>{v['name']}</option>" for k,v in LINEMEN.items())}
    </select>
    Reason:<input name=reason required>
    <button>Send OTP</button>
    </form>
    <p>{msg}</p>
    """)

# ================= OTP VERIFY =================
@app.route("/verify", methods=["POST"])
def verify():
    otp=request.form["otp"]
    sso_id=request.form["sso_id"]

    con=db(); cur=con.cursor()
    cur.execute("""
    UPDATE requests SET otp_verified=1
    WHERE sso_id=? AND otp=? AND otp_verified=0
    """,(sso_id,otp))
    con.commit(); con.close()
    return "OTP VERIFIED – WAIT FOR JE"

# ================= JE =================
@app.route("/je", methods=["GET","POST"])
def je():
    con=db(); cur=con.cursor()

    if request.method=="POST":
        rid=request.form["rid"]
        decision=request.form["decision"]

        cur.execute("SELECT sso_id,action FROM requests WHERE id=?", (rid,))
        sso_id,action = cur.fetchone()

        if decision=="APPROVE":
            now=time.time()
            if action=="TAKEN":
                cur.execute("UPDATE shutdowns SET taken_time=? WHERE sso_id=?",(now,sso_id))
                mqttc.publish(f"uppcl/feeder{sso_id}/cmd","TRIP")
            else:
                cur.execute("UPDATE shutdowns SET return_time=? WHERE sso_id=?",(now,sso_id))
                mqttc.publish(f"uppcl/feeder{sso_id}/cmd","CLOSE")

        cur.execute("UPDATE requests SET je_decision=? WHERE id=?",(decision,rid))
        con.commit()
        return redirect("/je")

    cur.execute("""
    SELECT r.id,s.sso_id,s.feeder,s.lineman,r.action,
           s.reason,s.taken_time,s.return_time,r.je_decision
    FROM requests r
    JOIN shutdowns s ON r.sso_id=s.sso_id
    WHERE r.otp_verified=1
    ORDER BY r.created_at DESC
    """)
    rows=cur.fetchall()
    con.close()

    html="<table><tr><th>SSO ID</th><th>Feeder</th><th>Lineman</th><th>Action</th><th>Reason</th><th>Taken</th><th>Return</th><th>Duration</th><th>JE</th></tr>"

    for r in rows:
        html+="<tr>"
        html+=f"<td>{r[1]:04d}</td><td>{r[2]}</td><td>{r[3]}</td><td>{r[4]}</td><td>{r[5]}</td>"
        html+=f"<td>{ts(r[6])}</td><td>{ts(r[7])}</td><td>{duration(r[6],r[7])}</td>"
        if r[8]:
            html+=f"<td class='disabled'>{r[8]}</td>"
        else:
            html+="<td><form method=post>"
            html+=f"<input type=hidden name=rid value='{r[0]}'>"
            html+="<button class=approve name=decision value=APPROVE>APPROVE</button> "
            html+="<button class=reject name=decision value=REJECT>REJECT</button>"
            html+="</form></td>"
        html+="</tr>"

    html+="</table>"
    return render_template_string(BASE,content=html)

@app.route("/")
def home():
    return "UPPCL SAFETY SERVER RUNNING"

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0",port=port)
