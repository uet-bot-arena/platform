// OTTv2 protocol v2; single-threaded, persistent C++17 process.
#include <iostream>
#include <string>
#include "json.hpp"
using json = nlohmann::json;

json choose_move(const json& state, const json& legal) {
    // Prefer an immediate base capture, then captures, then advance towards base.
    const std::string side = state.at("to_move");
    const std::string base = side == "red" ? "i9" : "a1";
    json best = legal.at(0);
    int best_score = -100000;
    for (const auto& move : legal) {
        const std::string to = move.at("to");
        const int row = 9 - (to[1] - '0'), col = to[0] - 'a';
        int score = side == "red" ? 8 - row + col : row + 8 - col;
        if (!state.at("board").at(row).at(col).is_null()) score += 50;
        if (to == base) score += 10000;
        if (score > best_score) { best = move; best_score = score; }
    }
    return best;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    std::string line, match;
    while (std::getline(std::cin, line)) {
        try {
            const auto m = json::parse(line);
            if (m.at("protocol_version") != 2) return 2;
            if (m.at("type") == "init") {
                match = m.at("match_id");
                std::cout << json({{"protocol_version",2},{"type","ready"},{"match_id",match}}).dump() << '\n' << std::flush;
            } else if (m.at("type") == "observation" && m.at("match_id") == match) {
                const auto legal = m.at("payload").at("legal_moves");
                const auto action = legal.empty() ? json({{"resign",true}}) : choose_move(m.at("payload").at("state"), legal);
                std::cout << json({{"protocol_version",2},{"type","action"},{"match_id",match},
                    {"turn_id",m.at("turn_id")},{"state_version",m.at("state_version")},{"payload",action}}).dump() << '\n' << std::flush;
            }
        } catch (const json::exception& e) {
            std::cerr << e.what() << '\n' << std::flush;
            return 1;
        }
    }
}
