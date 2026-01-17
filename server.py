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
    d = int(end - start)
    return f"{d//60} min {d%60} sec"

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

# ================= HELPERS =================
def lineman_has_active_shutdown(feeder, lineman):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
        SELECT action FROM requests
        WHERE feeder=? AND lineman_name=? AND je_decision='APPROVED'
        ORDER BY created_at DESC LIMIT 1
    """, (feeder, lineman))
    row = cur.fetchone()
    con.close()
    return row and row[0] == "TRIP"

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
    active = [n for n,a in cur.fetchall() if a == "TRIP"]
    con.close()
    return active, len(active)

def ui_lineman_status(feeder):
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
    rows = cur.fetchall()
    con.close()
    return rows

def last_taken_time(feeder, lineman):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
        SELECT shutdown_taken FROM requests
        WHERE feeder=? AND lineman_name=? AND action='TRIP'
          AND je_decision='APPROVED'
        ORDER BY created_at DESC LIMIT 1
    """, (feeder, lineman))
    row = cur.fetchone()
    con.close()
    return row[0] if row else None

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
.lock{background:#fff3cd;border:1px solid #ffcc00;padding:10px;margin-bottom:10px}
</style>
<script>
function disableButtons(form){
  form.querySelectorAll("button").forEach(b => b.disabled=true);
}
</script>
</head>
<body>
<div class="header">PROJECT :- UPPCL LINEMAN SAFETY SHUTDOWN</div>
<div class="container">{{ content | safe }}</div>
</body>
</html>
"""

# ================= SSO =================
SSO_HTML = """
<h2>SSO DASHBOARD</h2>
<form method="post">
<input type="hidden" name="step" value="send">
SSO ID:<br><input name="sso_id" required><br><br>
Feeder:<br><select name="feeder"><option value="1">FEEDER 1</option><option value="2">FEEDER 2</option></select><br><br>
Action:<br><select name="action"><option value="TRIP">TAKEN</option><option value="CLOSE">RETURN</option></select><br><br>
Lineman:<br><select name="lineman">{% for k,l in linemen.items() %}<option value="{{k}}">{{l.name}}</option>{% endfor %}</select><br><br>
Reason:<br><input name="reason" required><br><br>
<button type="submit">SEND OTP</button>
</form>

{% if rid %}
<hr>
Shutdown ID: <b>{{rid}}</b>
<form method="post">
<input type="hidden" name="step" value="verify">
<input type="hidden" name="rid" value="{{rid}}">
OTP:<br><input name="otp" required>
<button type="submit">VERIFY OTP</button>
</form>
{% endif %}
<p><b>{{msg}}</b></p>
"""

# ================= JE =================
JE_HTML = """
<h2>JE DASHBOARD</h2>

{% for feeder,items in status.items() %}
<div class="lock">
🔒 FEEDER {{feeder}} STATUS:<br>
{% for n,a in items %}
{{"🟢" if a=="TRIP" else "🔵"}} {{n}} — {{ "TAKEN" if a=="TRIP" else "RETURN" }}<br>
{% endfor %}
</div>
{% endfor %}

<table>
<tr><th>DATE</th><th>TIME</th><th>SSO</th><th>FEEDER</th><th>LINEMAN</th><th>STATUS</th><th>REASON</th><th>APPROVE</th><th>REJECT</th><th>DURATION</th></tr>
{% for r in rows %}
<tr>
<td>{{r.date}}</td><td>{{r.time}}</td><td>{{r.sso}}</td>
<td>{{r.feeder}}</td><td>{{r.lineman}}</td><td>{{r.status}}</td>
<td>{{r.reason}}</td>
<td><form method="post" onsubmit="disableButtons(this)">
<input type="hidden" name="rid" value="{{r.id}}">
<button class="btn-approve" {% if r.decided %}disabled{% endif %} name="decision" value="APPROVE">APPROVE</button>
</form></td>
<td><form method="post" onsubmit="disableButtons(this)">
<input type="hidden" name="rid" value="{{r.id}}">
<button class="btn-reject" {% if r.decided %}disabled{% endif %} name="decision" value="REJECT">REJECT</button>
</form></td>
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
        if request.form["step"]=="send":
            feeder=request.form["feeder"]
            lin_key=request.form["lineman"]
            lin_name=LINEMEN[lin_key]["name"]

            if request.form["action"]=="TRIP" and lineman_has_active_shutdown(feeder, lin_name):
                msg=f"❌ {lin_name} already has an active shutdown."
                con.close()
                return render_template_string(BASE_HTML,content=render_template_string(SSO_HTML,linemen=LINEMEN,msg=msg))

            rid=str(random.randint(1000,9999))
            otp=str(random.randint(100000,999999))
            lin=LINEMEN[lin_key]

            cur.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid,feeder,request.form["sso_id"],lin["name"],request.form["reason"],
                 request.form["action"],otp,0,None,None,None,time.time()))
            con.commit()
            requests.get(f"https://2factor.in/API/V1/{OTP_API_KEY}/SMS/{lin['mobile']}/{otp}")
            msg=f"OTP sent to JE. {lin['name']} requesting {request.form['action']} of Feeder {feeder}"

        if request.form["step"]=="verify":
            cur.execute("SELECT otp FROM requests WHERE id=?",(request.form["rid"],))
            if cur.fetchone()[0]==request.form["otp"]:
                cur.execute("UPDATE requests SET otp_verified=1 WHERE id=?",(request.form["rid"],))
                con.commit()
                msg="OTP verified. Waiting for JE approval"
            else:
                msg="Invalid OTP"

    con.close()
    return render_template_string(BASE_HTML,content=render_template_string(SSO_HTML,linemen=LINEMEN,rid=rid,msg=msg))

@app.route("/je", methods=["GET","POST"])
def je():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()

    if request.method == "POST":
        rid = request.form.get("rid")
        decision = request.form.get("decision")

        if not rid or decision not in ["APPROVE", "REJECT"]:
            con.close()
            return redirect("/je")

        cur.execute(
            "SELECT action, feeder, lineman_name FROM requests WHERE id=?",
            (rid,)
        )
        row = cur.fetchone()
        if not row:
            con.close()
            return redirect("/je")

        action, feeder, lineman = row
        now = time.time()

        if decision == "APPROVE":
            if action == "TRIP":
                cur.execute("""
                    UPDATE requests
                    SET shutdown_taken=?, je_decision='APPROVED'
                    WHERE id=? AND je_decision IS NULL
                """, (now, rid))
                mqtt_client.publish(f"uppcl/feeder{feeder}/cmd", "TRIP")

            else:  # RETURN
                cur.execute("""
                    UPDATE requests
                    SET shutdown_return=?, je_decision='APPROVED'
                    WHERE id=? AND je_decision IS NULL
                """, (now, rid))

                active, _ = safety_active_lineman_details(feeder)
                if len(active) == 0:
                    mqtt_client.publish(f"uppcl/feeder{feeder}/cmd", "CLOSE")

        else:
            cur.execute("""
                UPDATE requests
                SET je_decision='REJECTED'
                WHERE id=? AND je_decision IS NULL
            """, (rid,))

        con.commit()
        con.close()
        return redirect("/je")

    cur.execute("SELECT * FROM requests WHERE otp_verified=1 ORDER BY created_at")
    data=cur.fetchall()
    con.close()

    rows=[]
    status={}
    for f in ["1","2"]:
        s=ui_lineman_status(f)
        if s: status[f]=s

    for r in data:
        dur=""
        if r[5]=="CLOSE" and r[9]:
            t=last_taken_time(r[1],r[3])
            dur=duration(t,r[9])
        rows.append({
            "id":r[0],"feeder":r[1],"sso":r[2],"lineman":r[3],
            "reason":r[4],"status":"TAKEN" if r[5]=="TRIP" else "RETURN",
            "date":ts_str(r[11]).split()[0],
            "time":" ".join(ts_str(r[11]).split()[1:]),
            "duration":dur,"decided":r[10] is not None
        })

    return render_template_string(BASE_HTML,content=render_template_string(JE_HTML,rows=rows,status=status))

@app.route("/")
def home():
    return "UPPCL LINEMAN SAFETY SHUTDOWN SERVER RUNNING"

if __name__=="__main__":
    app.run(host="0.0.0.0", port=10000)

