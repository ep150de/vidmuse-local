import json, os, sys
sys.path.insert(0, "/home/lingleblater/.hermes/hermes-agent")
os.environ["NOUS_TOOLS_ENABLED"] = "1"
os.environ["MANAGED_NOUS_TOOLS"] = "1"

from tools.managed_tool_gateway import resolve_managed_tool_gateway, peek_nous_access_token, read_nous_access_token
from tools.tool_backend_helpers import managed_nous_tools_enabled

print("managed_nous_tools_enabled:", managed_nous_tools_enabled())

token = peek_nous_access_token()
print("peek_nous_access_token:", "SET" if token else "None")

cfg = resolve_managed_tool_gateway("bfl")
if cfg:
    print("gateway_origin:", cfg.gateway_origin)
    print("vendor:", cfg.vendor)
else:
    print("resolve_managed_tool_gateway(bfl) -> None")

# Also try reading the token via read_nous_access_token (which may refresh)
token2 = read_nous_access_token()
print("read_nous_access_token:", "SET" if token2 else "None")

# Try checking BFL API key in env
print("BFL_API_KEY env:", "SET" if os.getenv("BFL_API_KEY") else "None")
print("OPENAI_API_KEY env:", "SET" if os.getenv("OPENAI_API_KEY") else "None")
