#!/usr/bin/env python3
"""Tambah soal baru ke soal.json lewat Gemini API (gratis) atau Claude API.
Dijalankan oleh GitHub Actions. Butuh salah satu secret: GEMINI_API_KEY atau
ANTHROPIC_API_KEY. Tanpa dependensi luar.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

LEVELS = {20: "Malu", 30: "Bersalah", 50: "Apatis", 75: "Duka", 100: "Takut",
          125: "Keinginan", 150: "Marah", 175: "Bangga", 200: "Berani",
          250: "Netral", 310: "Kesediaan", 350: "Penerimaan", 400: "Akal",
          500: "Cinta", 540: "Sukacita", 600: "Damai", 700: "Pencerahan"}
PER_RUN = int(os.getenv("SOAL_PER_LEVEL", "2"))
MAX_PER_LEVEL = int(os.getenv("SOAL_MAX_PER_LEVEL", "40"))
MODEL = os.getenv("SOAL_MODEL", "claude-sonnet-5-5")
PATH = os.getenv("SOAL_PATH", "soal.json")


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def build_prompt(existing):
    recent = {k: v[-8:] for k, v in existing.items() if isinstance(v, list)}
    levels = "\n".join(f"{k}: {v}" for k, v in LEVELS.items())
    return f"""Kamu membantu menyusun bank pernyataan untuk aplikasi refleksi diri berbasis Map of Consciousness David R. Hawkins.

Tugas: untuk setiap level di bawah, tulis {PER_RUN} pernyataan BARU.

Aturan:
- Sudut pandang orang pertama ("aku"), satu kalimat, 8 sampai 20 kata, bahasa Indonesia sehari-hari.
- Gambarkan pikiran, perasaan, atau perilaku konkret yang khas pada level itu. Jangan menyebut nama level atau kata emosinya secara langsung (untuk level Malu, jangan menulis "aku merasa malu"), supaya tidak mudah ditebak.
- Nada netral dan manusiawi. Pernyataan level rendah tidak boleh terdengar memalukan untuk diakui, dan level tinggi tidak boleh terdengar seperti pujian diri. Semua harus sama-sama masuk akal untuk dinilai jujur.
- Jangan menyalin atau memparafrase pernyataan yang sudah ada. Jangan mengutip Hawkins atau buku mana pun.

Level:
{levels}

Contoh pernyataan yang sudah ada (hindari kemiripan):
{json.dumps(recent, ensure_ascii=False)}

Keluaran: hanya satu objek JSON. Kunci adalah angka level sebagai string, nilai adalah array berisi {PER_RUN} string. Tanpa teks lain."""


def _send(req, name):
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"{name} error {e.code}: {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"{name} tidak terjangkau: {e.reason}")


def call_gemini(prompt, key):
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "maxOutputTokens": 8192, "temperature": 1.0},
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "x-goog-api-key": key, "content-type": "application/json"})
    data = _send(req, "Gemini")
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        sys.exit("Gemini tidak mengembalikan teks: " + json.dumps(data)[:300])
    return "".join(p.get("text", "") for p in parts)


def call_claude(prompt, key):
    body = json.dumps({"model": MODEL, "max_tokens": 3000,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    data = _send(req, "Claude")
    return "".join(b.get("text", "") for b in data.get("content", []))


def call_api(prompt):
    """Pakai Gemini (gratis) jika GEMINI_API_KEY ada, kalau tidak pakai Claude."""
    gem = os.environ.get("GEMINI_API_KEY")
    ant = os.environ.get("ANTHROPIC_API_KEY")
    if gem:
        return call_gemini(prompt, gem)
    if ant:
        return call_claude(prompt, ant)
    sys.exit("Atur secret GEMINI_API_KEY (gratis) atau ANTHROPIC_API_KEY di repo.")


def parse(text):
    text = re.sub(r"```(?:json)?", "", text)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < a:
        raise ValueError("Respons tidak berisi JSON")
    obj = json.loads(text[a:b + 1])
    if not isinstance(obj, dict):
        raise ValueError("JSON bukan objek")
    return obj


def merge(existing, new):
    merged = {k: list(v) for k, v in existing.items() if isinstance(v, list)}
    added = 0
    for lv in LEVELS:
        key = str(lv)
        have = merged.setdefault(key, [])
        seen = {t.strip().lower() for t in have}
        items = new.get(key, [])
        if not isinstance(items, list):
            continue
        for t in items:
            if not isinstance(t, str):
                continue
            t = " ".join(t.split())
            if not 15 <= len(t) <= 180 or t.lower() in seen or len(have) >= MAX_PER_LEVEL:
                continue
            have.append(t)
            seen.add(t.lower())
            added += 1
    merged = {k: v for k, v in merged.items() if v}
    return dict(sorted(merged.items(), key=lambda kv: int(kv[0]))), added


def main():
    existing = load(PATH)
    new = parse(call_api(build_prompt(existing)))
    merged, added = merge(existing, new)
    if not added:
        print("Tidak ada soal baru yang valid.")
        return
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"{added} soal baru ditambahkan ke {PATH}.")


if __name__ == "__main__":
    main()
