// Stub minimo do Arduino.h para compilar e visualizar o firmware no PC.
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <deque>
#ifndef PI
#define PI 3.1415926535897932384626433832795
#endif
struct SerialStub {
  std::deque<int> q;
  void begin(unsigned long) {}
  int available() { return (int)q.size(); }
  int read() { int c = q.front(); q.pop_front(); return c; }
  void println(const char *s) { std::printf("[serial] %s\n", s); }
};
extern SerialStub Serial;
extern uint32_t g_millis;
inline uint32_t millis() { return g_millis; }
inline void delay(uint32_t) {}
inline long random(long lo, long hi) { return lo + std::rand() % (hi - lo); }
inline long random(long hi) { return std::rand() % hi; }
void setup();
void loop();
