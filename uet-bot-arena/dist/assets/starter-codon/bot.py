"""Opt-in Codon 0.20.3 / CPython protocol skeleton, not a full OTTv2 SDK.

Native JSON in Codon differs from CPython's dict/list. Keep that detail at the
protocol boundary; convert numeric board values to int before your algorithm.
"""
import json
import sys


def choose_move(state, legal_moves):
    return legal_moves[0] if len(legal_moves) > 0 else json.loads('null')


def main():
    for line in sys.stdin:
        try:
            message = json.loads(line)
            if json.dumps(message['type']) != '"observation"':
                continue
            payload = message['payload']
            action = choose_move(payload['state'], payload['legal_moves'])
            # Serialize each boundary value separately: avoids heterogeneous
            # Python dict values being inferred as incompatible Codon types.
            response = ('{"protocol_version":1,"type":"action","turn_id":'
                        + json.dumps(message['turn_id'])
                        + ',"state_version":' + json.dumps(message['state_version'])
                        + ',"payload":' + json.dumps(action) + '}')
            print(response, flush=True)
        except Exception as error:
            print(str(error), file=sys.stderr, flush=True)


if __name__ == '__main__':
    main()
