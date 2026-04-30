import base64
import zlib
import urllib.request
import os

def encode_kroki(data):
    """Kroki encoding: deflate then base64 (URL-safe)"""
    compressed = zlib.compress(data.encode('utf-8'))[2:-4]
    return base64.urlsafe_b64encode(compressed).decode('utf-8').replace('+', '-').replace('/', '_')

def fetch_kroki(diagram_type, source_text, output_path, fmt='png'):
    encoded = encode_kroki(source_text)
    url = f"https://kroki.io/{diagram_type}/{fmt}/{encoded}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            with open(output_path, 'wb') as f:
                f.write(data)
        print(f"OK: {output_path} ({len(data)} bytes)")
        return True
    except Exception as e:
        print(f"FAIL: {output_path} - {e}")
        return False

# 1. Graphviz architecture
graphviz_src = open('../architecture.dot').read()
fetch_kroki('graphviz', graphviz_src, 'architecture.png')

# 2-5. Mermaid sequence diagrams
mermaid_files = [
    ('../sequence_p2p.md', 'sequence_p2p.png'),
    ('../sequence_group.md', 'sequence_group.png'),
    ('../sequence_human_confirm.md', 'sequence_human_confirm.png'),
    ('../sequence_self_discovery.md', 'sequence_self_discovery.png'),
]

for md_path, out_name in mermaid_files:
    content = open(md_path).read()
    # Extract mermaid block
    lines = content.split('\n')
    in_mermaid = False
    mermaid_lines = []
    for line in lines:
        if line.strip().startswith('```mermaid'):
            in_mermaid = True
            continue
        if in_mermaid and line.strip() == '```':
            break
        if in_mermaid:
            mermaid_lines.append(line)
    mermaid_src = '\n'.join(mermaid_lines)
    fetch_kroki('mermaid', mermaid_src, out_name)

print("Done.")
