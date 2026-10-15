// onestep: heuristic 1 nước kiểu bot sinh viên thường viết.
// 1) Có nước thắng ngay thì đi. 2) Loại các nước để đối thủ thắng ngay ở lượt sau.
// 3) Chấm điểm: ăn quân (nhất là quân cuối của một loại), tiến về căn cứ, tránh đứng
//    cạnh quân khắc mình. Không tìm kiếm sâu.
#include <algorithm>
#include <array>
#include <iostream>
#include <random>
#include <string>
#include <vector>
#include "json.hpp"
using json = nlohmann::json;

struct Move { int from, to; };
int owner(int p) { return (p - 1) / 3; }             // 0 = red, 1 = blue
int kind(int p) { return (p - 1) % 3; }              // 0 rock, 1 paper, 2 scissors
bool beats(int a, int b) { return (kind(a) + 2) % 3 == kind(b); }
int base(int side) { return side == 0 ? 72 : 8; }    // a1 = 72, i9 = 8
int dist(int a, int b) { return std::max(std::abs(a / 9 - b / 9), std::abs(a % 9 - b % 9)); }
int sq(const std::string& s) { return (9 - (s[1] - '0')) * 9 + (s[0] - 'a'); }
std::string name(int s) { return std::string(1, char('a' + s % 9)) + char('9' - s / 9); }

struct Pos {
    std::array<int, 81> b{};
    int cnt[2][3]{};
    std::vector<Move> moves(int side) const {
        std::vector<Move> out;
        for (int s = 0; s < 81; ++s) {
            if (!b[s] || owner(b[s]) != side) continue;
            for (int dr = -1; dr <= 1; ++dr) for (int dc = -1; dc <= 1; ++dc) {
                int r = s / 9 + dr, c = s % 9 + dc;
                if ((!dr && !dc) || r < 0 || r > 8 || c < 0 || c > 8) continue;
                int t = r * 9 + c;
                if (!b[t] || (owner(b[t]) != side && beats(b[s], b[t]))) out.push_back({s, t});
            }
        }
        return out;
    }
    bool wins(Move m, int side) const {   // nước này có thắng ngay không
        if (m.to == base(1 - side)) return true;
        int q = b[m.to];
        return q && cnt[1 - side][kind(q)] == 1;
    }
    Pos play(Move m) const {
        Pos p = *this;
        if (p.b[m.to]) --p.cnt[owner(p.b[m.to])][kind(p.b[m.to])];
        p.b[m.to] = p.b[m.from]; p.b[m.from] = 0;
        return p;
    }
};

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    std::mt19937 rng(7u);
    std::string line, match;
    while (std::getline(std::cin, line)) {
        try {
            const auto m = json::parse(line);
            if (m.at("protocol_version") != 2) return 2;
            if (m.at("type") == "init") {
                match = m.at("match_id");
                std::cout << json({{"protocol_version",2},{"type","ready"},{"match_id",match}}).dump() << '\n' << std::flush;
                continue;
            }
            if (m.at("type") != "observation" || m.at("match_id") != match) continue;
            const auto& st = m.at("payload").at("state");
            const int side = st.at("to_move") == "red" ? 0 : 1;
            Pos p;
            for (int s = 0; s < 81; ++s) {
                const auto& c = st.at("board").at(s / 9).at(s % 9);
                if (c.is_null()) continue;
                int who = c.at("side") == "red" ? 0 : 1;
                std::string k = c.at("kind");
                int kk = k == "rock" ? 0 : k == "paper" ? 1 : 2;
                p.b[s] = 1 + who * 3 + kk; ++p.cnt[who][kk];
            }
            auto moves = p.moves(side);
            json action = {{"resign", true}};
            if (!moves.empty()) {
                Move best = moves[0]; long best_score = -1000000000L;
                for (Move mv : moves) {
                    long score = 0;
                    if (p.wins(mv, side)) score = 100000000L;
                    else {
                        Pos q = p.play(mv);
                        bool danger = false;
                        for (Move r : q.moves(1 - side)) if (q.wins(r, 1 - side)) { danger = true; break; }
                        if (danger) score -= 10000000L;
                        if (q.moves(1 - side).empty()) score += 50000000L;
                        int cap = p.b[mv.to];
                        if (cap) score += p.cnt[1 - side][kind(cap)] == 2 ? 400 : 150;
                        score += 20 * (dist(mv.from, base(1 - side)) - dist(mv.to, base(1 - side)));
                        // phạt nếu ô mới bị quân khắc mình áp sát
                        int me = q.b[mv.to];
                        for (int dr = -1; dr <= 1; ++dr) for (int dc = -1; dc <= 1; ++dc) {
                            int r = mv.to / 9 + dr, c = mv.to % 9 + dc;
                            if ((!dr && !dc) || r < 0 || r > 8 || c < 0 || c > 8) continue;
                            int e = q.b[r * 9 + c];
                            if (e && owner(e) != side && beats(e, me)) score -= q.cnt[side][kind(me)] == 1 ? 3000 : 120;
                        }
                        score += rng() % 7;
                    }
                    if (score > best_score) { best_score = score; best = mv; }
                }
                action = {{"from", name(best.from)}, {"to", name(best.to)}};
            }
            std::cout << json({{"protocol_version",2},{"type","action"},{"match_id",match},
                {"turn_id",m.at("turn_id")},{"state_version",m.at("state_version")},{"payload",action}}).dump() << '\n' << std::flush;
        } catch (const std::exception& e) {
            std::cerr << e.what() << '\n';
            return 1;
        }
    }
}
