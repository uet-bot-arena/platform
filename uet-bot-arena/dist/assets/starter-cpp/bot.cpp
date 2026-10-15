// Protocol skeleton only; not a complete OTTv2 SDK or game engine.
#include <iostream>
#include <string>
#include "json.hpp"
using json = nlohmann::json;

json choose_move(const json& state, const json& legal_moves) {
    (void)state;
    return legal_moves.empty() ? json(nullptr) : legal_moves.at(0);
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    std::string line;
    while (std::getline(std::cin, line)) {
        try {
            const auto message = json::parse(line);
            if (message.value("type", "") != "observation") continue;
            const auto payload = message.at("payload");
            const auto state = payload.at("state");
            const auto legal = payload.at("legal_moves");
            if (!state.is_object() || !legal.is_array()) {
                std::cerr << "Expected state object and legal_moves array\n";
                continue;
            }
            const json response = {
                {"protocol_version", 1}, {"type", "action"},
                {"turn_id", message.at("turn_id")},
                {"state_version", message.at("state_version")},
                {"payload", choose_move(state, legal)}
            };
            std::cout << response.dump() << '\n' << std::flush;
        } catch (const json::exception& error) {
            std::cerr << error.what() << '\n' << std::flush;
        }
    }
}
