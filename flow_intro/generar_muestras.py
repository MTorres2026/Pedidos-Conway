#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genera las MUESTRAS de la intro de FLOW:
  - flow_intro.gif   animacion para ver en el movil / compartir
  - flow_intro.mp4   video para ponerlo de splash en Android
  - flow_intro.png   fotograma final (para icono / miniatura)

NO es necesario ejecutar esto para usar la intro: el archivo principal es
flow_intro.html (o flow_intro.svg). Esto solo crea copias en video.

Uso (con WinPython o Python normal):
    python -m pip install Pillow imageio imageio-ffmpeg
    python flow_intro/generar_muestras.py

El dibujo se hace con Pillow, sin internet y sin fuentes externas.
"""

import math
import os
import random

from PIL import Image, ImageChops, ImageDraw, ImageFont

# ---------------------------- Config --------------------------------------
BASE_W, BASE_H = 900, 460
OUT_W, OUT_H = 720, 368          # tamano del video / gif
SS = 2                           # supersampling (2 = bordes suaves)
W, H = OUT_W * SS, OUT_H * SS
K = (OUT_W / BASE_W) * SS         # escala: coords base -> pixels de trabajo
DUR = 5.5                        # segundos
FPS = 20
N_FRAMES = int(DUR * FPS)

C = {
    "fondo1": (11, 18, 38), "fondo2": (23, 42, 77),
    "tinta": (255, 215, 154), "brillo": (255, 243, 214),
    "gato": (242, 166, 90), "gato_osc": (217, 133, 63),
    "rosa": (255, 157, 176), "texto": (207, 224, 255),
    "rojo": (233, 96, 79), "oscuro": (35, 48, 77),
}

AQUI = os.path.dirname(os.path.abspath(__file__))
FUENTE = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FUENTE_NEGRITA = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
if not os.path.exists(FUENTE):                      # Windows
    FUENTE = r"C:\Windows\Fonts\segoeui.ttf"
    FUENTE_NEGRITA = r"C:\Windows\Fonts\seguisb.ttf"


# ---------------------------- Utilidades ----------------------------------
def P(x, y):
    return (int(round(x * K)), int(round(y * K)))


def L(v):
    return max(1, int(round(v * K)))


def lerp(a, b, t):
    return a + (b - a) * t


def mezcla(c1, c2, t):
    return tuple(int(lerp(c1[i], c2[i], t)) for i in range(3))


def suave(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def vent(t, a, b):
    """Progreso 0..1 de la animacion que va de a a b (fraccion de DUR)."""
    if b <= a:
        return 1.0 if t >= b else 0.0
    return suave((t - a) / (b - a))


def bezier(p0, p1, p2, p3, n=40):
    pts = []
    for i in range(n + 1):
        u = i / n
        v = 1 - u
        pts.append((
            v**3*p0[0] + 3*v*v*u*p1[0] + 3*v*u*u*p2[0] + u**3*p3[0],
            v**3*p0[1] + 3*v*v*u*p1[1] + 3*v*u*u*p2[1] + u**3*p3[1],
        ))
    return pts


def quad(p0, p1, p2, n=40):
    pts = []
    for i in range(n + 1):
        u = i / n
        v = 1 - u
        pts.append((v*v*p0[0] + 2*v*u*p1[0] + u*u*p2[0],
                    v*v*p0[1] + 2*v*u*p1[1] + u*u*p2[1]))
    return pts


def recortar(puntos, frac):
    """Devuelve la parte de la polilinea hasta `frac` de su longitud."""
    if frac <= 0:
        return []
    if frac >= 1:
        return list(puntos)
    seg = []
    total = 0.0
    for a, b in zip(puntos, puntos[1:]):
        d = math.hypot(b[0]-a[0], b[1]-a[1])
        seg.append(d)
        total += d
    objetivo, acum, salida = total * frac, 0.0, [puntos[0]]
    for i, d in enumerate(seg):
        if acum + d >= objetivo:
            f = 0 if d == 0 else (objetivo - acum) / d
            a, b = puntos[i], puntos[i+1]
            salida.append((lerp(a[0], b[0], f), lerp(a[1], b[1], f)))
            return salida
        acum += d
        salida.append(puntos[i+1])
    return salida


def trazar(d, puntos, color, ancho, alpha=255):
    if len(puntos) < 2:
        return
    pp = [P(*p) for p in puntos]
    w = L(ancho)
    # La mascara del gato es modo "L": necesita un entero, no RGBA.
    relleno = color if isinstance(color, int) else color + (alpha,)
    d.line(pp, fill=relleno, width=w, joint="curve")
    r = w // 2
    for x, y in (pp[0], pp[-1]):
        d.ellipse([x-r, y-r, x+r, y+r], fill=relleno)


def trazar_brillo(capa, puntos, color, ancho):
    """Trazo con halo: tres pasadas sobre una capa RGBA."""
    d = ImageDraw.Draw(capa)
    for mult, al in ((3.6, 38), (2.2, 85), (1.0, 255)):
        trazar(d, puntos, color, ancho * mult, al)


def texto_espaciado(d, texto, cx, cy, size, espacio, color, alpha, bold=False):
    if alpha <= 0:
        return
    ruta = FUENTE_NEGRITA if bold else FUENTE
    try:
        font = ImageFont.truetype(ruta, L(size))
    except OSError:
        font = ImageFont.load_default()
    anchos = [d.textlength(ch, font=font) for ch in texto]
    esp = L(espacio)
    total = sum(anchos) + esp * max(0, len(texto) - 1)
    x, y = P(cx, cy)[0] - total / 2, P(cx, cy)[1]
    for ch, wch in zip(texto, anchos):
        d.text((x, y), ch, font=font, fill=color + (int(alpha),), anchor="lm")
        x += wch + esp


# ---------------------------- Escenario -----------------------------------
def fondo():
    peque = Image.new("RGB", (120, 62))
    px = peque.load()
    for y in range(62):
        for x in range(120):
            dx = (x / 120 - .30) / .95
            dy = (y / 62 - .18) / .95
            t = min(1.0, math.sqrt(dx*dx + dy*dy))
            px[x, y] = mezcla(C["fondo2"], C["fondo1"], t * .92)
    return peque.resize((W, H), Image.BICUBIC).convert("RGBA")


FONDO = fondo()
random.seed(7)
PALOMITAS = [(random.uniform(4, 96), random.uniform(0, 100),
              random.uniform(2.5, 5.5), random.uniform(0, 1),
              random.uniform(.55, 1.15)) for _ in range(22)]


def dibujar_palomitas(img, t):
    d = ImageDraw.Draw(img, "RGBA")
    for xpor, y0, r, fase, vel in PALOMITAS:
        y = ((y0 + t * vel * 26 + fase * 100) % 130) - 15
        x = xpor + math.sin((t + fase) * 2.1) * .8
        cy = (y / 100) * BASE_H
        alt = 1 - abs(cy - BASE_H/2) / (BASE_H/2)
        a = int(78 * max(0, alt))
        if a <= 0:
            continue
        cx, cyy = P(x, cy)
        rr = L(r)
        d.ellipse([cx-rr, cyy-rr, cx+rr, cyy+rr], fill=C["brillo"] + (a,))


def capa_gato(t, alpha, dy):
    capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # Mascara de las partes con degradado (cuerpo, cabeza, orejas, cola, patas)
    mask = Image.new("L", (W, H), 0)
    m = ImageDraw.Draw(mask)
    meneo = math.sin(t * 2.4) * 8
    cola = bezier((205, 290), (275 + meneo, 285), (288 + meneo, 205), (250, 178), 30)
    trazar(m, cola, 255, 26, 255)
    m.ellipse([*P(150-76, 292-72), *P(150+76, 292+72)], fill=255)
    m.polygon([P(88,128), P(80,58), P(137,104)], fill=255)
    m.polygon([P(212,128), P(222,58), P(164,104)], fill=255)
    m.ellipse([*P(150-72, 158-72), *P(150+72, 158+72)], fill=255)
    m.ellipse([*P(120-30, 352-15), *P(120+30, 352+15)], fill=255)
    m.ellipse([*P(178-30, 352-15), *P(178+30, 352+15)], fill=255)

    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    y_top, y_bot = P(0, 55)[1], P(0, 370)[1]
    for y in range(y_top, y_bot + 1):
        f = (y - y_top) / max(1, y_bot - y_top)
        gd.line([(0, y), (W, y)], fill=mezcla(C["gato"], C["gato_osc"], f) + (255,))
    capa.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(capa, "RGBA")
    # orejas internas
    d.polygon([P(94,116), P(89,76), P(123,106)], fill=C["rosa"] + (215,))
    d.polygon([P(206,116), P(212,76), P(177,106)], fill=C["rosa"] + (215,))
    # ojos
    d.ellipse([*P(124-17,150-20), *P(124+17,150+20)], fill=(255, 248, 236, 255))
    d.ellipse([*P(176-17,150-20), *P(176+17,150+20)], fill=(255, 248, 236, 255))
    d.ellipse([*P(127-8,152-8), *P(127+8,152+8)], fill=C["oscuro"] + (255,))
    d.ellipse([*P(179-8,152-8), *P(179+8,152+8)], fill=C["oscuro"] + (255,))
    d.ellipse([*P(130-2.6,149-2.6), *P(130+2.6,149+2.6)], fill=(255,255,255,255))
    d.ellipse([*P(182-2.6,149-2.6), *P(182+2.6,149+2.6)], fill=(255,255,255,255))
    # parpadeo (cierra los ojos un instante)
    parp = math.sin(t * 1.5)
    if parp > .985:
        d.rectangle([*P(106,128), *P(142,172)], fill=C["gato"] + (255,))
        d.rectangle([*P(158,128), *P(194,172)], fill=C["gato"] + (255,))
    # nariz y boca
    d.polygon([P(143,176), P(157,176), P(150,184)], fill=C["rosa"] + (255,))
    trazar(d, quad((150,184), (138,196), (128,188), 12), (122,74,44), 3)
    trazar(d, quad((150,184), (162,196), (172,188), 12), (122,74,44), 3)
    # bigotes
    for a, b in (((112,180),(68,174)), ((112,188),(70,192)),
                 ((188,180),(232,174)), ((188,188),(230,192))):
        trazar(d, [a, b], C["brillo"], 2.6, 230)
    # cajita de palomitas
    d.rounded_rectangle([*P(222,300), *P(288,362)], radius=L(6), fill=C["rojo"] + (255,))
    d.rounded_rectangle([*P(222,300), *P(288,314)], radius=L(4), fill=C["tinta"] + (255,))
    for cx, cy, r in ((238,300,10),(255,297,12),(272,301,10),(246,291,8),(266,290,8)):
        d.ellipse([*P(cx-r,cy-r), *P(cx+r,cy+r)], fill=C["brillo"] + (255,))

    if alpha < 1:
        a = capa.getchannel("A").point(lambda v: int(v * alpha))
        capa.putalpha(a)
    if dy:
        capa = ImageChops.offset(capa, 0, int(dy * K))
    return capa


# Letras: coordenadas locales del grupo <g transform="translate(310,150) scale(1.18)">
def G(pts):
    return [(310 + 1.18*x, 150 + 1.18*y) for x, y in pts]


def curva_o():
    """O redondeada, en coordenadas LOCALES del grupo (ya con +214 de la O)."""
    segmentos = [
        ((310,79), (310,50), (290,26), (266,26)),
        ((266,26), (242,26), (222,50), (222,79)),
        ((222,79), (222,108), (242,132), (266,132)),
        ((266,132), (290,132), (310,108), (310,79)),
    ]
    puntos = []
    for p0, p1, p2, p3 in segmentos:
        puntos += bezier(p0, p1, p2, p3, 16) if not puntos else \
                  bezier(p0, p1, p2, p3, 16)[1:]
    return puntos


CURVA_O = curva_o()

TRAZOS = [
    # (puntos en coords base, ancho, inicio, fin)
    (G([(12,150),(12,8),(96,8)]),            13, .16, .27),
    (G([(12,78),(74,78)]),                    8.5, .28, .35),
    (G([(124,8),(124,150),(204,150)]),       13, .36, .45),
    (G(CURVA_O),                              13, .46, .56),
    (G([(338,8),(366,150),(396,52),(426,150),(454,8)]), 13, .57, .68),
]
SUBRAYADO = quad((330,352), (580,364), (835,352), 60)


def dibujar_letras(img, t):
    # guias tenues
    guias = Image.new("RGBA", (W, H), (0,0,0,0))
    gd = ImageDraw.Draw(guias)
    a = int(26 * vent(t, .04, .12))
    for pts, ancho, ini, fin in TRAZOS:
        trazar(gd, pts, C["tinta"], ancho, a)
    img.alpha_composite(guias)

    capa = Image.new("RGBA", (W, H), (0,0,0,0))
    punta = None
    for pts, ancho, ini, fin in TRAZOS:
        f = vent(t, ini, fin)
        if f <= 0:
            continue
        parte = recortar(pts, f)
        trazar_brillo(capa, parte, C["tinta"], ancho)
        if 0 < f < 1:
            punta = parte[-1]

    # subrayado
    fsub = vent(t, .69, .77)
    if fsub > 0:
        trazar_brillo(capa, recortar(SUBRAYADO, fsub), C["tinta"], 7)

    # resplandor de la punta que escribe
    if punta:
        dd = ImageDraw.Draw(capa, "RGBA")
        cx, cy = P(*punta)
        for mult, al in ((6, 12), (4, 26), (2.6, 55), (1.4, 110)):
            r = L(mult * 3)
            dd.ellipse([cx-r, cy-r, cx+r, cy+r], fill=C["brillo"] + (al,))
        r = L(4)
        dd.ellipse([cx-r, cy-r, cx+r, cy+r], fill=C["brillo"] + (255,))

    img.alpha_composite(capa)


def dibujar_textos(img, t):
    capa = Image.new("RGBA", (W, H), (0,0,0,0))
    d = ImageDraw.Draw(capa, "RGBA")
    texto_espaciado(d, "TU ALMACÉN, EN MOVIMIENTO", 450, 381, 19, 7,
                    C["texto"], 245 * vent(t, .72, .82))
    texto_espaciado(d, "CINESTOCK  ·  FLOW", 450, 411, 12, 4,
                    C["texto"], 150 * vent(t, .78, .88))
    # barra de carga
    d.line([P(360,432), P(540,432)], fill=(255,255,255,32), width=L(4))
    r = L(2)
    a, b = P(360,432)
    d.ellipse([a-r,b-r,a+r,b+r], fill=(255,255,255,32))
    fp = vent(t, .70, .95)
    if fp > 0:
        x2 = 360 + 180 * fp
        d.line([P(360,432), P(x2,432)], fill=C["tinta"] + (255,), width=L(4))
        cx, cy = P(x2, 432)
        d.ellipse([cx-r,cy-r,cx+r,cy+r], fill=C["tinta"] + (255,))
    img.alpha_composite(capa)


def fotograma(i):
    t = i / max(1, N_FRAMES - 1)
    img = FONDO.copy()
    dibujar_palomitas(img, t * DUR)

    a_gato = vent(t, .00, .10)
    dy = (1 - vent(t, .02, .16)) * 26
    img.alpha_composite(capa_gato(t * DUR, a_gato, dy))

    dibujar_letras(img, t)
    dibujar_textos(img, t)
    return img.convert("RGB").resize((OUT_W, OUT_H), Image.LANCZOS)


# ---------------------------- Salida --------------------------------------
def main():
    print(f"Renderizando {N_FRAMES} fotogramas a {OUT_W}x{OUT_H} ...")
    frames = [fotograma(i) for i in range(N_FRAMES)]

    # PNG final
    png = os.path.join(AQUI, "flow_intro.png")
    frames[-1].save(png)
    print("OK ->", png)

    # GIF (un frame de cada dos = 10 fps, paleta comun para que no parpadee)
    gif_frames = frames[::2]
    pal = gif_frames[0].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    paleta = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in gif_frames]
    gif = os.path.join(AQUI, "flow_intro.gif")
    paleta[0].save(gif, save_all=True, append_images=paleta[1:],
                   duration=100, loop=0, optimize=True, disposal=2)
    print("OK ->", gif, f"({os.path.getsize(gif)//1024} KB)")

    # MP4
    try:
        import imageio.v2 as iio
        import numpy as np
        mp4 = os.path.join(AQUI, "flow_intro.mp4")
        w = iio.get_writer(mp4, fps=FPS, codec="libx264", quality=8,
                           output_params=["-pix_fmt", "yuv420p",
                                          "-movflags", "+faststart"])
        for f in frames:
            w.append_data(np.asarray(f))
        w.close()
        print("OK ->", mp4, f"({os.path.getsize(mp4)//1024} KB)")
    except Exception as e:
        print("AVISO: no se pudo crear el MP4:", e)

    print("Listo.")


if __name__ == "__main__":
    main()
