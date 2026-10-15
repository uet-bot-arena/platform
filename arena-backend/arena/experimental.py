"""Bounded, deterministic rule packs. Classic engine bytes stay unchanged."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from arena import engine
from arena.protocol import Fault

ENGINE_ID = 'ottv2-experimental-v1'
ENGINE_SHA256 = hashlib.sha256(Path(__file__).read_bytes() + engine.ENGINE_SHA256.encode()).hexdigest()
KINDS = ('rock', 'paper', 'scissors')
PATTERNS = ('orthogonal', 'diagonal', 'king', 'knight')
PRESETS = {
    'energy-dash': {'schema_version': 1, 'name': 'Energy + Dash',
        'energy': {'cap': 3, 'initial': 1},
        'zones': [{'square': s, 'kind': 'energy', 'gain': 1} for s in ('c5', 'g5')],
        'modifiers': [{'id': 'dash', 'filter': {'kinds': ['scissors']}, 'pattern': 'orthogonal', 'range': 2, 'cost': 2, 'cooldown': 1}],
        'shield': {'enabled': False, 'cost': 2, 'duration': 2},
        'victory': {'base_capture': True, 'type_eliminated': True, 'no_legal_moves': True, 'control_target': 0}},
    'domination': {'schema_version': 1, 'name': 'Domination + Shield',
        'energy': {'cap': 5, 'initial': 2},
        'zones': [{'square': s, 'kind': k, 'gain': 1} for s, k in [('c5','energy'),('g5','energy'),('e5','control')]],
        'modifiers': [{'id': 'dash', 'filter': {'kinds': ['scissors']}, 'pattern': 'orthogonal', 'range': 2, 'cost': 2, 'cooldown': 1}],
        'shield': {'enabled': True, 'cost': 2, 'duration': 2},
        'victory': {'base_capture': True, 'type_eliminated': False, 'no_legal_moves': True, 'control_target': 6}}
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def rule_hash(definition):
    return hashlib.sha256(canonical(definition).encode()).hexdigest()


def sdk_hash():
    root = Path(__file__).resolve().parents[1] / 'sdk'
    return hashlib.sha256((root / 'json.hpp').read_bytes() + (root / 'experimental.hpp').read_bytes()).hexdigest()


def validate(d):
    def fail(path, reason):
        raise Fault('invalid_ruleset', path + ': ' + reason)
    def obj(value, keys, path):
        if not isinstance(value, dict) or set(value) - set(keys): fail(path, 'trường không được hỗ trợ')
    def number(value, lo, hi, path):
        if type(value) is not int or not lo <= value <= hi: fail(path, f'cần số nguyên {lo}..{hi}')
    obj(d, ('schema_version','name','energy','zones','modifiers','shield','victory'), 'ruleset')
    if d.get('schema_version') != 1 or type(d.get('schema_version')) is not int: fail('schema_version','chỉ hỗ trợ v1')
    if not isinstance(d.get('name'), str) or not 3 <= len(d['name'].strip()) <= 80 or any(ord(c)<32 for c in d['name']): fail('name','cần tên 3..80 ký tự')
    e=d.get('energy',{});obj(e,('cap','initial'),'energy')
    number(e.get('cap'),0,9,'energy.cap');number(e.get('initial'),0,e['cap'],'energy.initial')
    zones=d.get('zones');mods=d.get('modifiers')
    if not isinstance(zones,list) or len(zones)>9: fail('zones','tối đa 9 vùng')
    seen=set()
    for i,z in enumerate(zones):
        path=f'zones[{i}]';obj(z,('square','kind','gain'),path)
        try: engine.position(z.get('square'))
        except ValueError: fail(path+'.square','ô phải nằm trong a1..i9')
        if z['square'] in seen or z['square'] in engine.BASES.values(): fail(path+'.square','ô bị trùng hoặc là căn cứ')
        seen.add(z['square'])
        if z.get('kind') not in ('energy','control'): fail(path+'.kind','chỉ hỗ trợ energy/control')
        number(z.get('gain'),1,3,path+'.gain')
        if z['kind']=='energy' and not e['cap']: fail(path,'vùng năng lượng cần energy.cap > 0')
    if not isinstance(mods,list) or len(mods)>4: fail('modifiers','tối đa 4 modifier')
    ids=set()
    initial_ids={f'{p["side"]}-{engine.square(r,c)}' for r,row in enumerate(engine.initial_state()['board']) for c,p in enumerate(row) if p}
    for i,m in enumerate(mods):
        path=f'modifiers[{i}]';obj(m,('id','filter','pattern','range','cost','cooldown'),path)
        ident=m.get('id')
        if not isinstance(ident,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,23}',ident) or ident in ids: fail(path+'.id','ID cần duy nhất, chữ thường/số/_/-')
        if 'filter' not in m:fail(path+'.filter','cần object filter; dùng {} để áp dụng cho tất cả')
        ids.add(ident);f=m['filter'];obj(f,('kinds','piece_ids'),path+'.filter')
        for key,allowed in (('kinds',set(KINDS)),('piece_ids',initial_ids)):
            v=f.get(key,[])
            if not isinstance(v,list) or len(v)>18 or any(not isinstance(x,str) or x not in allowed for x in v) or len(set(v))!=len(v): fail(path+'.filter.'+key,'giá trị không hợp lệ hoặc bị trùng')
            if key in f and not v: fail(path+'.filter.'+key,'chọn ít nhất một giá trị; bỏ trường này để áp dụng cho tất cả')
        if m.get('pattern') not in PATTERNS: fail(path+'.pattern','orthogonal/diagonal/king/knight')
        number(m.get('range'),1,3,path+'.range');number(m.get('cost'),0,e['cap'],path+'.cost');number(m.get('cooldown'),0,8,path+'.cooldown')
        if m['pattern']=='knight' and m['range']!=1: fail(path+'.range','knight cần range = 1')
    sh=d.get('shield',{});obj(sh,('enabled','cost','duration'),'shield')
    if type(sh.get('enabled')) is not bool: fail('shield.enabled','cần true/false')
    number(sh.get('cost'),0,9,'shield.cost');number(sh.get('duration'),1,5,'shield.duration')
    if sh['enabled'] and (sh['cost']<1 or sh['cost']>e['cap']): fail('shield.cost','cần 1..energy.cap')
    v=d.get('victory',{});obj(v,('base_capture','type_eliminated','no_legal_moves','control_target'),'victory')
    for key in ('base_capture','type_eliminated','no_legal_moves'):
        if type(v.get(key)) is not bool: fail('victory.'+key,'cần true/false')
    number(v.get('control_target'),0,30,'victory.control_target')
    if v['control_target'] and not any(z['kind']=='control' for z in zones): fail('victory.control_target','cần ít nhất một vùng control')
    if len(canonical(d).encode())>8192: fail('ruleset','vượt giới hạn 8 KiB')
    # Prove an upper bound for every reachable board, not just the initial one.
    # A piece can never change kind/ID in rules-v1. Each action is <160 bytes;
    # 240 actions + full dynamic board + control envelope fit within 64 KiB.
    bounds={side:0 for side in engine.SIDES}
    for row in engine.initial_state()['board']:
        for p in row:
            if not p:continue
            # piece_ids filters can only reduce this bound, so omit them here.
            bound=8+int(sh['enabled'])
            for m in mods:
                if not m['filter'].get('kinds') or p['kind'] in m['filter']['kinds']:
                    bound+=({'orthogonal':4,'diagonal':4,'king':8,'knight':8}[m['pattern']])*m['range']
            bounds[p['side']]+=bound
    if max(bounds.values())>240:fail('modifiers','quá nhiều action tiềm năng (giới hạn 240 mỗi lượt); giảm range hoặc thu hẹp filter loại quân')
    # Algebraic bounds: <=18 pieces, 5 patterns each <=24 targets, plus shields.
    # Keep all legal actions + board + timing below the 64 KiB worker frame.
    trial=Rules(d).observation(Rules(d).initial_state())
    if len(canonical(trial).encode())>54000: fail('ruleset','observation quá lớn')
    warnings=[]
    rotated={(engine.square(8-engine.position(z['square'])[0],8-engine.position(z['square'])[1]),z['kind'],z['gain']) for z in zones}
    if rotated!={(z['square'],z['kind'],z['gain']) for z in zones}: warnings.append('Bản đồ vùng không đối xứng khi đổi màu.')
    if any(m['filter'].get('piece_ids') for m in mods): warnings.append('Filter theo ID có thể tạo lợi thế riêng cho một màu; cần kiểm tra hai lượt.')
    return {'valid':True,'hash':rule_hash(d),'requires':['rules-v1'],'warnings':warnings,
            'summary':f"{len(zones)} vùng · {len(mods)} mẫu đi bổ sung · khiên {'bật' if sh['enabled'] else 'tắt'} · mục tiêu kiểm soát {v['control_target'] or 'tắt'}"}


class Rules:
    def __init__(self, definition):
        self.d=deepcopy(definition);self.hash=rule_hash(definition)
    def initial_state(self):
        s=engine.initial_state();s.update(energy={side:self.d['energy']['initial'] for side in engine.SIDES},control={side:0 for side in engine.SIDES},turns={side:0 for side in engine.SIDES})
        for r,row in enumerate(s['board']):
            for c,p in enumerate(row):
                if p:p.update(id=p['side']+'-'+engine.square(r,c),cooldowns={},shield_until=0)
        return s
    @staticmethod
    def matches(piece, f):
        return (not f.get('kinds') or piece['kind'] in f['kinds']) and (not f.get('piece_ids') or piece['id'] in f['piece_ids'])
    def legal_actions(self,s):
        if s['result']:return []
        side=s['to_move'];actions=[]
        directions={'orthogonal':[(-1,0),(0,-1),(0,1),(1,0)],'diagonal':[(-1,-1),(-1,1),(1,-1),(1,1)],'king':engine.DIRECTIONS,
                    'knight':[(-2,-1),(-2,1),(-1,-2),(-1,2),(1,-2),(1,2),(2,-1),(2,1)]}
        def add(a):a['id']='a'+str(len(actions));actions.append(a)
        for r,row in enumerate(s['board']):
            for c,p in enumerate(row):
                if not p or p['side']!=side:continue
                patterns=[{'id':'','pattern':'king','range':1,'cost':0,'cooldown':0}]+[m for m in self.d['modifiers'] if self.matches(p,m['filter']) and p['cooldowns'].get(m['id'],0)<=s['turns'][side] and m['cost']<=s['energy'][side]]
                for m in patterns:
                    for dr,dc in directions[m['pattern']]:
                        for step in range(1,m['range']+1):
                            nr,nc=r+dr*step,c+dc*step
                            if not (0<=nr<9 and 0<=nc<9):break
                            target=s['board'][nr][nc]
                            if target is None or target['side']!=side and engine.BEATS[p['kind']]==target['kind']:
                                add({'type':'move','piece_id':p['id'],'from':engine.square(r,c),'to':engine.square(nr,nc),'ability':m['id'],'energy_cost':m['cost']})
                            if target is not None:break
                sh=self.d['shield']
                if sh['enabled'] and s['energy'][side]>=sh['cost'] and p['shield_until']<=s['turns'][side]:
                    add({'type':'shield','piece_id':p['id'],'from':engine.square(r,c),'energy_cost':sh['cost']})
        return actions
    def legal_moves(self,s):return [{'action_id':a['id']} for a in self.legal_actions(s)]
    def observation(self,s):
        return {'state':deepcopy(s),'legal_actions':self.legal_actions(s),'counts':engine.counts(s),'bases':engine.BASES,'ruleset_hash':self.hash,'zones':deepcopy(self.d['zones'])}
    def apply(self,s,action):return self.transition(s,action)[0]
    def transition(self,s,action):
        if s['result']:raise ValueError('Match already finished')
        if isinstance(action,dict) and set(action)=={'resign'} and action['resign'] is True:
            return engine.finish(deepcopy(s),engine.other(s['to_move']),'resignation'),[]
        if not isinstance(action,dict) or set(action)!={'action_id'} or not isinstance(action['action_id'],str):raise ValueError('Experimental action requires exactly {action_id: string}')
        a=next((x for x in self.legal_actions(s) if x['id']==action['action_id']),None)
        if a is None:raise ValueError('Unknown/expired action_id; choose one from this observation.legal_actions')
        out=deepcopy(s);side=s['to_move'];other=engine.other(side);r,c=engine.position(a['from']);p=out['board'][r][c];events=[]
        out['energy'][side]-=a['energy_cost']
        if a['type']=='shield':
            p['shield_until']=out['turns'][side]+self.d['shield']['duration']+1
            events.append({'type':'ability_used','side':side,'kind':p['kind'],'piece_id':p['id'],'ability':'shield','square':a['from']})
        else:
            tr,tc=engine.position(a['to']);target=out['board'][tr][tc]
            blocked=target is not None and target['shield_until']>out['turns'][other]
            events.append({'type':'move','side':side,'kind':p['kind'],'piece_id':p['id'],'from':a['from'],'to':a['to'],'ability':a['ability'],
                           'captured':deepcopy(target) if target and not blocked else None,'blocked':blocked})
            if blocked:
                target['shield_until']=0;events.append({'type':'shield_broken','side':other,'piece_id':target['id'],'square':a['to']})
            else:out['board'][tr][tc]=p;out['board'][r][c]=None
            if a['ability']:
                m=next(m for m in self.d['modifiers'] if m['id']==a['ability']);p['cooldowns'][m['id']]=out['turns'][side]+m['cooldown']+1
            if not blocked and self.d['victory']['base_capture'] and a['to']==engine.BASES[other]:engine.finish(out,side,'base_capture')
        out['ply']+=1;out['turns'][side]+=1;out['to_move']=other
        for row in out['board']:
            for piece in row:
                if piece and piece['side']==side:
                    piece['cooldowns']={k:v for k,v in piece['cooldowns'].items() if v>out['turns'][side]}
                    if piece['shield_until']<=out['turns'][side]:piece['shield_until']=0
        # Victory checks from captures take priority over end-of-turn rewards.
        remaining=engine.counts(out)[other]
        if not out['result'] and not sum(remaining.values()):engine.finish(out,side,'all_pieces_eliminated')
        if not out['result'] and self.d['victory']['type_eliminated'] and 0 in remaining.values():engine.finish(out,side,'type_eliminated')
        if not out['result']:
            for z in self.d['zones']:
                zr,zc=engine.position(z['square']);occupant=out['board'][zr][zc]
                if occupant and occupant['side']==side:
                    resource='energy' if z['kind']=='energy' else 'control';before=out[resource][side]
                    out[resource][side]=min(self.d['energy']['cap'] if resource=='energy' else 10000,before+z['gain'])
                    events.append({'type':resource+'_gained','side':side,'square':z['square'],'amount':out[resource][side]-before})
            if self.d['victory']['control_target'] and out['control'][side]>=self.d['victory']['control_target']:engine.finish(out,side,'control_target')
            if not out['result'] and self.d['victory']['no_legal_moves'] and not self.legal_actions(out):engine.finish(out,side,'no_legal_moves')
        return out,events
    @staticmethod
    def position_key(s):
        # Absolute turn counters must not defeat repetition. Keep relative
        # cooldown/shield life, resources and all identity-dependent rights.
        pieces=[]
        for row in s['board']:
            for p in row:
                pieces.append(None if not p else (p['id'],p['side'],p['kind'],max(0,p['shield_until']-s['turns'][p['side']]),tuple(sorted((k,max(0,v-s['turns'][p['side']])) for k,v in p['cooldowns'].items() if v>s['turns'][p['side']]))))
        return s['to_move'],tuple(pieces),tuple(s['energy'][x] for x in engine.SIDES),tuple(s['control'][x] for x in engine.SIDES)


def provider(snapshot=None):
    if not snapshot:return engine
    if snapshot.get('hash')!=rule_hash(snapshot.get('definition')) or snapshot.get('engine_sha256')!=ENGINE_SHA256 or snapshot.get('sdk_sha256')!=sdk_hash():
        raise Fault('provenance_mismatch','Experimental ruleset/engine/SDK changed; create a new published version')
    validate(snapshot['definition'])
    return Rules(snapshot['definition'])
