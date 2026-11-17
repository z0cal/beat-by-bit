// Stub do TFT_eSPI: rasteriza o sprite (8 bits RGB332 ou 4 bits com paleta) para visualizar.
#pragma once
#include <Arduino.h>
#include <vector>
#include <algorithm>

extern std::vector<uint32_t> g_frame;  // ultimo quadro enviado (RGB888)
extern int g_fw, g_fh;

class TFT_eSPI {
 public:
  void init() {}
  void setRotation(uint8_t) {}
  void fillScreen(uint32_t) {}
};

class TFT_eSprite : public TFT_eSPI {
 public:
  TFT_eSprite(TFT_eSPI *) {}
  void *setColorDepth(int8_t b) { bpp_ = b; return this; }
  void createPalette(const uint16_t *p, uint8_t n = 16) { for (int i = 0; i < n && i < 16; i++) pal_[i] = p[i]; }
  void *createSprite(int16_t w, int16_t h, uint8_t = 1) {
    w_ = w; h_ = h; buf_.assign((size_t)w * h, 0); return buf_.data();
  }
  void fillSprite(uint32_t c) { std::fill(buf_.begin(), buf_.end(), to332(c)); }
  void fillRect(int32_t x, int32_t y, int32_t w, int32_t h, uint32_t c) {
    uint8_t v = to332(c);
    for (int j = std::max(0, (int)y); j < std::min((int)h_, (int)(y + h)); j++)
      for (int i = std::max(0, (int)x); i < std::min((int)w_, (int)(x + w)); i++) buf_[j * w_ + i] = v;
  }
  void fillCircle(int32_t cx, int32_t cy, int32_t r, uint32_t c) {
    for (int dy = -r; dy <= r; dy++) {
      int dx = (int)std::floor(std::sqrt((double)r * r - (double)dy * dy) + 0.5);
      fillRect(cx - dx, cy + dy, 2 * dx + 1, 1, c);
    }
  }
  void fillRoundRect(int32_t x, int32_t y, int32_t w, int32_t h, int32_t r, uint32_t c) {
    for (int j = 0; j < h; j++) {
      int inset = 0;
      int d = j < r ? r - j : (j >= h - r ? j - (h - r - 1) : 0);
      if (d > 0) inset = r - (int)std::floor(std::sqrt((double)r * r - (double)(d - 1) * (d - 1)));
      fillRect(x + inset, y + j, w - 2 * inset, 1, c);
    }
  }
  void fillTriangle(int32_t x0, int32_t y0, int32_t x1, int32_t y1, int32_t x2, int32_t y2, uint32_t c) {
    int minx = std::min({x0, x1, x2}), maxx = std::max({x0, x1, x2});
    int miny = std::min({y0, y1, y2}), maxy = std::max({y0, y1, y2});
    auto edge = [](int ax, int ay, int bx, int by, int px, int py) { return (bx - ax) * (py - ay) - (by - ay) * (px - ax); };
    for (int y = miny; y <= maxy; y++)
      for (int x = minx; x <= maxx; x++) {
        int a = edge(x0, y0, x1, y1, x, y), b = edge(x1, y1, x2, y2, x, y), d = edge(x2, y2, x0, y0, x, y);
        if ((a >= 0 && b >= 0 && d >= 0) || (a <= 0 && b <= 0 && d <= 0)) fillRect(x, y, 1, 1, c);
      }
  }
  void pushSprite(int32_t, int32_t) {
    g_fw = w_; g_fh = h_; g_frame.resize(buf_.size());
    for (size_t i = 0; i < buf_.size(); i++) {
      uint8_t v = buf_[i];
      uint32_t r, g, b;
      if (bpp_ == 4) {
        uint16_t c = pal_[v & 15];
        r = ((c >> 11) & 31) * 255 / 31; g = ((c >> 5) & 63) * 255 / 63; b = (c & 31) * 255 / 31;
      } else {
        r = (v >> 5) * 255 / 7; g = ((v >> 2) & 7) * 255 / 7; b = (v & 3) * 255 / 3;
      }
      g_frame[i] = (r << 16) | (g << 8) | b;
    }
  }
 private:
  uint8_t to332(uint32_t c) const {
    if (bpp_ == 4) return (uint8_t)(c & 15);
    return (uint8_t)(((c >> 8) & 0xE0) | ((c >> 6) & 0x1C) | ((c >> 3) & 0x03));
  }
  int w_ = 0, h_ = 0, bpp_ = 8;
  uint16_t pal_[16] = {0};
  std::vector<uint8_t> buf_;
};
