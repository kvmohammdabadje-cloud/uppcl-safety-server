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

# ================= ACTIVE LINEMAN HELPERS =================
def safety_active_lineman_details(feeder):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
        SELECT lineman_name, action
        FROM requests r1
        WHERE feeder=?
          AND je_decision='APPROVED'
          AND created_at = (
              SELECT MAX(created_at)
              FROM requests r2
              WHERE r2.lineman_name=r1.lineman_name
                AND r2.feeder=r1.feeder
                AND r2.je_decision='APPROVED'
          )
    """, (feeder,))
    active=[]
    for n,a in cur.fetchall():
        if a=="TRIP":
            active.append(n)
    con.close()
    return active, len(active)

# 🔑 LAST TAKEN TIME (FOR DURATION)
def get_last_taken_time(feeder, lineman):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("""
        SELECT shutdown_taken
        FROM requests
        WHERE feeder=? AND lineman_name=?
          AND action='TRIP'
          AND je_decision='APPROVED'
        ORDER BY created_at DESC
        LIMIT 1
    """, (feeder, lineman))
    row = cur.fetchone()
    con.close()
    return row[0] if row else None

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

            active,_ = safety_active_lineman_details(feeder)
            if request.form["action"]=="TRIP" and lineman_name in active:
                msg=f"❌ Lineman {lineman_name} already has an active shutdown."
                con.close()
                return msg

            rid=str(random.randint(1000,9999))
            otp=str(random.randint(100000,999999))

            cur.execute("""
            INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,(rid,feeder,request.form["sso_id"],
                 lineman_name,request.form["reason"],
                 request.form["action"],otp,0,None,None,None,time.time()))
            con.commit()

            requests.get(f"https://2factor.in/API/V1/{OTP_API_KEY}/SMS/{LINEMEN[lineman_key]['mobile']}/{otp}")
            msg="OTP sent to lineman"

        if request.form["step"]=="verify":
            rid=request.form["rid"]
            cur.execute("UPDATE requests SET otp_verified=1 WHERE id=?",(rid,))
            con.commit()
            msg="OTP verified. Waiting for JE approval"

    con.close()
    return msg or "SSO OK"

@app.route("/je", methods=["GET","POST"])
def je():
    con=sqlite3.connect(DB_FILE); cur=con.cursor()

    if request.method=="POST":
        rid=request.form["rid"]
        decision=request.form["decision"]
        cur.execute("SELECT action,feeder,lineman_name FROM requests WHERE id=?",(rid,))
        action,feeder,lineman=cur.fetchone()
        now=time.time()

        if decision=="APPROVE":
            if action=="TRIP":
                cur.execute("UPDATE requests SET shutdown_taken=?,je_decision='APPROVED' WHERE id=?",(now,rid))
                mqtt_client.publish(f"uppcl/feeder{feeder}/cmd","TRIP")
            else:
                active,count=safety_active_lineman_details(feeder)
                if lineman in active:
                    count-=1
                if count==0:
                    cur.execute("UPDATE requests SET shutdown_return=?,je_decision='APPROVED' WHERE id=?",(now,rid))
                    mqtt_client.publish(f"uppcl/feeder{feeder}/cmd","CLOSE")
                else:
                    cur.execute("UPDATE requests SET je_decision='REJECTED' WHERE id=?",(rid,))
        else:
            cur.execute("UPDATE requests SET je_decision='REJECTED' WHERE id=?",(rid,))
        con.commit()
        return redirect("/je")

    cur.execute("SELECT * FROM requests WHERE otp_verified=1 ORDER BY created_at")
    data=cur.fetchall()
    con.close()

    rows=[]
    for r in data:
        dur=""
        if r[5]=="CLOSE" and r[9]:
            taken=get_last_taken_time(r[1], r[3])
            dur=duration(taken, r[9])

        rows.append({
            "id":r[0],
            "date":ts_str(r[11]),
            "feeder":r[1],
            "lineman":r[3],
            "status":"TAKEN" if r[5]=="TRIP" else "RETURN",
            "duration":dur
        })

    return {"JE_ROWS": rows}

@app.route("/")
def home():
    return "UPPCL LINEMAN SAFETY SHUTDOWN SERVER RUNNING"

if __name__=="__main__":
    app.run(host="0.0.0.0", port=10000)
