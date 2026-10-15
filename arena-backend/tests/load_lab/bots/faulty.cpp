// faulty: bot lỗi cố ý để kiểm tra khâu chấm. Lab sinh bot.cpp bằng cách thêm
// "#define FAULT <n>" lên đầu file này.
//   1 crash       : truy cập con trỏ null ở lượt đầu
//   2 illegal     : đi 2 ô (sai luật)
//   3 timeout     : vòng lặp vô hạn ở lượt đầu (đốt hết quỹ giờ)
//   4 spam        : in debug ra stdout (phá giao thức)
//   5 memhog      : cấp phát 400 MiB khi khởi động
//   6 slow_start  : ngủ 3 giây trước khi trả ready (startup chỉ có 2 giây)
//   7 sneaky      : thử fork/thread/socket/đọc file hệ thống, ghi kết quả ra stderr, rồi chơi bình thường
//   8 stale       : trả turn_id sai
#include <cerrno>
#include <chrono>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>
#include <vector>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>
#include "json.hpp"
using json = nlohmann::json;
#ifndef FAULT
#define FAULT 1
#endif

static void probe() {
    pid_t pid = fork();
    std::cerr << "fork: " << (pid < 0 ? std::strerror(errno) : "OK (!)") << '\n';
    if (pid == 0) _exit(0);
    if (pid > 0) waitpid(pid, nullptr, 0);
    try { std::thread t([] {}); t.join(); std::cerr << "thread: OK (!)\n"; }
    catch (const std::exception& e) { std::cerr << "thread: " << e.what() << '\n'; }
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    std::cerr << "socket: " << (fd < 0 ? std::strerror(errno) : "OK (!)") << '\n';
    for (const char* path : {"/etc/hostname", "/proc/1/cmdline", "/sdk/json.hpp", "/source/bot.cpp", "/var/run/docker.sock"}) {
        std::ifstream f(path);
        std::cerr << "read " << path << ": " << (f.good() ? "readable" : "no") << '\n';
    }
    std::ofstream w("/artifact/pwned");
    std::cerr << "write /artifact: " << (w.good() ? "OK (!)" : "denied") << '\n';
    std::ofstream t("/tmp/bot-work/scratch");
    std::cerr << "write /tmp/bot-work: " << (t.good() ? "OK" : "denied") << '\n';
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    std::string line, match;
    if (FAULT == 5) { std::vector<char> big(400u << 20, 1); std::cerr << big[12345] << '\n'; }
    if (FAULT == 7) probe();
    while (std::getline(std::cin, line)) {
        try {
            const auto m = json::parse(line);
            if (m.at("type") == "init") {
                match = m.at("match_id");
                if (FAULT == 6) std::this_thread::sleep_for(std::chrono::seconds(3));
                std::cout << json({{"protocol_version",2},{"type","ready"},{"match_id",match}}).dump() << '\n' << std::flush;
                continue;
            }
            if (m.at("type") != "observation") continue;
            const auto& legal = m.at("payload").at("legal_moves");
            json action = legal.empty() ? json({{"resign", true}}) : legal.at(0);
            json turn = m.at("turn_id");
            if (FAULT == 1) { volatile int* p = nullptr; *p = 1; }
            if (FAULT == 2) action = {{"from", "a2"}, {"to", "a4"}};
            if (FAULT == 3) { volatile unsigned long x = 0; for (;;) ++x; }
            if (FAULT == 4) std::cout << "debug: thinking..." << '\n';
            if (FAULT == 8) turn = "999999";
            std::cout << json({{"protocol_version",2},{"type","action"},{"match_id",match},
                {"turn_id",turn},{"state_version",m.at("state_version")},{"payload",action}}).dump() << '\n' << std::flush;
        } catch (const std::exception& e) {
            std::cerr << e.what() << '\n';
            return 1;
        }
    }
}
