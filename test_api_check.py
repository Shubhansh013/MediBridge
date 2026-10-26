import sys, os
sys.path.insert(0, os.getcwd())

from api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

# Test /api/medicines/search?q=paracetamol
resp1 = client.get("/api/medicines/search?q=paracetamol")
print(f"GET /api/medicines/search?q=paracetamol")
print(f"  Status: {resp1.status_code}")
data1 = resp1.json()
print(f"  Count: {len(data1)}")
for d in data1:
    print(f"  - brand={d['brand_name']}, generic={d['generic_name']}, strength={d['strength']}, form={d['form']}")

print()

# Test /api/medicines/ (list all)
resp2 = client.get("/api/medicines/")
print(f"GET /api/medicines/")
print(f"  Status: {resp2.status_code}")
data2 = resp2.json()
print(f"  Count: {len(data2)}")
for d in data2:
    print(f"  - brand={d['brand_name']}, generic={d['generic_name']}, strength={d['strength']}, form={d['form']}")