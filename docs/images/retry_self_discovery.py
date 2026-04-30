import json
import urllib.request

def fetch_kroki_post(diagram_type, source_text, output_path, fmt='png'):
    url = f"https://kroki.io/{diagram_type}/{fmt}"
    payload = json.dumps({"diagram_source": source_text}).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'image/png'
        },
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
            with open(output_path, 'wb') as f:
                f.write(data)
        print(f"OK: {output_path} ({len(data)} bytes)")
        return True
    except Exception as e:
        print(f"FAIL: {output_path} - {e}")
        return False

src = open('sequence_self_discovery_clean.mmd').read()
fetch_kroki_post('mermaid', src, 'sequence_self_discovery.png')
print("Done.")
