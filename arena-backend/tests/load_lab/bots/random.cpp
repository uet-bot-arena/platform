// random: chọn ngẫu nhiên một nước hợp lệ (mốc thấp nhất).
#include <iostream>
#include <random>
#include <string>
#include "json.hpp"
using json = nlohmann::json;

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    std::mt19937 rng(20261015u);
    std::string line, match;
    while (std::getline(std::cin, line)) {
        try {
            const auto m = json::parse(line);
            if (m.at("protocol_version") != 2) return 2;
            if (m.at("type") == "init") {
                match = m.at("match_id");
                std::cout << json({{"protocol_version",2},{"type","ready"},{"match_id",match}}).dump() << '\n' << std::flush;
            } else if (m.at("type") == "observation" && m.at("match_id") == match) {
                const auto& legal = m.at("payload").at("legal_moves");
                json action = {{"resign", true}};
                if (!legal.empty()) action = legal.at(rng() % legal.size());
                std::cout << json({{"protocol_version",2},{"type","action"},{"match_id",match},
                    {"turn_id",m.at("turn_id")},{"state_version",m.at("state_version")},{"payload",action}}).dump() << '\n' << std::flush;
            }
        } catch (const json::exception& e) {
            std::cerr << e.what() << '\n';
            return 1;
        }
    }
}
