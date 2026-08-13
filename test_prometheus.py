import requests
r = requests.get(
    "http://192.168.138.137:9090/api/v1/query",
    params={"query": 'namedprocess_namegroup_memory_bytes{groupname="dockerd",node="linux-vm2"}'},
    timeout=5,
)
print(r.status_code)
print(r.json())
