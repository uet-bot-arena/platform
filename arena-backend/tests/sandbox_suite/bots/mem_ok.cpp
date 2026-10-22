// Xin và chạm 200 MiB (dưới giới hạn 256 MiB). Kỳ vọng: ok.
#include <cstring>
#include <vector>
static std::vector<char*> keep;
static int attack(int turn) {
  if (turn != 1) return 0;
  const std::size_t size = 200u * 1024 * 1024;
  char* block = new char[size];
  std::memset(block, 1, size);
  keep.push_back(block);
  return 0;
}
