"""Obtain a DIRIGERA local API token (PKCE flow).

Usage: python scripts/dirigera_token.py <hub-ip> <output-file>
Then press the action button on the bottom of the hub within 5 minutes. The
token is written to <output-file>; copy it into .env as DIRIGERA_TOKEN.
Find the hub's IP with: dns-sd -B _ihsp._tcp local.  (then dns-sd -L <name> _ihsp._tcp)
"""
import base64, hashlib, secrets, socket, string, sys, time
import httpx

HOST = sys.argv[1]
BASE = f"https://{HOST}:8443/v1"
OUT = sys.argv[2]

verifier = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(128))
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")

with httpx.Client(verify=False, timeout=10) as c:
    deadline = time.time() + 300
    while time.time() < deadline:
        r = c.get(f"{BASE}/oauth/authorize", params={
            "audience": "homesmart.local", "response_type": "code",
            "code_challenge": challenge, "code_challenge_method": "S256"})
        r.raise_for_status()
        code = r.json()["code"]
        print("Got auth code, waiting for button press...", flush=True)
        code_deadline = min(deadline, time.time() + 55)
        while time.time() < code_deadline:
            t = c.post(f"{BASE}/oauth/token", data={
                "code": code, "name": socket.gethostname(),
                "grant_type": "authorization_code", "code_verifier": verifier})
            if t.status_code == 200:
                with open(OUT, "w") as f:
                    f.write(t.json()["access_token"])
                print("TOKEN OBTAINED", flush=True)
                sys.exit(0)
            time.sleep(2)
    print("Timed out without a button press", flush=True)
    sys.exit(1)
