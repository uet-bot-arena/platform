"""OTT v2: matched C++/Python reference search, including configurable boards."""
import json
import math
import random
import sys
import time

MODE = "alphabeta"  # alphabeta / defensive / random / mcts
THINK_MS = 100
MAX_DEPTH = 8
WIN = 1000000
NODES = 0
DEADLINE = 0
ROWS = COLS = 9
BASES = (72, 8)
ELIMINATE = 1
CAPTURE = True
NEIGHBORS = []
RNG = random.Random(20261015)

def configure(payload):
    global ROWS,COLS,BASES,ELIMINATE,CAPTURE,NEIGHBORS
    state=payload['state'];ROWS=len(state['board']);COLS=len(state['board'][0])
    rules=state.get('basic_rules',{})
    bases=payload.get('bases',{'red':'a1','blue':'i9'})
    BASES=tuple((ROWS-int(bases[s][1:]))*COLS+ord(bases[s][0])-97 for s in ('red','blue'))
    ELIMINATE=rules.get('eliminate_types',1);CAPTURE=rules.get('base_capture',True)
    NEIGHBORS=[[nr*COLS+nc for dr in (-1,0,1) for dc in (-1,0,1) if (dr or dc) and 0<=(nr:=i//COLS+dr)<ROWS and 0<=(nc:=i%COLS+dc)<COLS] for i in range(ROWS*COLS)]
    return tuple(0 if p is None else 1+(0 if p['side']=='red' else 3)+('rock','paper','scissors').index(p['kind']) for row in state['board'] for p in row),0 if state['to_move']=='red' else 1

def owner(p):return (p-1)//3
def kind(p):return (p-1)%3
def moves(board,side):
    return [(s,t) for s,p in enumerate(board) if p and owner(p)==side for t in NEIGHBORS[s] if not board[t] or owner(board[t])!=side and (kind(p)+2)%3==kind(board[t])]

def distance(a,b):return max(abs(a//COLS-b//COLS),abs(a%COLS-b%COLS))
def play(board,side,move):
    s,t=move;out=list(board);out[t]=out[s];out[s]=0;out=tuple(out)
    kinds={kind(p) for p in out if p and owner(p)!=side}
    won=CAPTURE and t==BASES[1-side] or 3-len(kinds)>=ELIMINATE
    return out,side if won else -1

def evaluate(board,side):
    scores=[0,0];nearest=[max(ROWS,COLS)]*2
    for s,p in enumerate(board):
        if p:
            who=owner(p);d=distance(s,BASES[1-who]);nearest[who]=min(nearest[who],d)
            scores[who]+=100+4*(max(ROWS,COLS)-d)
    for who in (0,1):scores[who]+=25*(max(ROWS,COLS)-nearest[who])
    return scores[side]-scores[1-side]

def ordered(board,side,legal):
    return sorted(legal,key=lambda m:-(10000*(CAPTURE and m[1]==BASES[1-side])+100*bool(board[m[1]])+distance(m[0],BASES[1-side])-distance(m[1],BASES[1-side])))

class Expired(Exception):pass
def negamax(board,side,depth,alpha,beta):
    global NODES
    NODES+=1
    if NODES%16==0 and time.monotonic_ns()>=DEADLINE:raise Expired
    legal=moves(board,side)
    if not legal:return -WIN
    if not depth:return evaluate(board,side)
    best=-2*WIN
    for move in ordered(board,side,legal):
        child,winner=play(board,side,move)
        value=WIN if winner==side else -negamax(child,1-side,depth-1,-beta,-alpha)
        best=max(best,value);alpha=max(alpha,value)
        if alpha>=beta:break
    return best

def choose(board,side,ms=THINK_MS):
    global NODES,DEADLINE
    started=time.monotonic_ns();DEADLINE=started+max(0,ms)*1000000;NODES=0
    legal=ordered(board,side,moves(board,side));best=legal[0];completed=0
    if MODE=='random':best=RNG.choice(legal)
    elif MODE=='mcts':best=mcts(board,side,legal)
    else:
        for depth in range(1,(1 if MODE=='defensive' else MAX_DEPTH)+1):
            candidate=best;alpha=-2*WIN
            try:
                for move in legal:
                    child,winner=play(board,side,move)
                    value=WIN if winner==side else -negamax(child,1-side,depth-1,-2*WIN,-alpha)
                    if MODE=='defensive':value-=sum(30 for enemy in moves(child,1-side) if enemy[1]==move[1])
                    if value>alpha:alpha=value;candidate=move
                best=candidate;completed=depth
                if time.monotonic_ns()>=DEADLINE or alpha>=WIN:break
            except Expired:break
    return best,{'nodes':NODES,'depth':completed,'search_ms':(time.monotonic_ns()-started)/1e6}

def mcts(board,side,legal):
    global NODES
    def node(b,s,w=-1):return {'b':b,'s':s,'w':w,'untried':moves(b,s) if w<0 else [],'children':[],'n':0,'v':0.0}
    root=node(board,side)
    while time.monotonic_ns()<DEADLINE:
        current=root;path=[root]
        while not current['untried'] and current['children']:
            sign=1 if current['s']==side else -1
            current=max(current['children'],key=lambda n:sign*n['v']/n['n']+math.sqrt(2*math.log(max(1,path[-1]['n']))/n['n']))
            path.append(current)
        if current['untried']:
            move=current['untried'].pop();b,w=play(current['b'],current['s'],move)
            child=node(b,1-current['s'],w);child['move']=move;current['children'].append(child);current=child;path.append(child)
        b,s,w=current['b'],current['s'],current['w']
        for _ in range(16):
            if w>=0:break
            options=moves(b,s)
            if not options:w=1-s;break
            b,w=play(b,s,RNG.choice(options));s=1-s;NODES+=1
        reward=(1.0 if w==side else -1.0) if w>=0 else math.tanh(evaluate(b,side)/500)
        for item in path:item['n']+=1;item['v']+=reward
    return max(root['children'],key=lambda n:n['n'])['move'] if root['children'] else legal[0]

def main():
    for line in sys.stdin:
        m=json.loads(line);out={'protocol_version':2,'match_id':m['match_id']}
        if m['type']=='init':out['type']='ready'
        else:
            board,side=configure(m['payload']);legal=moves(board,side)
            if legal:
                move,stats=choose(board,side,min(THINK_MS,max(0,int(m['timing']['budget_ms'])//2-5)))
                square=lambda s:chr(97+s%COLS)+str(ROWS-s//COLS)
                action={'from':square(move[0]),'to':square(move[1])}
                print(json.dumps(stats),file=sys.stderr,flush=True)
            else:action={'resign':True}
            out.update(type='action',turn_id=m['turn_id'],state_version=m['state_version'],payload=action)
        print(json.dumps(out,separators=(',',':')),flush=True)

if __name__=='__main__':main()
