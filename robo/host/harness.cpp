#include <Arduino.h>
#include <TFT_eSPI.h>
#include <string>
SerialStub Serial;
uint32_t g_millis = 0;
std::vector<uint32_t> g_frame;
int g_fw = 0, g_fh = 0;

static void dump(const std::string &name) {
  FILE *f = std::fopen(name.c_str(), "wb");
  std::fprintf(f, "P6\n%d %d\n255\n", g_fw, g_fh);
  for (uint32_t p : g_frame) { std::fputc((p >> 16) & 255, f); std::fputc((p >> 8) & 255, f); std::fputc(p & 255, f); }
  std::fclose(f);
}
static void run_until(uint32_t t) { while (g_millis < t) { g_millis += 33; loop(); } }

int main() {
  std::srand(7);
  setup();
  run_until(1000);               dump("f_neutro.ppm");
  Serial.q.push_back('H');       run_until(g_millis + 200);  dump("f_feliz.ppm");
  run_until(g_millis + 1000);    dump("f_apos_feliz.ppm");   // deve voltar ao neutro
  Serial.q.push_back('S');       run_until(g_millis + 700);  dump("f_triste.ppm");
  run_until(g_millis + 1000);    dump("f_apos_triste.ppm");
  Serial.q.push_back('C');       run_until(g_millis + 1500); dump("f_festa.ppm");
  run_until(g_millis + 3000);    dump("f_festa2.ppm");
  Serial.q.push_back('H');       run_until(g_millis + 300);  dump("f_festa_ignora_H.ppm"); // H ignorado
  run_until(g_millis + 24000);   dump("f_festa_calma.ppm");  // apos 25 s sem confete
  Serial.q.push_back('N');       run_until(g_millis + 300);  dump("f_volta_neutro.ppm");
  Serial.q.push_back('?');       run_until(g_millis + 40);
  std::puts("harness ok");
}
