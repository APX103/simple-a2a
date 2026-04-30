import base64
import zlib
import urllib.request

def encode_kroki(data):
    compressed = zlib.compress(data.encode('utf-8'))[2:-4]
    return base64.urlsafe_b64encode(compressed).decode('utf-8')

# Simple test
test = "graph TD; A-->B;"
encoded = encode_kroki(test)
url = f"https://kroki.io/mermaid/png/{encoded}"
print("URL:", url[:80])
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        print(f"OK: {len(data)} bytes")
except Exception as e:
    print(f"FAIL: {e}")
