from flask import Flask, request, render_template_string
import random
import os

app = Flask(__name__)

pending_requests = {}
last_command = "NONE"

# ================= HTML =================
SSO_PAGE = """
<h2>SSO Shutdown Request</h2>
<form method="post">
Feeder:
<select name="feeder">
<option value="F1">Feeder-1</option>
<option value="F2">Feeder-2</option>
</select><br><br>

Action:
<select name="action">
<option value="TRIP">TRIP</option>
<option value="CLOSE">CLOSE</option>
</select><br><br>

Reason:<br>
<input name="reason" required><br><br>

<button type="submit">Generate OTP</button>
</form>

{% if otp %}
<hr>
<b>OTP:</b> {{otp}} <br>
<b>Request ID:</b> {{rid}}
{% endif %}
"""

JE_PAGE = """
<h2>JE Approval Panel</h2>
{% for rid, r in reqs.items() %}
<hr>
<b>Request ID:</b> {{rid}}<br>
Feeder: {{r['feeder']}}<br>
Action: {{r['action']}}<br>
Reason: {{r['reason']}}<br>

<form method="post">
<input type="hidden" name="rid" value="{{rid}}">
<button name="decision" value="APPROVE">APPROVE</button>
<button name="decision" value="REJECT">REJECT</button>
</form>
{% endfor %}
"""

# ================= ROUTES =================
@app.route("/")
def home():
    return "UPPCL SAFETY SERVER RUNNING"

@app.route("/test")
def test():
    return "OK"

@app.route("/sso", methods=["GET", "POST"])
def sso():
    otp = None
    rid = None
    if request.method == "POST":
        feeder = request.form["feeder"]
        action = request.form["action"]
        reason = request.form["reason"]

        otp = random.randint(100000, 999999)
        rid = str(random.randint(1000, 9999))

        pending_requests[rid] = {
            "feeder": feeder,
            "action": action,
            "reason": reason,
            "otp": str(otp)
        }

    return render_template_string(SSO_PAGE, otp=otp, rid=rid)

@app.route("/verify_otp", methods=["POST"])
def verify_otp():
    global last_command
    data = request.json
    rid = data["rid"]
    otp = data["otp"]

    if rid in pending_requests and pending_requests[rid]["otp"] == otp:
        last_command = "WAIT_JE"
        return "OTP_OK"
    return "OTP_FAIL"

@app.route("/je", methods=["GET", "POST"])
def je():
    global last_command
    if request.method == "POST":
        rid = request.form["rid"]
        decision = request.form["decision"]

        if decision == "APPROVE":
            r = pending_requests[rid]
            last_command = f"{r['feeder']}_{r['action']}"
        else:
            last_command = "DENY"

        pending_requests.pop(rid, None)

    return render_template_string(JE_PAGE, reqs=pending_requests)

@app.route("/command")
def command():
    global last_command
    cmd = last_command
    last_command = "NONE"
    return cmd

# ================= MAIN =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
