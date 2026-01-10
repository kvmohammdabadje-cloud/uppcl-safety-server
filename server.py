from flask import Flask, request, render_template_string, redirect
import sqlite3, time, os, random
from datetime import datetime
import requests
import paho.mqtt.client as mqtt

app = Flask(__name__)
DB = "safety.db"

# ================= MQTT =================
MQTT_BROKER = "s871e161.ala.dedicated.gcp.emqxcloud.com"
MQTT_PORT = 1883
MQTT_USER = "UPPCL_SAFETY"
MQTT_PASS = "Lineman@safety123"

mqtt = mqtt.Client()
mqtt.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt.connect(MQTT_BROKER, MQTT_PORT, 60)
mqtt.loop_start()

# ================= OTP =================
OTP_API = "https://2factor.in/API/V1/f830a94b-ed93-11f0-a6b2-0200cd936042/SMS"

LINEMEN = {
    "L1": {"name": "KESHAV", "mobile": "919152225848"},
    "L2": {"name": "RAMESH", "mobile": "919520902397"}
}

# ================= DB =================
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
        otp_verified INTEGER,
        je_decision TEXT,
        created_at REAL
    )
    """)

    con.commit()
    con.close()

init_db()

# ================= HELPERS =================
def fmt(ts):
    return "-" if not ts else datetime.fromtimestamp(ts).strftime("%d-%m-%Y %H:%M:%S")

def duration(start, end):
    if not start:
        return "-"
    if not end:
        return "RUNNING"
    d = int(end - start)
    return f"{d//3600:02d}:{(d%3600)//60:02d}:{d%60:02d}"

# ================= UI =================
BASE = """
<!DOCTYPE html><html><head>
<style>
body{font-family:Arial;background:#eef}
table{width:100%;border-collapse:collapse}
th,td{border:1px solid #555;padding:6px;text-align:center}
th{background:#003366;color:white}
.approve{background:green;color:white}
.reject{background:red;color:white}
</style></head><body>
<h2 align=center>UPPCL OTP Shutdown Safety</h2>
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
            cur.execute("INSERT INTO shutdowns(feeder,lineman,reason) VALUES (?,?,?)",
                        (feeder,lineman["name"],reason))
            sso_id=cur.lastrowid
        else:
            cur.execute("""
            SELECT sso_id FROM shutdowns
            WHERE feeder=? AND lineman=? AND return_time IS NULL
            ORDER BY sso_id DESC LIMIT 1
            """,(feeder,lineman["name"]))
            row=cur.fetchone()
            if not row:
                return "NO ACTIVE SHUTDOWN"
            sso_id=row[0]

        cur.execute("""
        INSERT INTO requests(sso_id,action,otp,otp_verified,created_at)
        VALUES (?,?,?,?,?)
        """,(sso_id,action,otp,0,time.time()))
        con.commit()

        requests.get(f"{OTP_API}/{lineman['mobile']}/{otp}")
        msg=f"OTP SENT | SSO ID: {sso_id:04d}"

    con.close()
    return render_template_string(BASE,content=f"""
    <form method=post>
    Feeder:<select name=feeder><option>1</option><option>2</option></select>
    Action:<select name=action><option>TAKEN</option><option>RETURN</option></select>
    Lineman:<select name=lineman>{''.join(f"<option value={k}>{v['name']}</option>" for k,v in LINEMEN.items())}</select>
    Reason:<input name=reason required>
    <button>Send OTP</button>
    </form><p>{msg}</p>
    """)

# ================= JE =================
@app.route("/je", methods=["GET","POST"])
def je():
    con=db(); cur=con.cursor()

    if request.method=="POST":
        rid=request.form["rid"]
        decision=request.form["decision"]

        cur.execute("SELECT sso_id,action FROM requests WHERE id=?", (rid,))
        sso_id,action=cur.fetchone()

        if decision=="APPROVE":
            if action=="TAKEN":
                cur.execute("UPDATE shutdowns SET taken_time=? WHERE sso_id=?",
                            (time.time(),sso_id))
                mqtt.publish(f"uppcl/feeder{sso_id}/cmd","TRIP")
            else:
                cur.execute("UPDATE shutdowns SET return_time=? WHERE sso_id=?",
                            (time.time(),sso_id))
                mqtt.publish(f"uppcl/feeder{sso_id}/cmd","CLOSE")

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
        html+=f"""
        <tr>
        <td>{r[1]:04d}</td><td>{r[2]}</td><td>{r[3]}</td>
        <td>{r[4]}</td><td>{r[5]}</td>
        <td>{fmt(r[6])}</td><td>{fmt(r[7])}</td>
        <td>{duration(r[6],r[7])}</td>
        <td>
        {'<b>'+r[8]+'</b>' if r[8] else
        f"<form method=post><input type=hidden name=rid value={r[0]}>
        <button class=approve name=decision value=APPROVE>APPROVE</button>
        <button class=reject name=decision value=REJECT>REJECT</button></form>"}
        </td></tr>
        """
    html+="</table>"
    return render_template_string(BASE,content=html)

@app.route("/")
def home():
    return "SERVER RUNNING"

if __name__=="__main__":
    app.run(host="0.0.0.0",port=10000)
