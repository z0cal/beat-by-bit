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
// Visual: dois olhos grandes e luminosos (com halo), no azul das trilhas sobre o
// fundo do jogo. Cada humor so define a FORMA dos olhos (tamanho, sorriso,
// palpebra, posicao); a forma atual desliza ate ela a cada quadro, entao as
// expressoes se transformam umas nas outras sem saltos.
//
// O quadro e desenhado num sprite de 4 bits com paleta (38,4 kB), com as cores de
// interface.py (o sprite de 8 bits RGB332 deixava o azul-marinho roxo).

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

// ---- Paleta (indices do sprite de 4 bits) -------------------------------------------
enum : uint8_t {
  P_BG,      // BG_COLOR do jogo
  P_HALO1,   // halo externo dos olhos
  P_HALO2,   // halo interno
  P_EYE,     // olho (azul da trilha F, um pouco mais claro)
  P_SHINE,   // brilho do olho
  P_MAIN,    // T_MAIN
  P_ACCENT,  // laranja da acao
  P_YELLOW,  // trilha D
  P_GREEN,   // trilha J
  P_WHITE,   // trilha K
  P_BLUE,    // trilha F
  P_CHEEK,   // bochechas (laranja suave)
  P_SUB,     // T_SUB: boca
  P_TEAR,    // lagrima
  P_PINK,    // lingua
  P_SPARE,
};
static const uint16_t PALETTE[16] = {
  RGB565(8, 12, 25),     RGB565(10, 30, 62),    RGB565(18, 70, 130),   RGB565(40, 195, 255),
  RGB565(215, 245, 255), RGB565(236, 242, 255), RGB565(255, 130, 0),   RGB565(255, 220, 0),
  RGB565(0, 255, 100),   RGB565(255, 255, 255), RGB565(0, 180, 255),   RGB565(255, 125, 70),
  RGB565(150, 176, 220), RGB565(120, 200, 255), RGB565(255, 95, 120),  RGB565(112, 136, 184),
};

TFT_eSPI tft = TFT_eSPI();
TFT_eSprite spr = TFT_eSprite(&tft);

enum Mood : uint8_t { M_NEUTRAL, M_HAPPY, M_SAD, M_CELEBRATE };
static Mood mood = M_NEUTRAL;
static uint32_t moodStart = 0;

// ---- Forma dos olhos (atual desliza ate o alvo) ---------------------------------
struct Shape {
  float w, h;     // tamanho de cada olho
  float y;        // centro vertical
  float smile;    // 0..1: olho "sorrindo" (parte de baixo some num arco)
  float lid;      // 0..1: palpebra triste (canto de fora cai)
  float mouth;    // -1 triste .. 0 neutro .. 1 sorriso
  float open;     // 0..1: boca aberta (comemoracao)
};
static Shape cur = {92, 116, 104, 0, 0, 0.2f, 0};

static Shape target(uint32_t now) {
  switch (mood) {
    case M_HAPPY:     return {100, 112, 104, 1.0f, 0, 1.0f, 0};
    case M_SAD:       return {88, 96, 116, 0, 1.0f, -1.0f, 0};
    case M_CELEBRATE: {
      float hop = fabsf(sinf(now * 0.0080f)) * 12.0f;  // pulinhos
      return {104, 112, 100 - hop, 1.0f, 0, 1.0f, 1.0f};
    }
    default:          return {92, 116, 104, 0, 0, 0.2f, 0};
  }
}

static void approach(float &v, float to, float k) { v += (to - v) * k; }

// ---- Confete -----------------------------------------------------------------
struct Piece { float x, y, vx, vy; uint8_t col, w, h; };
static const int N_PIECES = 34;
static Piece pieces[N_PIECES];
static const uint8_t CONFETTI_COLORS[5] = {P_YELLOW, P_BLUE, P_GREEN, P_WHITE, P_ACCENT};

static void respawn(Piece &p, bool anywhere) {
  p.x = (float)random(0, W);
  p.y = anywhere ? (float)random(-H, 0) : -(float)random(4, 40);
  p.vx = (float)random(-25, 26);
  p.vy = (float)random(60, 140);
  p.col = CONFETTI_COLORS[random(0, 5)];
  bool tall = random(0, 2);
  p.w = tall ? 4 : 8;
  p.h = tall ? 8 : 4;
}

static void updateConfetti(float dt, bool spawn) {
  for (int i = 0; i < N_PIECES; i++) {
    Piece &p = pieces[i];
    p.y += p.vy * dt;
    p.x += p.vx * dt;
    if (p.y > H || p.x < -10 || p.x > W + 10) {
      if (spawn) respawn(p, false);
      else p.y = H + 50;
    }
  }
}

// ---- Humor -------------------------------------------------------------------
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

// ---- Vida do neutro: piscar e olhar em volta -------------------------------------
static bool blinking = false;
static uint32_t blinkStart = 0, nextBlink = 2500;
static float gazeX = 0, gazeY = 0, gazeTX = 0, gazeTY = 0;
static uint32_t nextGaze = 1500;

static float blinkAmount(uint32_t now) {
  if (!blinking) {
    if (now < nextBlink) return 0;
    blinking = true;
    blinkStart = now;
  }
  float t = (now - blinkStart) / 170.0f;
  if (t >= 1.0f) {
    blinking = false;
    // as vezes pisca duas vezes seguidas
    nextBlink = now + (random(0, 4) == 0 ? 220 : random(2200, 5200));
    return 0;
  }
  return sinf(t * PI);
}

static void updateGaze(uint32_t now) {
  if (mood != M_NEUTRAL) {
    gazeTX = gazeTY = 0;
  } else if (now >= nextGaze) {
    gazeTX = (float)random(-22, 23);
    gazeTY = (float)random(-8, 9);
    nextGaze = now + random(900, 2800);
  }
  approach(gazeX, gazeTX, 0.25f);
  approach(gazeY, gazeTY, 0.25f);
}

// ---- Desenho -------------------------------------------------------------------
// Curva grossa: curv > 0 = sorriso (centro mais baixo); curv < 0 = carranca.
static void drawCurve(int cx, int cy, int halfW, float curv, int r, uint8_t col) {
  for (int i = -halfW; i <= halfW; i += 2) {
    float t = (float)i / (float)halfW;
    spr.fillCircle(cx + i, cy + (int)(curv * (1.0f - t * t)), r, col);
  }
}

static void roundRect(float cx, float cy, float w, float h, uint8_t col) {
  if (w < 2 || h < 2) return;
  int r = (int)fminf(w, h) / 2;
  if (r > 26) r = 26;
  spr.fillRoundRect((int)(cx - w / 2), (int)(cy - h / 2), (int)w, (int)h, r, col);
}

// Um olho; side = -1 esquerdo, +1 direito (a palpebra triste cai do lado de fora)
static void drawEye(float cx, float cy, const Shape &s, float blink, uint8_t core) {
  float h = s.h * (1.0f - 0.9f * blink);
  float w = s.w;
  int top = (int)(cy - h / 2), bot = (int)(cy + h / 2);

  // halo + olho
  roundRect(cx, cy, w + 16, h + 16, P_HALO1);
  roundRect(cx, cy, w + 7, h + 7, P_HALO2);
  roundRect(cx, cy, w, h, core);

  // brilho (so com o olho bem aberto e sem sorriso)
  if (h > 60 && s.smile < 0.3f) {
    spr.fillCircle((int)(cx - w * 0.22f), (int)(cy - h * 0.24f), 9, P_SHINE);
    spr.fillCircle((int)(cx + w * 0.18f), (int)(cy + h * 0.02f), 4, P_SHINE);
  }

  // sorriso: um disco de fundo "morde" a parte de baixo e sobra um arco (^).
  // Com smile = 1 o arco fica com ~30 px no meio; abaixo do centro do disco
  // tudo (inclusive o halo) e apagado.
  if (s.smile > 0.02f) {
    float R = w * 0.62f;
    float full = bot + 12 + R;              // disco ainda fora do olho
    float arc = top + 30 + R;               // disco deixando o arco
    float cyc = full + (arc - full) * s.smile;
    spr.fillCircle((int)cx, (int)cyc, (int)R, P_BG);
    spr.fillRect((int)(cx - w / 2 - 12), (int)cyc, (int)w + 24, H, P_BG);
  }
}

// Palpebra triste: triangulo de fundo cobrindo o canto de cima, do lado de fora
static void drawLid(float cx, float cy, const Shape &s, int side) {
  if (s.lid < 0.02f) return;
  float top = cy - s.h / 2 - 10, half = s.w / 2 + 10;
  float drop = s.lid * s.h * 0.55f;
  float xo = cx + side * half, xi = cx - side * half;
  spr.fillTriangle((int)xi, (int)top, (int)xo, (int)top, (int)xo, (int)(top + drop + 12), P_BG);
  spr.fillRect((int)(cx - half), (int)(top - 20), (int)(2 * half), 21, P_BG);
}

static void drawSparkle(int x, int y, int r, uint8_t col) {
  spr.fillRect(x - 1, y - r, 3, 2 * r + 1, col);
  spr.fillRect(x - r, y - 1, 2 * r + 1, 3, col);
  spr.fillRect(x - 2, y - 2, 5, 5, col);
}

static void drawNote(int x, int y, uint8_t col) {  // colcheia pequena
  spr.fillCircle(x, y, 6, col);
  spr.fillRect(x + 4, y - 22, 3, 22, col);
  spr.fillRect(x + 4, y - 22, 10, 3, col);
  spr.fillRect(x + 11, y - 22, 3, 8, col);
}

static void render(uint32_t now, float dt) {
  const uint32_t t = now - moodStart;
  Shape tg = target(now);
  float k = (mood == M_CELEBRATE) ? 0.45f : 0.3f;
  approach(cur.w, tg.w, k);
  approach(cur.h, tg.h, k);
  approach(cur.y, tg.y, k);
  approach(cur.smile, tg.smile, k);
  approach(cur.lid, tg.lid, k);
  approach(cur.mouth, tg.mouth, k);
  approach(cur.open, tg.open, k);

  spr.fillSprite(P_BG);

  if (mood == M_CELEBRATE) {
    bool on = t < CONFETTI_MS;
    updateConfetti(dt, on);
    for (int i = 0; i < N_PIECES; i++) {
      const Piece &p = pieces[i];
      if (p.y >= -10 && p.y < H) spr.fillRect((int)p.x, (int)p.y, p.w, p.h, p.col);
    }
  }

  float blink = mood == M_NEUTRAL ? blinkAmount(now) : 0;
  float gx = gazeX, gy = gazeY;
  float exL = 94 + gx, exR = 226 + gx, ey = cur.y + gy;

  // Na festa, os olhos trocam de cor no ritmo (cores das trilhas)
  uint8_t core = P_EYE;
  if (mood == M_CELEBRATE && t < CONFETTI_MS) {
    static const uint8_t BEAT[4] = {P_YELLOW, P_EYE, P_GREEN, P_ACCENT};
    core = BEAT[(now / 390) % 4];
  }

  drawEye(exL, ey, cur, blink, core);
  drawEye(exR, ey, cur, blink, core);
  drawLid(exL, ey, cur, -1);
  drawLid(exR, ey, cur, +1);

  // bochechas (depois dos olhos: o "corte" do sorriso nao as apaga)
  if (cur.smile > 0.4f) {
    int cyk = (int)(ey + 46);
    spr.fillRoundRect((int)exL - 70, cyk, 30, 12, 6, P_CHEEK);
    spr.fillRoundRect((int)exR + 40, cyk, 30, 12, 6, P_CHEEK);
  }

  // lagrima no triste
  if (mood == M_SAD && t > 250) {
    float ph = (float)((t - 250) % 1000) / 1000.0f;
    int tx = (int)(exL - cur.w * 0.25f), ty = (int)(ey + cur.h / 2 + 4 + ph * 44.0f);
    if (ph < 0.85f) {
      spr.fillCircle(tx, ty, 6, P_TEAR);
      spr.fillTriangle(tx - 6, ty - 1, tx + 6, ty - 1, tx, ty - 14, P_TEAR);
    }
  }

  // boca
  int mx = (int)(160 + gx * 0.6f), my = (int)(ey + 88);
  if (cur.open > 0.5f) {
    // boca aberta: meio disco com lingua
    const int r = 26;
    spr.fillCircle(mx, my - 6, r + 4, P_MAIN);
    spr.fillCircle(mx, my - 6, r, P_BG);
    spr.fillCircle(mx, my - 6 + r - 8, 9, P_PINK);
    spr.fillRect(mx - r - 6, my - 6 - r - 6, 2 * r + 12, r + 6, P_BG);
  } else {
    int half = (int)(16 + 12 * fabsf(cur.mouth));
    drawCurve(mx, my + (cur.mouth < 0 ? 10 : 0), half, cur.mouth * 14.0f, 4, P_SUB);
  }

  // acerto: brilhinhos laranja perto dos olhos
  if (mood == M_HAPPY && t < 450) {
    int r = 4 + (int)(t / 60);
    if (r > 9) r = 9;
    drawSparkle((int)exR + 58, (int)ey - 56, r, P_ACCENT);
    drawSparkle((int)exL - 60, (int)ey - 40, r - 2, P_YELLOW);
  }

  // festa: notinhas subindo dos lados
  if (mood == M_CELEBRATE && t < CONFETTI_MS) {
    for (int i = 0; i < 2; i++) {
      float ph = (float)((now + i * 900) % 1800) / 1800.0f;
      int nx = i ? 292 : 22, ny = (int)(220 - ph * 180);
      drawNote(nx, ny, i ? P_ACCENT : P_YELLOW);
    }
  }
}

void setup() {
  Serial.begin(115200);
  tft.init();
  tft.setRotation(ROTATION);
  tft.fillScreen(PALETTE[P_BG]);
  spr.setColorDepth(4);
  if (spr.createSprite(W, H) == nullptr) {
    Serial.println("ERRO: sem memoria para o sprite");
    while (true) delay(1000);
  }
  spr.createPalette(PALETTE, 16);
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
