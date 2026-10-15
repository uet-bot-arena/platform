"""Bot skeleton: protocol demonstration only, not a complete OTTv2 SDK."""
import json
import sys


def choose_move(state, legal_moves):
    return legal_moves[0] if legal_moves else None


def main():
    for line in sys.stdin:
        try:
            message = json.loads(line)
            if message.get("type") != "observation":
                continue
            payload = message.get("payload", {})
            action = choose_move(payload.get("state", {}), payload.get("legal_moves", []))
            response = {
                "protocol_version": 1,
                "type": "action",
                "turn_id": message.get("turn_id"),
                "state_version": message.get("state_version"),
                "payload": action,
            }
            print(json.dumps(response), flush=True)
        except (ValueError, TypeError) as error:
            print(str(error), file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
