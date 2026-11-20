# ClipCraft Hosting Guide

## Option 1: Share with Friend on Same Network (Easiest)

If your friend is on the same WiFi network:

1. Start the server:
   ```bash
   python server.py
   ```

2. Find your local IP address:
   - Windows: Open Command Prompt, run `ipconfig`, look for "IPv4 Address"
   - Example: `192.168.1.100`

3. Share this URL with your friend:
   ```
   http://YOUR_IP:5003
   ```

4. Your friend opens this in their browser.

**Note:** The server currently only allows localhost. You'll need to modify the security check.

---

## Option 2: Tunneling Service (ngrok / cloudflared) - RECOMMENDED

This gives you a public URL without hosting. Perfect for sharing with friends.

### Using ngrok:

1. Install ngrok: https://ngrok.com/download

2. Start your ClipCraft server:
   ```bash
   python server.py
   ```

3. In a new terminal, run:
   ```bash
   ngrok http 5003
   ```

4. ngrok will give you a public URL like:
   ```
   https://abc123.ngrok.io
   ```

5. Share this URL with your friend.

### Using cloudflared (free, no account needed):

1. Install cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

2. Start your ClipCraft server:
   ```bash
   python server.py
   ```

3. In a new terminal, run:
   ```bash
   cloudflared tunnel --url http://localhost:5003
   ```

4. cloudflared will give you a public URL like:
   ```
   https://random-words.trycloudflare.com
   ```

5. Share this URL with your friend.

**Pros:** Free, no hosting costs, easy to set up
**Cons:** Requires your computer to be running, URL changes each time

---

## Option 3: Cloud Hosting (Render, Railway, Heroku)

For permanent hosting that works 24/7.

### Render (Free tier available):

1. Create a Render account: https://render.com

2. Connect your GitHub repository

3. Create a new Web Service:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python server.py`
   - Environment: Python 3

4. Add environment variables if needed

5. Deploy and get a public URL like:
   ```
   https://clipcraft.onrender.com
   ```

### Railway (Free tier available):

1. Create a Railway account: https://railway.app

2. Install Railway CLI:
   ```bash
   npm install -g @railway/cli
   ```

3. Login and deploy:
   ```bash
   railway login
   railway init
   railway up
   ```

4. Get your public URL from the Railway dashboard.

### Important: Update Security for Cloud Hosting

The current server only allows localhost. For cloud hosting, you need to update `server.py`:

Find this line (around line 29):
```python
def protect_local_server():
    if request.host.split(":")[0].lower() not in ("127.0.0.1", "localhost"):
        return _error("This studio is available on localhost only.", 403)
```

Replace with:
```python
def protect_local_server():
    # Allow all hosts for cloud deployment
    # You can add specific domains if needed
    pass
```

Or for better security, allow specific domains:
```python
ALLOWED_HOSTS = {"clipcraft.onrender.com", "your-domain.com"}

def protect_local_server():
    host = request.host.split(":")[0].lower()
    if host not in ALLOWED_HOSTS and host not in ("127.0.0.1", "localhost"):
        return _error("Access denied.", 403)
```

---

## Option 4: VPS Hosting (DigitalOcean, AWS, etc.)

For full control and better performance.

1. Rent a VPS (e.g., DigitalOcean droplet, $5/month)

2. SSH into your server

3. Install Python and dependencies:
   ```bash
   sudo apt update
   sudo apt install python3 python3-pip
   git clone YOUR_REPO_URL
   cd clipcraft
   pip3 install -r requirements.txt
   ```

4. Run with a process manager (gunicorn):
   ```bash
   pip3 install gunicorn
   gunicorn -w 4 -b 0.0.0.0:5003 server:app
   ```

5. Set up Nginx as a reverse proxy (optional but recommended)

6. Use a domain or your server's IP:
   ```
   http://YOUR_SERVER_IP:5003
   ```

---

## Mobile Responsiveness

Your app is already responsive! I just made additional improvements:

- Better mobile header layout
- Improved button spacing on small screens
- Full-width clip actions on mobile
- Better modal layouts for mobile
- Touch-friendly button sizes

Your friend can use it on mobile browsers. The app works best on:
- Modern smartphones (iPhone 12+, Android 10+)
- Tablets
- Desktop browsers

---

## Quick Start for Your Friend

**Easiest method (ngrok):**

1. You run: `python server.py`
2. You run: `ngrok http 5003`
3. You send your friend the ngrok URL
4. They open it on their phone or computer

**For permanent hosting:**

1. Deploy to Render or Railway
2. Update security in `server.py`
3. Send your friend the permanent URL

---

## Security Notes

- The app processes video locally, so it's safe
- API keys are stored locally in settings.json
- For cloud hosting, consider adding authentication
- Don't expose your API keys in public repos
- Use HTTPS for production (Render/Railway provide this automatically)

---

## Troubleshooting

**Friend can't access:**
- Check if server is running
- Check firewall settings
- For ngrok, check if tunnel is active

**Mobile issues:**
- Clear browser cache
- Try a different browser
- Check internet connection

**Upload issues:**
- Large files may timeout on slow connections
- Consider compressing videos before upload
- Check file size limits (2GB max)
