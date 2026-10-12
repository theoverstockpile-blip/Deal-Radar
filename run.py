"""Start Deal Radar.   python run.py   ->  http://localhost:5050

Env:
  DEALRADAR_INBOX   folder of .eml/.json emails to scan when no email is connected (default ./inbox)
  DEALRADAR_DB      sqlite file (default ./dealradar.db)
  DEALRADAR_TOKEN   optional shared secret; API calls must send it as X-Token (recommended if exposed)
  DEALRADAR_DEV     1 to enable dev-only endpoints (reset)

The actual email source is picked at scan time from Settings > "Connect your email": once an
address and app password are saved there, every scan reads that inbox over IMAP -- no env var or
redeploy needed. Until then it reads DEALRADAR_INBOX, same as always. See dealradar/sources.py.
"""
import os

from dealradar.server import create_app

app = create_app(os.environ.get("DEALRADAR_DB", "dealradar.db"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), threaded=True)
