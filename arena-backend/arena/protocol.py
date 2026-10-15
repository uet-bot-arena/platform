import json

VERSION = 2


class Fault(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("Duplicate JSON key")
            out[key] = value
        return out
    def constant(_):
        raise ValueError("Non-finite JSON number")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Fault("protocol_error", "Invalid UTF-8 JSON: " + str(exc)) from exc


def response(raw, expected, kind="action"):
    msg = strict_json(raw)
    fields = {"protocol_version", "type", "match_id"}
    if kind == "action":
        fields |= {"turn_id", "state_version", "payload"}
    version = expected.get('protocol_version', VERSION)
    if version == 3 and kind == 'ready':
        fields |= {'ruleset_hash', 'capabilities'}
    if not isinstance(msg, dict) or set(msg) != fields:
        raise Fault("protocol_error", "Unexpected or missing response fields")
    if type(msg["protocol_version"]) is not int or version not in (2, 3) or msg["protocol_version"] != version or msg["type"] != kind:
        raise Fault("protocol_error", "Wrong protocol version or response type")
    if version == 3 and kind == 'ready':
        ruleset = expected['payload']['ruleset']
        if msg['ruleset_hash'] != ruleset['hash']:
            raise Fault('protocol_error', 'Bot did not acknowledge the exact experimental ruleset hash')
        caps = msg['capabilities']
        if not isinstance(caps, list) or len(caps)>16 or any(not isinstance(c,str) for c in caps) or not set(ruleset['requires']).issubset(caps):
            raise Fault('protocol_error', 'Bot lacks required experimental capabilities: ' + ', '.join(ruleset['requires']))
    for key in fields - {"protocol_version", "type", "payload", "capabilities", "ruleset_hash"}:
        if type(msg[key]) is not type(expected[key]) or msg[key] != expected[key]:
            raise Fault("stale_action", "Mismatched " + key)
    return msg
