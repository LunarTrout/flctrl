import json
import socket

TCP = ("127.0.0.1", 9787)


def call(cmd, params=None):
    s = socket.create_connection(TCP, timeout=20)
    s.sendall((json.dumps({"op": "call", "cmd": cmd, "params": params or {}}) + "\n").encode())
    d = s.recv(65536)
    s.close()
    return json.loads(d.decode("utf-8", "replace").strip())


def ex(expr):
    r = call("exec", {"expr": expr})
    if not r.get("ok"):
        return "ERR: " + str(r)[:160]
    d = r.get("data") or {}
    if not d.get("ok"):
        return "FLERR: " + str(d.get("error"))[:160]
    return d.get("result")


probes = [
    "hasattr(ui, 'keySeq')",
    "hasattr(mixer, 'focusEditor')",
    "[n for n in dir(ui) if 'key' in n.lower()]",
    "[n for n in dir(mixer) if 'focus' in n.lower() or 'select' in n.lower()]",
    "[n for n in dir(ui) if 'indow' in n or 'show' in n.lower()]",
]
for p in probes:
    print(p, "->", ex(p))
