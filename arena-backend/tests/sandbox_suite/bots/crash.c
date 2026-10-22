/* Truy cập con trỏ NULL. Kỳ vọng: crash. */
static int attack(int turn) {
  volatile int *pointer = 0;
  *pointer = turn;
  return 0;
}
