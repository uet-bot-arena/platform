#include <algorithm>
#include <chrono>
#include <cmath>
#include <iostream>
#include <string>
#include "json.hpp"
using json = nlohmann::json;
using Clock = std::chrono::steady_clock;

json choose_move(const json& payload, Clock::time_point deadline) {
    const auto& legal = payload.at("legal_moves");
    if (legal.empty()) return json{{"resign", true}};
    const auto& state = payload.at("state");
    const auto& board = state.at("board");
    const std::string side = state.at("to_move");
    const std::string enemy = side == "red" ? "blue" : "red";
    const std::string base = payload.at("bases").at(enemy);
    const int base_row = 9 - (base[1] - '0'), base_col = base[0] - 'a';
    json best = legal.at(0); // Always keep a legal fallback.
    int best_score = -10000000;
    for (const auto& move : legal) {
        if (Clock::now() >= deadline) break;
        const std::string to = move.at("to");
        const int row = 9 - (to[1] - '0'), col = to[0] - 'a';
        int score = -10 * std::max(std::abs(row - base_row), std::abs(col - base_col));
        const auto& target = board.at(row).at(col);
        if (!target.is_null()) {
            score += 500;
            const std::string kind = target.at("kind");
            if (payload.at("counts").at(enemy).at(kind).get<int>() == 1)
                score += 10000;
        }
        if (to == base) score += 1000000;
        if (score > best_score) { best_score = score; best = move; }
    }
    return best;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    std::string line, match;
    while (std::getline(std::cin, line)) {
        const auto received = Clock::now(); // After getline, before JSON parsing.
        try {
            const auto message = json::parse(line);
            if (message.at("protocol_version") != 2) return 2;
            if (message.at("type") == "init") {
                match = message.at("match_id").get<std::string>();
                std::cout << json{{"protocol_version",2},{"type","ready"},
                    {"match_id",match}}.dump() << '\n' << std::flush;
            } else if (message.at("type") == "observation") {
                if (match.empty() || message.at("match_id") != match) return 3;
                const int budget = message.at("timing").at("budget_ms").get<int>();
                const int slice = budget / 80;
                const int reserve = std::max(5, slice / 20);
                // Share the bank across future turns (or independent benchmark cases).
                const int think = std::max(0, std::min(250, slice - reserve));
                const auto deadline = received + std::chrono::milliseconds(think);
                const auto action = choose_move(message.at("payload"), deadline);
                std::cout << json{{"protocol_version",2},{"type","action"},
                    {"match_id",message.at("match_id")},
                    {"turn_id",message.at("turn_id")},
                    {"state_version",message.at("state_version")},
                    {"payload",action}}.dump() << '\n' << std::flush;
            }
        } catch (const std::exception& error) {
            std::cerr << "Cannot process input: " << error.what() << '\n';
            return 1;
        }
    }
    return 0;
}
