// Beat by Bit - Robo
// ESP32-WROOM-32D + tela ST7789V 240x320 (SPI), em paisagem (320x240).
//
// O PC (pc/interface.py) manda comandos de 1 byte pela serial USB (115200 baud):
//   'H'  acerto            -> feliz por ~0,9 s
//   'S'  erro              -> triste por ~0,9 s
//   'C'  fim da musica     -> comemoracao (qualquer resultado), ate chegar 'N'
//   'N'  volta ao neutro
//   '?'  responde "BBB-ROBO OK" (teste de conexao)
// Sem comandos, o robo fica no neutro: pisca e olha em volta.
//
// O quadro e desenhado num sprite de 8 bits (RGB332, 76,8 kB) e enviado inteiro
// a cada quadro (~30 fps), sem piscar. So usa primitivas simples do TFT_eSPI.

#include <Arduino.h>
#include <TFT_eSPI.h>
#include <math.h>

#ifndef ROTATION
#define ROTATION 1
#endif

#define RGB565(r, g, b) \
  ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | ((b) >> 3)))

static const int W = 320;
static const int H = 240;
static const uint32_t FRAME_MS = 33;       // ~30 fps
static const uint32_t REACT_MS = 900;      // duracao de feliz/triste
static const uint32_t CONFETTI_MS = 25000; // depois disso, so o rosto feliz

// Paleta do jogo (mesmas cores de interface.py)
static const uint16_t C_BG     = RGB565(8, 12, 25);
static const uint16_t C_EDGE   = RGB565(55, 100, 180);
static const uint16_t C_PANEL  = RGB565(18, 30, 66);
static const uint16_t C_EYE    = RGB565(0, 220, 255);
static const uint16_t C_MOUTH  = RGB565(236, 242, 255);
static const uint16_t C_WHITE  = RGB565(255, 255, 255);
static const uint16_t C_CHEEK  = RGB565(255, 95, 130);
static const uint16_t C_TEAR   = RGB565(90, 170, 255);
static const uint16_t C_ACCENT = RGB565(255, 130, 0);
static const uint16_t C_YELLOW = RGB565(255, 220, 0);
static const uint16_t C_GREEN  = RGB565(0, 255, 100);
static const uint16_t C_DIM    = RGB565(70, 90, 130);

// Layout do rosto
static const int EW = 74, EH = 84;            // olho
static const int EX_L = 100, EX_R = 220;      // centros dos olhos
static const int EY = 104, MY = 176;          // altura dos olhos e da boca

TFT_eSPI tft = TFT_eSPI();
TFT_eSprite spr = TFT_eSprite(&tft);

enum Mood : uint8_t { M_NEUTRAL, M_HAPPY, M_SAD, M_CELEBRATE };
static Mood mood = M_NEUTRAL;
static uint32_t moodStart = 0;

// ---- Confete -------------------------------------------------------------
struct Piece { float x, y, vx, vy; uint16_t col; uint8_t w, h; };
static const int N_PIECES = 36;
static Piece pieces[N_PIECES];
static const uint16_t CONFETTI_COLORS[5] = {C_YELLOW, C_EYE, C_GREEN, C_WHITE, C_ACCENT};

static void respawn(Piece &p, bool anywhere) {
  p.x = (float)random(0, W);
  p.y = anywhere ? (float)random(-H, 0) : -(float)random(4, 40);
  p.vx = (float)random(-30, 31);
  p.vy = (float)random(70, 160);
  p.col = CONFETTI_COLORS[random(0, 5)];
  p.w = (uint8_t)random(4, 9);
  p.h = (uint8_t)random(4, 9);
}

static void updateConfetti(float dt, bool spawn) {
  for (int i = 0; i < N_PIECES; i++) {
    Piece &p = pieces[i];
    p.y += p.vy * dt;
    p.x += p.vx * dt;
    if (p.y > H || p.x < -10 || p.x > W + 10) {
      if (spawn) respawn(p, false);
      else p.y = H + 50;  // estacionado fora da tela
    }
  }
}

// ---- Humor ---------------------------------------------------------------
static void setMood(Mood m) {
  mood = m;
  moodStart = millis();
  if (m == M_CELEBRATE)
    for (int i = 0; i < N_PIECES; i++) respawn(pieces[i], true);
}

static void handleSerial() {
  while (Serial.available() > 0) {
    int c = Serial.read();
    switch (c) {
      case 'H': case 'h': if (mood != M_CELEBRATE) setMood(M_HAPPY); break;
      case 'S': case 's': if (mood != M_CELEBRATE) setMood(M_SAD); break;
      case 'C': case 'c': if (mood != M_CELEBRATE) setMood(M_CELEBRATE); break;
      case 'N': case 'n': if (mood != M_NEUTRAL) setMood(M_NEUTRAL); break;
      case '?': Serial.println("BBB-ROBO OK"); break;
      default: break;  // ignora \r, \n e o resto
    }
  }
}

// ---- Vida do neutro: piscar e olhar em volta --------------------------------
static bool blinking = false;
static uint32_t blinkStart = 0, nextBlink = 2500;
static float gazeX = 0, gazeTarget = 0;
static uint32_t nextGaze = 1500;

static float blinkAmount(uint32_t now) {
  if (!blinking) {
    if (now < nextBlink) return 0;
    blinking = true;
    blinkStart = now;
  }
  float t = (now - blinkStart) / 140.0f;
  if (t >= 1.0f) {
    blinking = false;
    nextBlink = now + random(2200, 5200);
    return 0;
  }
  return sinf(t * PI);
}

static void updateGaze(uint32_t now) {
  if (now >= nextGaze) {
    gazeTarget = (float)random(-10, 11);
    nextGaze = now + random(900, 2600);
  }
  gazeX += (gazeTarget - gazeX) * 0.15f;
}

// ---- Desenho -------------------------------------------------------------
static float easeOut(float t) {
  if (t < 0) t = 0;
  if (t > 1) t = 1;
  return 1.0f - (1.0f - t) * (1.0f - t);
}

// Curva grossa: curv > 0 = sorriso (centro mais baixo); curv < 0 = "^" ou carranca.
static void drawCurve(int cx, int cy, int halfW, float curv, int r, uint16_t col) {
  for (int i = -halfW; i <= halfW; i += 2) {
    float t = (float)i / (float)halfW;
    spr.fillCircle(cx + i, cy + (int)(curv * (1.0f - t * t)), r, col);
  }
}

static void drawEyeOpen(int cx, int cy, int eh, uint16_t col) {
  int rad = eh / 2 < 16 ? eh / 2 : 16;
  spr.fillRoundRect(cx - EW / 2, cy - eh / 2, EW, eh, rad, col);
  if (eh > 44) spr.fillRoundRect(cx - EW / 2 + 10, cy - eh / 2 + 10, 18, 14, 5, C_WHITE);
}

static void drawCheeks(int dy) {
  spr.fillCircle(EX_L - 28, 150 + dy, 12, C_CHEEK);
  spr.fillCircle(EX_R + 28, 150 + dy, 12, C_CHEEK);
}

static void drawFace(uint32_t now) {
  const uint32_t t = now - moodStart;
  uint16_t ball = C_DIM;  // bolinha da antena

  switch (mood) {
    case M_NEUTRAL: {
      int eh = EH - (int)((EH - 6) * blinkAmount(now));
      int gx = (int)gazeX;
      drawEyeOpen(EX_L + gx, EY, eh, C_EYE);
      drawEyeOpen(EX_R + gx, EY, eh, C_EYE);
      drawCurve(W / 2 + gx / 2, MY, 28, 5.0f, 4, C_MOUTH);
      break;
    }
    case M_HAPPY: {
      float k = easeOut(t / 150.0f);
      int dy = -(int)(sinf(fminf(t / 260.0f, 1.0f) * PI) * 12.0f);
      drawCheeks(dy);
      drawCurve(EX_L, EY + 8 + dy, EW / 2, -20.0f * k, 6, C_EYE);
      drawCurve(EX_R, EY + 8 + dy, EW / 2, -20.0f * k, 6, C_EYE);
      drawCurve(W / 2, MY - 10 + dy, 46, 28.0f * k, 5, C_MOUTH);
      ball = C_ACCENT;
      break;
    }
    case M_SAD: {
      float k = easeOut(t / 300.0f);
      int dy = (int)(6.0f * k);
      int ey = EY + 10 + dy, eh = EH - 14, top = ey - eh / 2;
      drawEyeOpen(EX_L, ey, eh, C_EYE);
      drawEyeOpen(EX_R, ey, eh, C_EYE);
      // palpebras caidas (cantos de fora mais baixos)
      spr.fillTriangle(EX_L - EW / 2 - 2, top - 2, EX_L + EW / 2 + 2, top - 2,
                       EX_L - EW / 2 - 2, top + 36, C_PANEL);
      spr.fillTriangle(EX_R - EW / 2 - 2, top - 2, EX_R + EW / 2 + 2, top - 2,
                       EX_R + EW / 2 + 2, top + 36, C_PANEL);
      // lagrima
      float ph = (float)(t % 1100) / 1100.0f;
      if (t > 250 && ph < 0.9f) {
        int tx = EX_L - EW / 2 + 14, ty = ey + eh / 2 + (int)(ph * 46.0f);
        spr.fillCircle(tx, ty, 5, C_TEAR);
        spr.fillTriangle(tx - 5, ty - 1, tx + 5, ty - 1, tx, ty - 12, C_TEAR);
      }
      drawCurve(W / 2, MY + 16 + dy, 36, -22.0f * k, 5, C_MOUTH);
      break;
    }
    case M_CELEBRATE: {
      int dy = -(int)(fabsf(sinf(now * 0.0075f)) * 16.0f);
      uint16_t eyec = ((now / 220) & 1) ? C_YELLOW : C_EYE;
      drawCheeks(dy);
      drawCurve(EX_L, EY + 8 + dy, EW / 2, -22.0f, 6, eyec);
      drawCurve(EX_R, EY + 8 + dy, EW / 2, -22.0f, 6, eyec);
      // boca aberta: meio disco com lingua
      const int mr = 34, mx = W / 2, my = MY - 14 + dy;
      spr.fillCircle(mx, my, mr + 4, C_MOUTH);
      spr.fillCircle(mx, my, mr, C_BG);
      spr.fillCircle(mx, my + mr - 10, 9, C_CHEEK);
      spr.fillRect(mx - mr - 6, my - mr - 6, 2 * mr + 12, mr + 6, C_PANEL);
      ball = ((now / 150) & 1) ? C_ACCENT : C_WHITE;
      break;
    }
  }

  // antena
  spr.fillRect(W / 2 - 2, 8, 4, 14, C_EDGE);
  spr.fillCircle(W / 2, 8, 7, ball);
}

static void render(uint32_t now, float dt) {
  spr.fillSprite(C_BG);
  spr.fillRoundRect(4, 20, W - 8, H - 26, 28, C_EDGE);   // moldura
  spr.fillRoundRect(8, 24, W - 16, H - 34, 25, C_PANEL); // painel
  drawFace(now);
  if (mood == M_CELEBRATE) {
    bool on = (now - moodStart) < CONFETTI_MS;
    updateConfetti(dt, on);
    for (int i = 0; i < N_PIECES; i++) {
      const Piece &p = pieces[i];
      if (p.y >= 0 && p.y < H) spr.fillRect((int)p.x, (int)p.y, p.w, p.h, p.col);
    }
  }
}

void setup() {
  Serial.begin(115200);
  tft.init();
  tft.setRotation(ROTATION);
  tft.fillScreen(C_BG);
  spr.setColorDepth(8);
  if (spr.createSprite(W, H) == nullptr) {
    Serial.println("ERRO: sem memoria para o sprite");
    while (true) delay(1000);
  }
  setMood(M_NEUTRAL);
  Serial.println("BBB-ROBO OK");
}

void loop() {
  static uint32_t last = 0;
  handleSerial();
  uint32_t now = millis();
  if (now - last < FRAME_MS) return;
  float dt = (now - last) / 1000.0f;
  last = now;
  if ((mood == M_HAPPY || mood == M_SAD) && now - moodStart >= REACT_MS) setMood(M_NEUTRAL);
  updateGaze(now);
  render(now, dt);
  spr.pushSprite(0, 0);
}
