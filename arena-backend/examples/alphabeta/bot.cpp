// OTTv2 v2: iterative deepening negamax, alpha-beta, transposition table.
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>
#include "json.hpp"
using json = nlohmann::json;
using Clock = std::chrono::steady_clock;
constexpr int WIN = 1000000, INF = 2000000;
constexpr int THINK_MS = 250, MAX_DEPTH = 8, QDEPTH = 2;
struct Move { int from = -1, to = -1; };
bool operator==(Move a, Move b) { return a.from == b.from && a.to == b.to; }
std::array<std::vector<int>,81> neighbors;
std::uint64_t zobrist[81][7], side_key;
std::uint64_t splitmix(std::uint64_t& x) {
    x += 0x9e3779b97f4a7c15ULL;
    auto z = x; z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}
void setup() {
    std::uint64_t seed = 20261010;
    for (int s=0;s<81;++s) {
        for (int p=0;p<7;++p) zobrist[s][p]=splitmix(seed);
        for (int dr=-1;dr<=1;++dr) for (int dc=-1;dc<=1;++dc) {
            int r=s/9+dr,c=s%9+dc;
            if ((dr||dc)&&r>=0&&r<9&&c>=0&&c<9) neighbors[s].push_back(r*9+c);
        }
    }
    side_key=splitmix(seed);
}
int owner(int p) { return (p-1)/3; } // red=0, blue=1; never called on empty.
int kind(int p) { return (p-1)%3; }  // rock=0, paper=1, scissors=2.
bool beats(int a,int b) { return (kind(a)+2)%3 == kind(b); }
int base(int side) { return side==0 ? 72 : 8; }
int distance(int a,int b) { return std::max(std::abs(a/9-b/9),std::abs(a%9-b%9)); }
int square(const std::string& s) { return (9-(s[1]-'0'))*9+s[0]-'a'; }
std::string square(int s) { return std::string(1,char('a'+s%9))+char('9'-s/9); }
struct Position {
    std::array<int,81> board{};
    int count[2][3]{};
    int side=0, winner=-1;
    std::uint64_t hash=0;
    explicit Position(const json& state) {
        side=state.at("to_move")=="red" ? 0 : 1;
        for (int s=0;s<81;++s) {
            const auto& p=state.at("board").at(s/9).at(s%9);
            if (p.is_null()) continue;
            int who=p.at("side")=="red" ? 0 : 1;
            std::string type=p.at("kind");
            int k=type=="rock" ? 0 : type=="paper" ? 1 : 2;
            board[s]=1+who*3+k; ++count[who][k]; hash^=zobrist[s][board[s]];
        }
        if (side) hash^=side_key;
        if (!state.at("result").is_null()) winner=state.at("result").at("winner")=="red" ? 0 : 1;
    }
    std::vector<Move> moves() const {
        std::vector<Move> result; result.reserve(100);
        if (winner>=0) return result;
        for (int s=0;s<81;++s) if (board[s]&&owner(board[s])==side)
            for (int t:neighbors[s]) if (!board[t] || (owner(board[t])!=side&&beats(board[s],board[t])))
                result.push_back({s,t});
        return result;
    }
    Position play(Move m) const {
        Position p=*this;
        int moving=p.board[m.from], captured=p.board[m.to];
        p.hash^=zobrist[m.from][moving]^zobrist[m.to][moving]^side_key;
        if (captured) { --p.count[1-side][kind(captured)]; p.hash^=zobrist[m.to][captured]; }
        p.board[m.from]=0; p.board[m.to]=moving; p.side=1-side;
        if (m.to==base(1-side)||p.count[1-side][0]==0||p.count[1-side][1]==0||p.count[1-side][2]==0)
            p.winner=side;
        else if (p.moves().empty()) p.winner=side;
        return p;
    }
};
// Threats are heuristic; terminal wins are always decided by exact rules above.
int evaluate(const Position& p) {
    int score[2]{}, nearest[2]{9,9};
    for (int who=0;who<2;++who) for (int k=0;k<3;++k) {
        int n=p.count[who][k];
        score[who]+=100*n+(n>=3 ? 220 : n==2 ? 150 : 0);
    }
    for (int s=0;s<81;++s) {
        int piece=p.board[s]; if (!piece) continue;
        int who=owner(piece), threats=0, guards=0, mobility=0;
        for (int t:neighbors[s]) {
            int q=p.board[t];
            if (!q) ++mobility;
            else if (owner(q)!=who) {
                if (beats(q,piece)) ++threats;
                if (beats(piece,q)) ++mobility;
            } else if (kind(q)==(kind(piece)+2)%3) ++guards;
        }
        int d=distance(s,base(1-who)); nearest[who]=std::min(nearest[who],d);
        score[who]+=8*(8-d)+2*mobility;
        score[who]+=3*(8-distance(s,40));
        if (s==base(who)) score[who]+=30;
        if (threats) {
            // Last-of-type cannot be traded: losing it ends the game immediately.
            int cost=p.count[who][kind(piece)]==1 ? 650 : 85;
            if (guards && p.count[who][kind(piece)]>1) cost-=30;
            score[who]-=cost+10*(threats-1);
        }
    }
    for (int who=0;who<2;++who) score[who]+=50*(8-nearest[who]);
    return score[p.side]-score[1-p.side];
}
struct Entry { std::uint64_t key=0; int depth=-1, score=0, flag=0; Move best; };
std::array<Entry,1<<18> table;
struct Timeout {};
struct Search {
    Clock::time_point deadline;
    std::uint64_t nodes=0;
    int completed_depth=0, best_score=0;
    void tick() { if ((++nodes&127)==0 && Clock::now()>=deadline) throw Timeout{}; }
    static int pack(int s,int ply) { return s>WIN-1000 ? s+ply : s<-WIN+1000 ? s-ply : s; }
    static int unpack(int s,int ply) { return s>WIN-1000 ? s-ply : s<-WIN+1000 ? s+ply : s; }
    int order(const Position& p,Move m,Move preferred) {
        if (m==preferred) return 10000000;
        int capture=p.board[m.to], value=0;
        if (m.to==base(1-p.side)) value+=5000000;
        if (capture) value+=p.count[1-p.side][kind(capture)]==1 ? 4000000 : 10000;
        value+=30*(distance(m.from,base(1-p.side))-distance(m.to,base(1-p.side)));
        return value;
    }
    void sort(const Position& p,std::vector<Move>& moves,Move best={}) {
        std::stable_sort(moves.begin(),moves.end(),[&](Move a,Move b){return order(p,a,best)>order(p,b,best);});
    }
    bool enemy_has_immediate_win(const Position& p) {
        // Check possible winning captures/base entries for the other side, even
        // at a quiet-search horizon. Do not allow stand-pat under that threat.
        Position enemy=p; enemy.side=1-p.side;
        for (Move m:enemy.moves()) {
            int q=enemy.board[m.to];
            if (m.to==base(p.side)||(q&&enemy.count[p.side][kind(q)]==1)) return true;
        }
        return false;
    }
    int quiet(const Position& p,int alpha,int beta,int ply,int remaining) {
        tick();
        if (p.winner>=0) return p.winner==p.side ? WIN-ply : -WIN+ply;
        auto moves=p.moves(); if (moves.empty()) return -WIN+ply;
        int value=evaluate(p);
        if (remaining==0) return value;
        bool danger=enemy_has_immediate_win(p);
        if (!danger) {
            if (value>=beta) return value;
            alpha=std::max(alpha,value);
            moves.erase(std::remove_if(moves.begin(),moves.end(),[&](Move m){return !p.board[m.to]&&m.to!=base(1-p.side);}),moves.end());
        }
        sort(p,moves);
        for (Move m:moves) {
            int s=-quiet(p.play(m),-beta,-alpha,ply+1,remaining-1);
            if (s>=beta) return s;
            alpha=std::max(alpha,s);
        }
        return alpha;
    }
    int negamax(const Position& p,int depth,int alpha,int beta,int ply) {
        tick();
        if (p.winner>=0) return p.winner==p.side ? WIN-ply : -WIN+ply;
        if (depth==0) return quiet(p,alpha,beta,ply,QDEPTH);
        int original_alpha=alpha;
        Entry cached=table[p.hash&(table.size()-1)]; Move preferred;
        if (cached.depth>=0&&cached.key==p.hash) {
            preferred=cached.best;
            int s=unpack(cached.score,ply);
            if (cached.depth>=depth && (cached.flag==0||(cached.flag==1&&s>=beta)||(cached.flag==2&&s<=alpha))) return s;
        }
        auto moves=p.moves(); if (moves.empty()) return -WIN+ply;
        sort(p,moves,preferred);
        int value=-INF; Move best=moves.front();
        for (Move m:moves) {
            int s=-negamax(p.play(m),depth-1,-beta,-alpha,ply+1);
            if (s>value) { value=s; best=m; }
            alpha=std::max(alpha,s); if (alpha>=beta) break;
        }
        table[p.hash&(table.size()-1)]={p.hash,depth,pack(value,ply),value<=original_alpha ? 2 : value>=beta ? 1 : 0,best};
        return value;
    }
    Move choose(const Position& p,std::vector<Move> moves) {
        sort(p,moves); Move best=moves.front();
        // Exact one-ply wins need no search budget.
        for (Move m:moves) if (p.play(m).winner==p.side) return m;
        for (int depth=1;depth<=MAX_DEPTH;++depth) {
            if (Clock::now()>=deadline) break;
            try {
                sort(p,moves,best); int alpha=-INF; Move candidate=best;
                for (Move m:moves) {
                    int s=-negamax(p.play(m),depth-1,-INF,-alpha,1);
                    if (s>alpha) { alpha=s; candidate=m; }
                }
                best=candidate; best_score=alpha; completed_depth=depth;
                if (alpha>WIN-1000) break;
            } catch (const Timeout&) { break; } // Commit only completed iterations.
        }
        return best;
    }
};
#ifndef OTT_RULES_TEST
int main() {
    std::ios::sync_with_stdio(false); std::cin.tie(nullptr); setup();
    std::string line, match;
    while (std::getline(std::cin,line)) {
        auto started=Clock::now();
        try {
            auto m=json::parse(line); if (m.at("protocol_version")!=2) return 2;
            if (m.at("type")=="init") {
                match=m.at("match_id");
                std::cout<<json({{"protocol_version",2},{"type","ready"},{"match_id",match}}).dump()<<'\n'<<std::flush;
            } else if (m.at("type")=="observation"&&m.at("match_id")==match) {
                const auto& legal=m.at("payload").at("legal_moves");
                json action={{"resign",true}};
                if (!legal.empty()) {
                    Position p(m.at("payload").at("state"));
                    std::vector<Move> moves;
                    for (const auto& l:legal) moves.push_back({square(l.at("from").get<std::string>()),square(l.at("to").get<std::string>())});
                    int budget=m.at("timing").at("budget_ms").get<int>();
                    Search search; search.deadline=started+std::chrono::milliseconds(std::max(0,std::min(THINK_MS,budget*3/5-20)));
                    Move selected=search.choose(p,moves);
                    action={{"from",square(selected.from)},{"to",square(selected.to)}};
                    std::cerr<<"depth="<<search.completed_depth<<" nodes="<<search.nodes<<" score="<<search.best_score<<'\n'<<std::flush;
                }
                std::cout<<json({{"protocol_version",2},{"type","action"},{"match_id",match},
                    {"turn_id",m.at("turn_id")},{"state_version",m.at("state_version")},{"payload",action}}).dump()<<'\n'<<std::flush;
            }
        } catch (const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
    }
}
#endif
