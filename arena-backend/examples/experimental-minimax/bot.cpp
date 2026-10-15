#include <chrono>
#include <cmath>
#include <iostream>
#include <limits>
#include <memory>
#include <unordered_map>
#include "experimental.hpp"

using json = nlohmann::json;
using Clock = std::chrono::steady_clock;
using arena_experimental::Rules;
using arena_experimental::other;
using arena_experimental::pos;

// Raise THINK_MS after measuring your own benchmark. No worker deadline extension.
constexpr int THINK_MS = 250;
constexpr int MAX_DEPTH = 6;
constexpr unsigned NODE_LIMIT = 20000;
constexpr double WIN = 1000000.0;

struct Timeout {};

// Same repetition identity as rules-v1: relative cooldown/shield lifetimes,
// resources and side to move. Absolute ply/turn counters do not identify a position.
std::string position_key(const json& s) {
    json board = s.at("board");
    for (auto& row : board) for (auto& p : row) if (!p.is_null()) {
        const int turn = s.at("turns").at(p.at("side").get<std::string>());
        p["shield_until"] = std::max(0, p.at("shield_until").get<int>() - turn);
        json cd = json::object();
        for (auto it = p.at("cooldowns").begin(); it != p.at("cooldowns").end(); ++it)
            if (it.value().get<int>() > turn) cd[it.key()] = it.value().get<int>() - turn;
        p["cooldowns"] = std::move(cd);
    }
    return json::array({board, s.at("to_move"), s.at("energy"), s.at("control")}).dump();
}

int distance(const std::string& a, const std::string& b) {
    auto [r, c] = pos(a); auto [tr, tc] = pos(b);
    return std::max(std::abs(r - tr), std::abs(c - tc));
}

class Search {
    const Rules& rules;
    std::string root_side;
    Clock::time_point deadline;
    bool repetition;
    unsigned nodes = 0;
    std::unordered_map<std::string, int> seen;

    void check() const {
        if (nodes >= NODE_LIMIT || Clock::now() >= deadline) throw Timeout{};
    }

    double evaluate(const json& s) const {
        double score = rules.evaluate_objectives(s, root_side);
        const auto& v = rules.definition.at("victory");
        for (const auto& side : {root_side, other(root_side)}) {
            const double sign = side == root_side ? 1.0 : -1.0;
            const auto counts = rules.counts(s, side);
            if (v.at("type_eliminated").get<bool>()) {
                for (auto it = counts.begin(); it != counts.end(); ++it) {
                    const int n = it.value();
                    score -= sign * (n == 1 ? 85 : n == 2 ? 20 : 0);
                }
            }
            const int target = v.at("control_target");
            if (target > 0) {
                const int control = s.at("control").at(side);
                score += sign * 120.0 * control / target;
            }
            for (const auto& zone : rules.definition.at("zones")) {
                if (zone.at("kind") == "control" && target == 0) continue;
                int nearest = 9;
                for (int r = 0; r < 9; ++r) for (int c = 0; c < 9; ++c) {
                    const auto& p = s.at("board").at(r).at(c);
                    if (!p.is_null() && p.at("side") == side)
                        nearest = std::min(nearest, distance(arena_experimental::square(r, c), zone.at("square")));
                }
                double weight = zone.at("kind") == "control" ? 14.0 : 6.0;
                if (zone.at("kind") == "energy" && s.at("energy").at(side) == rules.definition.at("energy").at("cap")) weight *= 0.3;
                score += sign * weight * (9 - nearest) * zone.at("gain").get<int>();
            }
            // Legal threats include the current custom movement and cooldowns.
            json view = s; view["to_move"] = side;
            const auto actions = rules.legal_actions(view);
            std::vector<std::string> threatened;
            for (const auto& a : actions) if (a.at("type") == "move") {
                auto [r, c] = pos(a.at("to")); const auto& p = s.at("board").at(r).at(c);
                if (p.is_null() || p.at("shield_until").get<int>() > s.at("turns").at(other(side)).get<int>()) continue;
                const auto id = p.at("id").get<std::string>();
                if (std::find(threatened.begin(), threatened.end(), id) != threatened.end()) continue;
                threatened.push_back(id);
                auto enemy_counts = rules.counts(s, other(side));
                double value = v.at("type_eliminated").get<bool>() && enemy_counts.at(p.at("kind").get<std::string>()) == 1 ? 110.0 : 24.0;
                score += sign * value;
            }
        }
        return score;
    }

    double ordering(const json& s, const json& a) const {
        const std::string side = s.at("to_move");
        double score = -2.0 * a.at("energy_cost").get<int>();
        if (a.at("type") == "shield") return score - 4;
        const std::string from = a.at("from"), to = a.at("to");
        auto [r, c] = pos(to); const auto& p = s.at("board").at(r).at(c);
        if (!p.is_null()) {
            bool shield = p.at("shield_until").get<int>() > s.at("turns").at(other(side)).get<int>();
            score += shield ? 40 : 300;
            if (!shield && rules.definition.at("victory").at("type_eliminated").get<bool>() &&
                rules.counts(s, other(side)).at(p.at("kind").get<std::string>()) == 1) score += 50000;
        }
        if (rules.definition.at("victory").at("base_capture").get<bool>()) {
            const std::string base = side == "red" ? "i9" : "a1";
            if (to == base) score += 100000;
            score += 2 * (distance(from, base) - distance(to, base));
        }
        for (const auto& zone : rules.definition.at("zones")) {
            const bool active = zone.at("kind") == "energy" || rules.definition.at("victory").at("control_target").get<int>() > 0;
            if (!active) continue;
            score += (zone.at("kind") == "control" ? 15 : 7) *
                (distance(from, zone.at("square")) - distance(to, zone.at("square")));
        }
        return score;
    }

    std::vector<json> ordered(const json& s, const json& legal, const std::string& preferred = "") {
        std::vector<std::pair<double, json>> ranked;
        for (const auto& a : legal) {
            check();
            ranked.emplace_back(ordering(s, a) + (a.at("id") == preferred ? 1000000 : 0), a);
        }
        std::stable_sort(ranked.begin(), ranked.end(), [](const auto& a, const auto& b) { return a.first > b.first; });
        std::vector<json> out;
        for (auto& item : ranked) out.push_back(std::move(item.second));
        return out;
    }

    double minimax(const json& s, int depth, double alpha, double beta, int ply) {
        check(); ++nodes;
        if (!s.at("result").is_null()) {
            const auto& winner = s.at("result").at("winner");
            return winner.is_null() ? 0 : winner == root_side ? WIN - ply : -WIN + ply;
        }
        std::string key;
        if (repetition) {
            key = position_key(s);
            if (++seen[key] >= 3) { --seen[key]; return 0; }
        }
        // Unwind path counters even when a time limit aborts the iteration.
        struct Guard {
            std::unordered_map<std::string, int>& seen; const std::string& key;
            ~Guard() { if (!key.empty()) { auto it = seen.find(key); if (--it->second == 0) seen.erase(it); } }
        } guard{seen, key};
        if (s.at("ply").get<int>() >= 512) return 0;
        if (depth == 0) { auto score = evaluate(s); check(); return score; }
        const auto legal = rules.legal_actions(s);
        // With no_legal_moves disabled a bot still cannot send a legal action.
        if (legal.empty()) return s.at("to_move") == root_side ? -WIN + ply : WIN - ply;
        const bool maximize = s.at("to_move") == root_side;
        double best = maximize ? -2 * WIN : 2 * WIN;
        for (const auto& a : ordered(s, legal)) {
            check();
            auto child = rules.apply(s, json{{"action_id", a.at("id")}});
            double value = minimax(child, depth - 1, alpha, beta, ply + 1);
            if (maximize) { best = std::max(best, value); alpha = std::max(alpha, best); }
            else { best = std::min(best, value); beta = std::min(beta, best); }
            if (alpha >= beta) break;
        }
        return best;
    }

public:
    Search(const Rules& r, std::string side, Clock::time_point end,
           const std::unordered_map<std::string, int>& history, bool draw_rule)
        : rules(r), root_side(std::move(side)), deadline(end), repetition(draw_rule), seen(history) {}

    json choose(const json& state, const json& legal) {
        // This fallback is valid even if parsing/validation consumed the budget.
        json best_action{{"action_id", legal.at(0).at("id")}};
        double best_score = -2 * WIN; int completed = 0;
        try {
            for (int depth = 1; depth <= MAX_DEPTH; ++depth) {
                double score = -2 * WIN, alpha = -2 * WIN;
                json candidate = best_action;
                for (const auto& a : ordered(state, legal, best_action.at("action_id"))) {
                    check();
                    auto child = rules.apply(state, json{{"action_id", a.at("id")}});
                    double value = minimax(child, depth - 1, alpha, 2 * WIN, 1);
                    if (value > score) { score = value; candidate = json{{"action_id", a.at("id")}}; }
                    alpha = std::max(alpha, score);
                    // A proven immediate win is the maximum possible root value.
                    if (score >= WIN - 1) break;
                }
                best_action = candidate; best_score = score; completed = depth;
                if (std::abs(score) >= WIN - depth) break;
            }
        } catch (const Timeout&) {}
        std::cerr << "minimax depth=" << completed << " nodes=" << nodes << " score=" << best_score << '\n';
        return best_action;
    }
};

int main() {
    std::ios::sync_with_stdio(false); std::cin.tie(nullptr);
    std::string line, match;
    std::unique_ptr<Rules> rules;
    int previous_ply = -2;
    bool benchmark = false, draw_rule = false;
    std::unordered_map<std::string, int> history;
    while (std::getline(std::cin, line)) {
        const auto received = Clock::now();
        try {
            const auto m = json::parse(line);
            if (m.at("protocol_version") != 3) throw std::runtime_error("This bot requires SDK v3 / Experimental.");
            if (m.at("type") == "init") {
                match = m.at("match_id"); const auto& p = m.at("payload");
                const auto& pack = p.at("ruleset");
                rules = std::make_unique<Rules>(pack.at("definition"));
                benchmark = p.contains("match") && p.at("match").value("kind", std::string()) == "benchmark";
                draw_rule = p.contains("match") && p.at("match").contains("adjudication") &&
                    p.at("match").at("adjudication").value("repetition_count", json()) == 3;
                history.clear(); previous_ply = -2;
                std::cout << json{{"protocol_version", 3}, {"type", "ready"}, {"match_id", match},
                    {"ruleset_hash", pack.at("hash")}, {"capabilities", json::array({"rules-v1"})}}.dump() << '\n' << std::flush;
            } else if (m.at("type") == "observation") {
                if (!rules || m.at("match_id") != match) throw std::runtime_error("Unexpected match/init.");
                const auto& p = m.at("payload"); const auto& state = p.at("state");
                const auto& legal = p.at("legal_actions");
                if (rules->legal_actions(state) != legal) throw std::runtime_error("Server / SDK legal-action mismatch.");
                const int ply = state.at("ply");
                // Benchmark observations are independent fixtures, not a game history.
                if (benchmark || ply != previous_ply + 2) history.clear();
                if (!benchmark) ++history[position_key(state)];
                const int budget = std::max(0, std::min(THINK_MS, m.at("timing").at("budget_ms").get<int>() / 80 - 5));
                Search search(*rules, state.at("to_move"), received + std::chrono::milliseconds(budget), history, !benchmark && draw_rule);
                json action = legal.empty() ? json{{"resign", true}} : search.choose(state, legal);
                // Serialize/flush the chosen action before bookkeeping or any next search.
                std::cout << json{{"protocol_version", 3}, {"type", "action"}, {"match_id", match},
                    {"turn_id", m.at("turn_id")}, {"state_version", m.at("state_version")}, {"payload", action}}.dump() << '\n' << std::flush;
                if (!benchmark) ++history[position_key(rules->apply(state, action))];
                previous_ply = ply;
            } else throw std::runtime_error("Unexpected message type.");
        } catch (const std::exception& e) {
            std::cerr << e.what() << '\n' << std::flush;
            return 1;
        }
    }
}
