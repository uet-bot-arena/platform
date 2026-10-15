"""Presentation and packaging for rules-v1, separate from the pinned game engine.

An add-on is declarative data. Import never loads Python/C++ or changes an engine.
Contracts, change lists and examples are derived from the same definition bots use.
"""
from copy import deepcopy
import json

from arena import engine, experimental
from arena.protocol import Fault

FORMAT = 'ottv2.ruleset-addon'
MODULES = [
    {'id': 'energy', 'name': 'Năng lượng', 'description': 'Quỹ riêng mỗi đội, nhận thưởng ở cuối lượt chiếm vùng; không vượt cap.'},
    {'id': 'zones', 'name': 'Vùng bàn cờ', 'description': 'Vùng energy/control thưởng cho bên vừa hành động nếu còn chiếm ô.'},
    {'id': 'movement', 'name': 'Mẫu đi', 'description': 'Thêm mẫu đi theo loại hoặc ID quân, có chi phí và cooldown.'},
    {'id': 'shield', 'name': 'Khiên', 'description': 'Tốn một lượt để chặn một lần bị ăn; không thay quan hệ khắc chế.'},
    {'id': 'victory', 'name': 'Mục tiêu', 'description': 'Bật/tắt mục tiêu căn cứ, loại quân, hết nước; thêm điểm kiểm soát.'},
]
LABELS = {'energy': 'Năng lượng', 'zones': 'Vùng', 'modifiers': 'Mẫu đi', 'shield': 'Khiên', 'victory': 'Điều kiện thắng',
          'cap': 'giới hạn', 'initial': 'ban đầu', 'enabled': 'bật', 'cost': 'chi phí', 'duration': 'thời hạn',
          'base_capture': 'chiếm căn cứ', 'type_eliminated': 'hết một loại quân', 'no_legal_moves': 'hết nước hợp lệ',
          'control_target': 'điểm kiểm soát', 'name': 'Tên'}
BASELINE = {'name': 'OTT v2 tiêu chuẩn', 'energy': {'cap': 0, 'initial': 0}, 'zones': [], 'modifiers': [],
            'shield': {'enabled': False, 'cost': 2, 'duration': 2},
            'victory': {'base_capture': True, 'type_eliminated': True, 'no_legal_moves': True, 'control_target': 0}}


def definition_schema():
    def obj(properties, required=None):
        return {'type':'object','additionalProperties':False,'properties':properties,'required':list(properties) if required is None else required}
    def integer(lo,hi):return {'type':'integer','minimum':lo,'maximum':hi}
    piece_ids=[p['side']+'-'+engine.square(r,c) for r,row in enumerate(engine.initial_state()['board']) for c,p in enumerate(row) if p]
    schema=obj({'schema_version':{'const':1,'type':'integer'},'name':{'type':'string','minLength':3,'maxLength':80},
        'energy':obj({'cap':integer(0,9),'initial':integer(0,9)}),
        'zones':{'type':'array','maxItems':9,'items':obj({'square':{'type':'string','pattern':'^[a-i][1-9]$'},'kind':{'enum':['energy','control']},'gain':integer(1,3)})},
        'modifiers':{'type':'array','maxItems':4,'items':obj({
            'id':{'type':'string','pattern':'^[a-z][a-z0-9_-]{0,23}$'},
            'filter':obj({'kinds':{'type':'array','minItems':1,'maxItems':3,'uniqueItems':True,'items':{'enum':list(experimental.KINDS)}},
                          'piece_ids':{'type':'array','minItems':1,'maxItems':18,'uniqueItems':True,'items':{'enum':piece_ids}}},[]),
            'pattern':{'enum':list(experimental.PATTERNS)},'range':integer(1,3),'cost':integer(0,9),'cooldown':integer(0,8)})},
        'shield':obj({'enabled':{'type':'boolean'},'cost':integer(0,9),'duration':integer(1,5)}),
        'victory':obj({'base_capture':{'type':'boolean'},'type_eliminated':{'type':'boolean'},'no_legal_moves':{'type':'boolean'},'control_target':integer(0,30)})})
    return {'$schema':'https://json-schema.org/draft/2020-12/schema','title':'OTTv2 rules-v1 definition',
            'description':'Schema cấu trúc. Server kiểm tra thêm initial/cost <= cap, knight range=1, ô/ID duy nhất, vùng control và giới hạn 240 action. Definition tối đa 8 KiB.',**schema}


def notes(value):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 32 and c not in '\n\t\r' for c in value):
        raise Fault('invalid_ruleset', 'release_notes: cần văn bản tối đa 2000 ký tự')
    return value.strip()


def unpack(package):
    if not isinstance(package, dict) or len(experimental.canonical(package).encode()) > 32768:
        raise Fault('invalid_ruleset', 'Gói add-on cần object JSON, tối đa 32 KiB')
    if 'format' not in package:
        experimental.validate(package)
        return deepcopy(package), ''
    if package.get('format') != FORMAT or type(package.get('format_version')) is not int or package['format_version'] != 1:
        raise Fault('invalid_ruleset', 'Gói add-on cần format ottv2.ruleset-addon và format_version 1')
    if set(package) - {'format', 'format_version', 'definition', 'release_notes', 'source'}:
        raise Fault('invalid_ruleset', 'Gói add-on chứa trường không được hỗ trợ')
    experimental.validate(package.get('definition'))
    return deepcopy(package['definition']), notes(package.get('release_notes', ''))


def changes(definition, previous=None):
    before = previous or BASELINE
    rows = []
    for section in ('energy', 'shield', 'victory'):
        for key, value in definition[section].items():
            old = before[section].get(key)
            if old != value:
                rows.append({'path': section + '.' + key, 'label': LABELS[section] + ' · ' + LABELS.get(key, key), 'before': old, 'after': value})
    for section, key in (('zones', 'square'), ('modifiers', 'id')):
        a = {x[key]: x for x in before[section]}
        b = {x[key]: x for x in definition[section]}
        for ident in sorted(a.keys() | b.keys()):
            if a.get(ident) != b.get(ident):
                rows.append({'path': section + '.' + ident, 'label': LABELS[section] + ' · ' + ident,
                             'before': a.get(ident), 'after': b.get(ident)})
    return rows


def check(definition):
    validation = experimental.validate(definition)
    rules = experimental.Rules(definition)
    samples = []
    for first in engine.SIDES:
        state = rules.initial_state()
        state['to_move'] = first
        steps = 0
        while steps < 32 and not state['result']:
            actions = rules.legal_actions(state)
            if not actions:
                break
            # Deterministic smoke path. This is not a balance or bot benchmark.
            state, _ = rules.transition(state, {'action_id': actions[steps % len(actions)]['id']})
            steps += 1
        samples.append({'first_side': first, 'plies': steps, 'result': state['result']})
    return {**validation, 'checks': ['schema', 'action_bound', 'observation_size', 'smoke_red', 'smoke_blue'], 'smoke': samples}


def preview(definition, actions):
    rules = experimental.Rules(definition)
    state = rules.initial_state()
    events = []
    for index, action in enumerate(actions):
        try:
            state, trace = rules.transition(state, {'action_id': action})
        except ValueError as exc:
            raise Fault('invalid_action', f'Nước thử {index + 1}: {exc}') from exc
        events.extend(trace)
    return {'validation': experimental.validate(definition), 'observation': rules.observation(state), 'events': events}


def contract(record, previous=None):
    d = record['definition']
    hashed = experimental.rule_hash(d)
    initial = experimental.Rules(d).observation(experimental.Rules(d).initial_state())
    action = initial['legal_actions'][0] if initial['legal_actions'] else None
    match = 'example-match'
    return {
        'format': FORMAT, 'format_version': 1, 'experimental': True,
        'ruleset_id': record['id'], 'version': record['version'], 'hash': hashed,
        'release_notes': record.get('release_notes', ''),
        'compared_to': {'id': previous['id'], 'name': previous['definition']['name'], 'version': previous['version']} if previous else {'id': None, 'name': 'OTT v2 tiêu chuẩn'},
        'changes': changes(d, previous['definition'] if previous else None),
        'modules': [{**m, 'enabled': bool({'energy': d['energy']['cap'], 'zones': d['zones'], 'movement': d['modifiers'],
                    'shield': d['shield']['enabled'], 'victory': True}[m['id']])} for m in MODULES],
        'compatibility': {'protocol_version': 3, 'capabilities': ['rules-v1'], 'sdk': 'experimental.hpp',
                          'classic_bot': 'Bot chỉ hỗ trợ protocol v2 cần nâng lên SDK v3 trước khi dự giải add-on.'},
        'limits': {'zones': 9, 'modifiers': 4, 'range': [1, 3], 'energy_cap': [0, 9], 'cooldown': [0, 8],
                   'shield_duration': [1, 5], 'control_target': [0, 30], 'legal_actions': 240, 'preview_plies': 32},
        'movement': {'base': '8 hướng, 1 ô, miễn phí; chỉ ăn quân bị khắc chế.',
                     'patterns': {'orthogonal': 'Thẳng: 1..range ô, không xuyên quân.', 'diagonal': 'Chéo: 1..range ô, không xuyên quân.',
                                  'king': '8 hướng: 1..range ô, không xuyên quân.', 'knight': 'Nhảy chữ L, vượt quân; range phải bằng 1.'},
                     'filters': 'kinds và piece_ids kết hợp AND; các phần tử trong mỗi danh sách kết hợp OR. Bỏ trường con để chọn mọi quân; danh sách rỗng bị từ chối.',
                     'cooldown': 'Cooldown N: hoàn thành N lượt tiếp theo của đội trước khi dùng lại; gắn riêng với từng quân.',
                     'shield': 'Khiên tiêu thụ một lượt, chặn một lần bị ăn. Quân tấn công ở nguyên ô; năng lượng và cooldown vẫn bị trừ.'},
        'protocol': {'transport': 'JSON Lines qua stdin/stdout; một JSON mỗi dòng và flush. Log ra stderr. Bot không gọi HTTP.',
                     'identity': 'Sao chép nguyên match_id, turn_id và state_version từ observation. Không đổi kiểu dữ liệu.',
                     'legal_actions': 'Chọn id từ payload.legal_actions của observation hiện tại. ID hết hạn sau lượt; không giữ lại hoặc suy ra từ tọa độ.',
                     'state_fields': ['board', 'to_move', 'ply', 'result', 'energy', 'control', 'turns'],
                     'piece_fields': ['side', 'kind', 'id', 'cooldowns', 'shield_until'],
                     'clock': 'Đọc payload.clocks (mili giây theo màu) và timing.remaining_ms. Quỹ 60s hoặc 300s mỗi bot cho cả trận, chỉ trừ trong lượt; không increment. Giải cũ giữ timing đã ghim. Không thay chính sách đồng hồ bằng add-on.'},
        'examples': {
            'ready': {'protocol_version': 3, 'type': 'ready', 'match_id': match, 'ruleset_hash': hashed, 'capabilities': ['rules-v1']},
            'observation_payload': initial,
            'action': {'protocol_version': 3, 'type': 'action', 'match_id': match, 'turn_id': 'copy-current-turn-id',
                       'state_version': 0, 'payload': {'action_id': action['id']}} if action else None},
    }


def package(record):
    return {'format': FORMAT, 'format_version': 1, 'definition': deepcopy(record['definition']),
            'release_notes': record.get('release_notes', ''),
            'source': {'id': record['id'], 'version': record['version'], 'hash': experimental.rule_hash(record['definition'])}}


def markdown(record, spec):
    d = record['definition']
    out = [f"# {d['name']} · v{record['version']}", '', 'Ruleset add-on · Experimental · protocol v3 / rules-v1', '',
           f"Hash: `{spec['hash']}`", '', record.get('release_notes') or 'Chưa có ghi chú riêng từ BTC.', '',
           '## Thay đổi so với ' + spec['compared_to']['name'], '']
    for row in spec['changes']:
        out.append('- ' + row['label'] + ': `' + json.dumps(row['before'], ensure_ascii=False) + '` → `' + json.dumps(row['after'], ensure_ascii=False) + '`')
    out += ['', '## Giao thức bot', '', spec['protocol']['transport']]
    out += ['', spec['protocol']['identity'], '', spec['protocol']['legal_actions'], '', spec['protocol']['clock'], '',
            'Nộp ZIP với SDK v3, benchmark đúng ruleset/hash và cấu hình thời gian của giải. Chọn bot đạt chuẩn rồi đăng ký vào giải.', '',
            '## Nước đi và kỹ năng', '', *[str(v) for v in spec['movement'].values() if isinstance(v, str)], '']
    for key, value in spec['movement']['patterns'].items():
        out.append(f'- `{key}`: {value}')
    out += ['', '## Cấu hình đầy đủ', '', '```json', json.dumps(d, ensure_ascii=False, indent=2), '```', '',
            '## Ready mẫu', '', '```json', json.dumps(spec['examples']['ready'], ensure_ascii=False), '```', '',
            '## Action mẫu', '', '```json', json.dumps(spec['examples']['action'], ensure_ascii=False), '```', '',
            'Ví dụ action chỉ dùng cùng observation mẫu; khi chạy thật hãy sao chép envelope hiện tại.', '',
            'Gói tài liệu chứa starter.zip để nộp source. Không nộp nguyên gói tài liệu có contract.json/ruleset.json.', '',
            'Đọc /api/v1/experimental/guide hoặc PROTOCOL.md trong gói tài liệu để biết thưởng vùng, thời hạn khiên, thứ tự thắng và timing. Công bố xác nhận kiểm tra cấu hình và smoke; không chứng nhận cân bằng.']
    return '\n'.join(out) + '\n'
