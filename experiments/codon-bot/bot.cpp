// Algorithm-for-algorithm reference port of bot.py; no bitboard/SIMD redesign.
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <sstream>
#include <string>
#include <tuple>
#include <utility>
#include <vector>
using namespace std;
using I = int64_t;
using Board = vector<I>;
constexpr I SIZE=9, CELLS=81, WIN=1000000;
const vector<I> WEIGHTS={0,1,8,80,800,WIN};
const vector<pair<I,I>> DIRECTIONS={{0,1},{1,0},{1,1},{1,-1}};
vector<Board> make_windows(){
    vector<Board> windows;
    for(I row=0;row<SIZE;row++)for(I col=0;col<SIZE;col++)for(auto [dr,dc]:DIRECTIONS){
        I er=row+4*dr,ec=col+4*dc;
        if(er>=0&&er<SIZE&&ec>=0&&ec<SIZE){Board w;for(I k=0;k<5;k++)w.push_back((row+k*dr)*SIZE+col+k*dc);windows.push_back(w);}
    }
    return windows;
}
const vector<Board> WINDOWS=make_windows();
I evaluate(const Board& board,I player){
    I score=0;
    for(const auto& window:WINDOWS){
        I own=0,enemy=0;
        for(I cell:window){I value=board[cell];if(value==player)own++;else if(value==-player)enemy++;}
        if(enemy==0){if(own==5)return WIN;score+=WEIGHTS[own];}
        if(own==0){if(enemy==5)return -WIN;score-=WEIGHTS[enemy];}
    }
    return score;
}
bool won(const Board& board,I cell,I player){
    I row=cell/SIZE,col=cell%SIZE;
    for(auto [dr,dc]:DIRECTIONS){
        I count=1;
        for(I sign:{-1,1}){I rr=row+sign*dr,cc=col+sign*dc;while(rr>=0&&rr<SIZE&&cc>=0&&cc<SIZE&&board[rr*SIZE+cc]==player){count++;rr+=sign*dr;cc+=sign*dc;}}
        if(count>=5)return true;
    }
    return false;
}
Board candidates(const Board& board,I limit){
    vector<pair<I,I>> ranked;
    for(I cell=0;cell<CELLS;cell++){
        if(board[cell]!=0)continue;
        I row=cell/SIZE,col=cell%SIZE,neighbours=0;
        for(I dr:{-1,0,1})for(I dc:{-1,0,1}){I rr=row+dr,cc=col+dc;if(rr>=0&&rr<SIZE&&cc>=0&&cc<SIZE&&board[rr*SIZE+cc]!=0)neighbours++;}
        I priority=neighbours*20-abs(row-4)-abs(col-4);
        if(won(board,cell,1)||won(board,cell,-1))priority+=10000;
        ranked.emplace_back(-priority,cell);
    }
    sort(ranked.begin(),ranked.end());Board moves;
    for(I i=0;i<min(limit,I(ranked.size()));i++)moves.push_back(ranked[i].second);
    return moves;
}
tuple<I,I,I> greedy(Board& board,I player){
    I best_move=-1,best_score=-2*WIN,nodes=0;
    for(I move:candidates(board,12)){board[move]=player;I score=evaluate(board,player);board[move]=0;nodes++;if(score>best_score){best_move=move;best_score=score;}}
    return {best_move,best_score,nodes};
}
pair<I,I> negamax(Board& board,I player,I depth,I alpha,I beta){
    I current=evaluate(board,player);if(depth==0||abs(current)==WIN)return {current,1};
    auto moves=candidates(board,8);if(moves.empty())return {0,1};
    I best=-2*WIN,nodes=1;
    for(I move:moves){board[move]=player;auto [child_score,child_nodes]=negamax(board,-player,depth-1,-beta,-alpha);I score=-child_score;board[move]=0;nodes+=child_nodes;best=max(best,score);alpha=max(alpha,score);if(alpha>=beta)break;}
    return {best,nodes};
}
tuple<I,I,I> minimax(Board& board,I player,I depth){
    I best_move=-1,best_score=-2*WIN,nodes=0,alpha=-2*WIN;
    for(I move:candidates(board,8)){board[move]=player;auto [child_score,child_nodes]=negamax(board,-player,depth-1,-2*WIN,-alpha);I score=-child_score;board[move]=0;nodes+=child_nodes;if(score>best_score){best_move=move;best_score=score;}alpha=max(alpha,score);}
    return {best_move,best_score,nodes};
}
tuple<I,I,I> monte_carlo(const Board& board,I player,I rollouts){
    auto moves=candidates(board,8);if(moves.empty())return {-1,0,0};
    Board totals(moves.size(),0),counts(moves.size(),0);I seed=1234567,nodes=0;
    for(I trial=0;trial<rollouts;trial++){
        I index=trial%I(moves.size()),move=moves[index],value=0;Board work=board;work[move]=player;nodes++;
        if(won(work,move,player))value=1;
        else{
            Board empty;for(I cell=0;cell<CELLS;cell++)if(work[cell]==0)empty.push_back(cell);
            I turn=-player,plies=min(I(24),I(empty.size()));
            for(I ply=0;ply<plies;ply++){
                seed=(seed*1103515245+12345)&2147483647;I chosen=seed%I(empty.size()),cell=empty[chosen];
                empty[chosen]=empty.back();empty.pop_back();work[cell]=turn;nodes++;
                if(won(work,cell,turn)){value=turn==player?1:-1;break;}turn=-turn;
            }
            if(value==0){I heuristic=evaluate(work,player);value=heuristic>0?1:heuristic<0?-1:0;}
        }
        totals[index]+=value;counts[index]++;
    }
    I best=0;for(I i=1;i<I(moves.size());i++)if(totals[i]*counts[best]>totals[best]*counts[i])best=i;
    return {moves[best],totals[best],nodes};
}
vector<string> split(const string& text,char delimiter){vector<string> out;string part;istringstream input(text);while(getline(input,part,delimiter))out.push_back(part);return out;}
int main(){
    cout<<"READY|1"<<endl;string line;
    while(getline(cin,line)){
        if(line=="QUIT")break;if(line=="PING"){cout<<"PONG"<<endl;continue;}
        auto fields=split(line,'|');if(fields.size()!=5){cout<<"ERROR|protocol"<<endl;continue;}
        I player=stoll(fields[2]),parameter=stoll(fields[3]);Board board;for(auto value:split(fields[4],','))board.push_back(stoll(value));
        if(board.size()!=CELLS||(player!=1&&player!=-1)||any_of(board.begin(),board.end(),[](I v){return v < -1||v>1;})){cout<<"ERROR|board"<<endl;continue;}
        auto started=chrono::steady_clock::now();I move,score,nodes;
        if(all_of(board.begin(),board.end(),[](I v){return v!=0;}))tie(move,score,nodes)=make_tuple(I(-1),I(0),I(0));
        else if(fields[1]=="greedy")tie(move,score,nodes)=greedy(board,player);
        else if(fields[1]=="minimax"&&parameter>=1&&parameter<=5)tie(move,score,nodes)=minimax(board,player,parameter);
        else if(fields[1]=="mc"&&parameter>=8&&parameter<=4096)tie(move,score,nodes)=monte_carlo(board,player,parameter);
        else{cout<<"ERROR|algorithm"<<endl;continue;}
        auto us=chrono::duration_cast<chrono::microseconds>(chrono::steady_clock::now()-started).count();
        cout<<fields[0]<<'|'<<move<<'|'<<score<<'|'<<nodes<<'|'<<us<<endl;
    }
}
