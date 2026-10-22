// Xin và chạm 512 MiB. Kỳ vọng: crash.
#include <cstring>
#include <vector>
static std::vector<char*> keep;
static int attack(int turn) {
  if (turn != 1) return 0;
  const std::size_t size = 512u * 1024 * 1024;
  char* block = new char[size];
  std::memset(block, 1, size);
  keep.push_back(block);
  return 0;
}
