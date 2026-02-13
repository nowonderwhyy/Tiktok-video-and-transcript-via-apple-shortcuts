## Accessing Your Flask Server From iPhone (Cellular)

This guide explains practical and secure ways to use your Apple Shortcut to POST to `/set_url` and poll `/get_transcription` when your iPhone is off your home Wi‑Fi (on cellular) and your script is running on your home PC.

Your current script already binds to all interfaces (`0.0.0.0`) on port `5000`, so it is reachable on your LAN. For remote access, choose one of the options below.

### Quick Recommendation

- If you want a set‑and‑forget solution with strong security and minimal router fiddling: use Tailscale.
- If you want a public HTTPS URL quickly: use a tunnel (Cloudflare Tunnel or ngrok).
- If you control your router and ISP allows it: use port forwarding + dynamic DNS + TLS.

---

## Option 1: VPN Overlay (Tailscale)

Tailscale creates a private, encrypted mesh network (WireGuard) between your devices. Your iPhone can reach the PC directly, even on cellular.

### Steps
- Install Tailscale on your Windows PC and your iPhone, sign in to the same Tailscale account (tailnet).
- On the PC, note its Tailscale IP (e.g., `100.x.y.z`) or MagicDNS name (e.g., `pcname.tailnet-name.ts.net`).
- Start your Python app (`python tiktokdownload.py`).
- In your Shortcut, change the base URL to:
  - `http://100.x.y.z:5000` or `http://pcname.tailnet-name.ts.net:5000`

### Pros
- Encrypted end‑to‑end; no port forwarding or public exposure.
- Stable addressing with MagicDNS.

### Cons
- Requires the Tailscale app running on both phone and PC.

Optional: Use Tailscale ACLs to restrict who can reach the PC, and enable node sharing if needed.

---

## Option 2: Public HTTPS Tunnel (Cloudflare Tunnel or ngrok)

Tunnels expose `localhost:5000` to the internet via a public HTTPS URL without router changes. Ideal if your ISP uses CGNAT or you can’t port‑forward.

### A) Cloudflare Tunnel (free, stable hostname)
1. Create a Cloudflare account and add a domain (or use a subdomain you control).
2. Install cloudflared on Windows.
3. Authenticate: `cloudflared login` and pick your domain.
4. Create a tunnel and route a hostname to your service:
   - Create a tunnel (via dashboard or CLI) and set an ingress rule to `http://localhost:5000`.
   - Example ingress config:
     ```yaml
     ingress:
       - hostname: tiktok.yourdomain.com
         service: http://localhost:5000
       - service: http_status:404
     ```
5. Run the tunnel as a service.
6. In your Shortcut, use `https://tiktok.yourdomain.com` for both endpoints.

Security add‑ons with Cloudflare:
- Protect endpoints with Cloudflare Access (SSO), IP restrictions, or JWT headers.
- Set caching behavior for static videos if desired.

### B) ngrok (fast start, dynamic URL unless reserved)
1. Install ngrok and set authtoken.
2. Run: `ngrok http 5000`.
3. Copy the `https://<random>.ngrok.io` URL and use it in your Shortcut.
   - For a stable subdomain, reserve a domain with a paid plan or use the ngrok agent with a config.

Pros of tunnels:
- Automatic HTTPS, no port‑forwarding.
- Works behind CGNAT.

Cons:
- Publicly reachable; you must add authentication.
- ngrok free URLs rotate unless reserved.

---

## Option 3: Router Port Forwarding + Dynamic DNS + TLS

Expose your PC to the internet on a chosen port and use a DNS name.

### Steps
1. Give your Windows PC a static LAN IP (or DHCP reservation).
2. Open Windows Firewall for inbound TCP 5000 (or your chosen port) for `python.exe` or the port explicitly.
3. On your router, forward a public TCP port (e.g., 443 or 8443) to `PC_LAN_IP:5000`.
4. Set up dynamic DNS (e.g., DuckDNS) so you have a stable hostname (`yourname.duckdns.org`).
5. Terminate TLS on the PC using a reverse proxy such as Caddy or Nginx:
   - Caddy one‑liner (auto‑TLS):
     ```bash
     caddy reverse-proxy --from yourname.duckdns.org --to 127.0.0.1:5000
     ```
   - Ensure ports 80/443 are forwarded to the PC for certificate issuance.
6. In your Shortcut, use `https://yourname.duckdns.org`.

Notes:
- If your ISP is behind CGNAT, this option won’t work; use tunnels or VPN instead.

---

## Secure Your Endpoints (Highly Recommended)

If you expose the service (tunnel or port‑forward), protect `/set_url` and `/get_transcription` with a token.

### Minimal Bearer Token Example
Add a shared secret token (environment variable `API_TOKEN`) and check it in each route.

```python
import os
from flask import Flask, request, jsonify

API_TOKEN = os.environ.get("API_TOKEN", "change-me")

def require_token():
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {API_TOKEN}"

@app.route('/set_url', methods=['POST'])
def set_url():
    if not require_token():
        return jsonify({"error": "unauthorized"}), 401
    # existing logic...

@app.route('/get_transcription', methods=['GET'])
def get_transcription():
    if not require_token():
        return jsonify({"error": "unauthorized"}), 401
    # existing logic...
```

In your Shortcut, add the header:

```
Authorization: Bearer <your-secret>
```

Additional hardening:
- Restrict methods to only POST/GET as implemented.
- Add simple rate limiting (e.g., with a proxy) and logging.
- Avoid exposing other services on the same host.

---

## Apple Shortcuts: What to Change

- Replace the base URL with your chosen approach:
  - Tailscale: `http://pcname.tailnet-name.ts.net:5000`
  - Cloudflare Tunnel: `https://tiktok.yourdomain.com`
  - ngrok: `https://<random>.ngrok.io`
  - Port‑forward + DDNS: `https://yourname.duckdns.org`
- Add `Authorization: Bearer <token>` header to both requests.
- Keep your existing flow:
  - POST to `/set_url` with JSON body `{ "url": "..." }`.
  - Poll `/get_transcription` until it returns a non‑"..." `transcription` and a `video_url`.
  - Download `video_url` to save the MP4; save `transcription` to a note/file.

Tip: Ensure your Shortcut follows redirects (tunnels may redirect HTTP→HTTPS). Use HTTPS for public endpoints.

---

## Testing Checklist

- Turn off Wi‑Fi on the iPhone to ensure cellular testing.
- Verify the endpoint is reachable in Safari first.
- Confirm the `video_url` in the JSON loads in the browser and downloads a file.
- Watch your Python console for logs while testing.

---

## Troubleshooting

- CGNAT/No public IP: use Tailscale or a tunnel.
- Windows Firewall blocks: add an inbound rule for the port or `python.exe`.
- Certificate issues: if using your own domain, ensure 80/443 are forwarded to the proxy box; check DNS propagation.
- Tunnel not mapping static files: ensure your tunnel/proxy forwards all paths, including `/static/videos/...`.
- Shortcut times out: increase timeouts, reduce polling interval, or ensure your PC isn’t sleeping.

---

## Security Notes

- Prefer private VPN (Tailscale) for the simplest secure setup.
- If going public, always use HTTPS and an auth layer (token, Cloudflare Access, etc.).
- Treat `static/videos/` as public when exposed; don’t put sensitive data there.

