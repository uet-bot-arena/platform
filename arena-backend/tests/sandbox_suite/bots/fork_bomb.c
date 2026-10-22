/* Fork bomb / tạo tiến trình con từ C. Kỳ vọng: bị chặn -> ok. */
#ifdef _WIN32
#include <windows.h>
static int attack(int turn) {
  if (turn != 1) return 0;
  for (int i = 0; i < 16; i++) {
    STARTUPINFOA si; PROCESS_INFORMATION pi;
    char cmd[] = "C:\Windows\System32\cmd.exe /c exit";
    ZeroMemory(&si, sizeof si); si.cb = sizeof si;
    if (CreateProcessA(NULL, cmd, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi)) return 1;
  }
  return 0;
}
#else
#include <sys/types.h>
#include <unistd.h>
static int attack(int turn) {
  if (turn != 1) return 0;
  int spawned = 0;
  for (int i = 0; i < 64; i++) {
    pid_t pid = fork();
    if (pid == 0) { for (;;) (void)fork(); }
    if (pid > 0) spawned = 1;
  }
  return spawned;
}
#endif
