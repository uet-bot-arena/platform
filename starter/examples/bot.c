/* OTTv2 starter C11, giao thuc v2. Chi can sua ham choose_move ben duoi.
 * Phan con lai doc JSON (thu vien jsmn, giay phep MIT giu trong jsmn.h), dien san ban co,
 * danh sach nuoc hop le va can cu doi phuong, roi in cau tra loi dung giao thuc. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "jsmn.h"

#define MAX_SIDE 15
#define MAX_MOVES 512

typedef struct { char side, kind; } Piece;  /* side: 'r' do, 'b' xanh, 0 o trong; kind: 'r' bua, 'p' bao, 's' keo */
typedef struct {
  int rows, cols;                 /* kich thuoc ban; hang 0 la hang mang so lon nhat */
  Piece board[MAX_SIDE][MAX_SIDE];
  char me;                        /* ben dang toi luot: 'r' hoac 'b' */
  char enemy_base[4];             /* vi du "i9" */
} Game;
typedef struct { char from[4], to[4]; } Move;  /* vi du from "a2", to "a3" */

static int row_of(const Game *g, const char *cell) { return g->rows - atoi(cell + 1); }
static int col_of(const char *cell) { return cell[0] - 'a'; }

/* ===== SUA HAM NAY: tra ve chi so cua nuoc chon trong moves[0..count-1] ===== */
static int choose_move(const Game *g, const Move *moves, int count) {
  int best = 0;
  long best_score = -1000000;
  int br = row_of(g, g->enemy_base), bc = col_of(g->enemy_base);
  for (int i = 0; i < count; i++) {
    int r = row_of(g, moves[i].to), c = col_of(moves[i].to);
    int dr = abs(r - br), dc = abs(c - bc);
    long score = 0;
    if (strcmp(moves[i].to, g->enemy_base) == 0) score += 10000;  /* vao can cu doi phuong: thang */
    if (g->board[r][c].side) score += 50;                            /* o co quan dich: nuoc an quan */
    score -= dr > dc ? dr : dc;                                      /* cang gan can cu dich cang tot */
    if (score > best_score) { best_score = score; best = i; }
  }
  return best;
}
/* ===== Het phan can sua ===== */

static char line[65538];
static jsmntok_t tok[16384];
static int ntok;

static int eq(int i, const char *s) {
  return i >= 0 && tok[i].type == JSMN_STRING && (int)strlen(s) == tok[i].end - tok[i].start
         && strncmp(line + tok[i].start, s, strlen(s)) == 0;
}
static int skip(int i) {  /* chi so token dung sau gia tri bat dau tai i */
  int end = tok[i].end;
  for (i++; i < ntok && tok[i].start < end; i++) {}
  return i;
}
static int get(int obj, const char *key) {  /* gia tri cua khoa trong object, -1 neu khong co */
  if (obj < 0 || tok[obj].type != JSMN_OBJECT) return -1;
  int i = obj + 1;
  for (int k = 0; k < tok[obj].size; k++) {
    if (eq(i, key)) return i + 1;
    i = skip(i + 1);
  }
  return -1;
}
static int item(int arr, int n) {  /* phan tu thu n cua mang, -1 neu khong co */
  if (arr < 0 || tok[arr].type != JSMN_ARRAY || n >= tok[arr].size) return -1;
  int i = arr + 1;
  while (n--) i = skip(i);
  return i;
}
static void copy(int i, char *out, int cap) {
  int len = i < 0 ? 0 : tok[i].end - tok[i].start;
  if (len >= cap) len = cap - 1;
  if (len > 0) memcpy(out, line + tok[i].start, (size_t)len);
  out[len < 0 ? 0 : len] = 0;
}
static void raw(int i) {  /* in lai nguyen van mot gia tri JSON (chuoi giu dau ngoac kep) */
  if (tok[i].type == JSMN_STRING) putchar('"');
  fwrite(line + tok[i].start, 1, (size_t)(tok[i].end - tok[i].start), stdout);
  if (tok[i].type == JSMN_STRING) putchar('"');
}

static int read_game(int payload, Game *g, Move *moves, int *count) {
  int state = get(payload, "state"), board = get(state, "board"), legal = get(payload, "legal_moves"), bases = get(payload, "bases");
  if (board < 0 || legal < 0 || bases < 0) return 0;
  g->rows = tok[board].size;
  g->cols = g->rows ? tok[item(board, 0)].size : 0;
  if (g->rows > MAX_SIDE || g->cols > MAX_SIDE) return 0;
  g->me = eq(get(state, "to_move"), "red") ? 'r' : 'b';
  copy(get(bases, g->me == 'r' ? "blue" : "red"), g->enemy_base, (int)sizeof g->enemy_base);
  for (int r = 0; r < g->rows; r++) {
    int row = item(board, r);
    for (int c = 0; c < g->cols; c++) {
      int cell = item(row, c), side = get(cell, "side"), kind = get(cell, "kind");
      g->board[r][c].side = side < 0 ? 0 : line[tok[side].start];
      g->board[r][c].kind = kind < 0 ? 0 : line[tok[kind].start];
    }
  }
  *count = tok[legal].size < MAX_MOVES ? tok[legal].size : MAX_MOVES;
  for (int i = 0; i < *count; i++) {
    int move = item(legal, i);
    copy(get(move, "from"), moves[i].from, (int)sizeof moves[i].from);
    copy(get(move, "to"), moves[i].to, (int)sizeof moves[i].to);
  }
  return 1;
}

int main(void) {
  static Game game;
  static Move moves[MAX_MOVES];
  while (fgets(line, sizeof line, stdin)) {
    jsmn_parser parser;
    jsmn_init(&parser);
    ntok = jsmn_parse(&parser, line, strlen(line), tok, sizeof tok / sizeof tok[0]);
    if (ntok < 1) return 2;
    int version = get(0, "protocol_version"), type = get(0, "type"), match = get(0, "match_id");
    if (version < 0 || line[tok[version].start] != '2' || type < 0 || match < 0) return 2;
    printf("{\"protocol_version\":2,\"match_id\":");
    raw(match);
    if (eq(type, "init")) {
      printf(",\"type\":\"ready\"}");
    } else {
      int turn = get(0, "turn_id"), state_version = get(0, "state_version"), count = 0;
      if (turn < 0 || state_version < 0 || !read_game(get(0, "payload"), &game, moves, &count)) return 2;
      printf(",\"type\":\"action\",\"turn_id\":");
      raw(turn);
      printf(",\"state_version\":");
      raw(state_version);
      if (count == 0) {
        printf(",\"payload\":{\"resign\":true}}");
      } else {
        int k = choose_move(&game, moves, count);
        printf(",\"payload\":{\"from\":\"%s\",\"to\":\"%s\"}}", moves[k].from, moves[k].to);
      }
    }
    putchar('\n');
    fflush(stdout);
  }
  return 0;
}
