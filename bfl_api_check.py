import json, httpx

# Fetch the openapi spec and find the get_result endpoint details
resp = httpx.get("https://api.bfl.ai/openapi.json", timeout=15)
spec = resp.json()

# Find get_result endpoint
for path, methods in spec.get("paths", {}).items():
    for method, info in methods.items():
        if "get_result" in info.get("operationId", ""):
            print(f"Endpoint: {method.upper()} {path}")
            print(f"Summary: {info.get('summary')}")
            print(f"Security: {info.get('security')}")
            params = info.get("parameters", [])
            for p in params:
                print(f"  Param: {p.get('name')} (in={p.get('in')}, required={p.get('required')})")
                schema = p.get("schema", {})
                print(f"    Schema: {schema}")
            print(f"Responses: {json.dumps(info.get('responses', {}), indent=2)[:500]}")
            print()

# Also print the ResultResponse schema
schemas = spec.get("components", {}).get("schemas", {})
for name in ["ResultResponse", "AsyncResponse"]:
    if name in schemas:
        print(f"\n=== {name} schema ===")
        print(json.dumps(schemas[name], indent=2))
