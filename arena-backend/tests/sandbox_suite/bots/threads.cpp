// Mở 4 thread bận 400 ms để lấy thêm CPU. Nếu CPU dùng > 1,5 x thời gian thực thì BREACH. Kỳ vọng: ok.
#include <atomic>
#include <chrono>
#include <thread>
#include <vector>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
static double cpu_seconds() {
  FILETIME created, exited, kernel, user;
  GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user);
  ULARGE_INTEGER k, u;
  k.LowPart = kernel.dwLowDateTime; k.HighPart = kernel.dwHighDateTime;
  u.LowPart = user.dwLowDateTime; u.HighPart = user.dwHighDateTime;
  return (k.QuadPart + u.QuadPart) / 1e7;
}
#else
#include <time.h>
static double cpu_seconds() {
  timespec t;
  clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &t);
  return t.tv_sec + t.tv_nsec / 1e9;
}
#endif
static int attack(int turn) {
  if (turn != 1) return 0;
  const auto start = std::chrono::steady_clock::now();
  const double cpu = cpu_seconds();
  std::atomic<bool> stop{false};
  std::vector<std::thread> pool;
  try {
    for (int i = 0; i < 4; i++) pool.emplace_back([&stop] { volatile unsigned long n = 0; while (!stop) n = n + 1; });
  } catch (...) {
  }
  while (std::chrono::steady_clock::now() - start < std::chrono::milliseconds(400)) {}
  stop = true;
  for (auto& t : pool) t.join();
  const double wall = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
  return cpu_seconds() - cpu > 1.5 * wall;
}
