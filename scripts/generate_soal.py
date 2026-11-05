#!/usr/bin/env python3
"""Tambah soal baru ke soal.json lewat Gemini API (gratis) atau Claude API.
Dijalankan oleh GitHub Actions. Butuh salah satu secret: GEMINI_API_KEY atau
ANTHROPIC_API_KEY. Tanpa dependensi luar.
"""
import difflib
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

LEVELS = {20: "Malu", 30: "Bersalah", 50: "Apatis", 75: "Duka", 100: "Takut",
         125: "Keinginan", 150: "Marah", 175: "Bangga", 200: "Berani",
         250: "Netral", 310: "Kesediaan", 350: "Penerimaan", 400: "Akal",
         500: "Cinta", 540: "Sukacita", 600: "Damai", 700: "Pencerahan"}

PER_RUN = int(os.getenv("SOAL_PER_LEVEL", "2"))
MAX_PER_LEVEL = int(os.getenv("SOAL_MAX_PER_LEVEL", "40"))
LEVELS_PER_RUN = int(os.getenv("SOAL_LEVELS_PER_RUN", "3"))  # Jumlah level acak per eksekusi cron
MODEL = os.getenv("SOAL_MODEL", "claude-sonnet-5-5")
PATH = os.getenv("SOAL_PATH", "soal.json")


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def build_prompt_for_level(level_num, level_name, existing_items):
    """Membangun prompt khusus untuk satu level tertentu."""
    recent = existing_items.get(str(level_num), [])[-8:]
    levels_str = f"{level_num}: {level_name}"
    return f"""Kamu membantu menyusun bank pernyataan untuk aplikasi refleksi diri berbasis Map of Consciousness David R. Hawkins.

Tugas: Tulis tepat {PER_RUN} pernyataan BARU untuk level kesadaran **{level_name} (Level {level_num})**.

Aturan:
- Sudut pandang orang pertama ("aku"), satu kalimat, 8 sampai 20 kata, bahasa Indonesia sehari-hari.
- Gambarkan pikiran, perasaan, atau perilaku konkret yang khas pada level {level_name}. Jangan menyebut nama level atau kata emosinya secara langsung, supaya tidak mudah ditebak.
- Nada netral dan manusiawi. Pernyataan level ini tidak boleh terdengar memalukan untuk diakui atau terdengar seperti pujian diri.
- Jangan menyalin atau memparafrase pernyataan yang sudah ada. Jangan mengutip Hawkins atau buku mana pun.

Contoh pernyataan yang sudah ada untuk level ini (hindari kemiripan):
{json.dumps({str(level_num): recent}, ensure_ascii=False)}

Keluaran: hanya satu objek JSON dengan format tunggal: {{"{level_num}": ["pernyataan 1", "pernyataan 2"]}}. Tanpa teks lain."""


RETRY_CODES = {429, 500, 502, 503, 504}


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def _send(req, name, tries=5):
    """Kirim permintaan; ulangi dengan jeda bertambah jika server sibuk."""
    delay = 10
    for i in range(tries):
        last = i == tries - 1
        try:
            # Timeout dinaikkan menjadi 180 detik untuk mencegah timeout pada jaringan
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:300]
            if e.code in RETRY_CODES and not last:
                print(f"{name} {e.code}, coba lagi dalam {delay} detik ...", file=sys.stderr)
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            raise ApiError(e.code, f"{name} error {e.code}: {msg}")
        except urllib.error.URLError as e:
            if not last:
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            raise ApiError(0, f"{name} tidak terjangkau: {e.reason}")


def _gemini_once(prompt, key, model):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "maxOutputTokens": 8192, "temperature": 1.0},
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "x-goog-api-key": key, "content-type": "application/json"})
    data = _send(req, f"Gemini ({model})")
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        raise ApiError(0, "Gemini tidak mengembalikan teks: " + json.dumps(data)[:300])
    return "".join(p.get("text", "") for p in parts)


def call_gemini(prompt, key):
    """Coba model utama; jika gagal, coba model cadangan (GEMINI_FALLBACKS)."""
    models = [os.getenv("GEMINI_MODEL", "gemini-2.5-flash")]
    models += [m.strip() for m in os.getenv("GEMINI_FALLBACKS", "gemini-2.0-flash").split(",") if m.strip()]
    err = None
    for model in dict.fromkeys(models):
        try:
            return _gemini_once(prompt, key, model)
        except ApiError as e:
            if e.code in (400, 401, 403):
                sys.exit(str(e))
            print(f"Gagal dengan {model}: {e}", file=sys.stderr)
            err = e
    sys.exit(str(err))


def call_claude(prompt, key):
    body = json.dumps({"model": MODEL, "max_tokens": 3000,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        data = _send(req, "Claude")
    except ApiError as e:
        sys.exit(str(e))
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


def is_too_similar(new_text, existing_texts, threshold=0.65):
    """Mengecek apakah teks baru terlalu mirip dengan teks yang sudah ada."""
    new_clean = " ".join(new_text.lower().split())
    for item in existing_texts:
        item_clean = " ".join(item.lower().split())
        ratio = difflib.SequenceMatcher(None, new_clean, item_clean).ratio()
        if ratio >= threshold:
            return True
    return False


def merge(existing, new):
    merged = {k: list(v) for k, v in existing.items() if isinstance(v, list)}
    added = 0
    for lv in LEVELS:
        key = str(lv)
        have = merged.setdefault(key, [])
        items = new.get(key, [])
        if not isinstance(items, list):
            continue
        for t in items:
            if not isinstance(t, str):
                continue
            t = " ".join(t.split())
            if not 15 <= len(t) <= 180 or len(have) >= MAX_PER_LEVEL:
                continue
            
            # Cek duplikat persis
            seen_exact = {item.strip().lower() for item in have}
            if t.lower() in seen_exact:
                continue
            
            # Cek kemiripan teks (similarity check)
            if is_too_similar(t, have, threshold=0.65):
                continue
                
            have.append(t)
            added += 1
    merged = {k: v for k, v in merged.items() if v}
    return dict(sorted(merged.items(), key=lambda kv: int(kv[0]))), added


def main():
    existing = load(PATH)
    total_added = 0
    
    all_levels = list(LEVELS.items())
    random.shuffle(all_levels)
    target_levels = all_levels[:LEVELS_PER_RUN]
    
    print(f"Memilih {len(target_levels)} level secara acak untuk eksekusi kali ini...")
    
    for lv_num, lv_name in target_levels:
        key = str(lv_num)
        current_count = len(existing.get(key, []))
        
        if current_count >= MAX_PER_LEVEL:
            print(f"Level {lv_num} ({lv_name}) sudah mencapai batas maksimal ({current_count}/{MAX_PER_LEVEL}). Melewati...")
            continue
            
        print(f"Memproses Level {lv_num} - {lv_name} (Saat ini: {current_count} soal)...")
        prompt = build_prompt_for_level(lv_num, lv_name, existing)
        
        try:
            raw_response = call_api(prompt)
            new_data = parse(raw_response)
            
            existing, added = merge(existing, new_data)
            if added > 0:
                total_added += added
                with open(PATH, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=1)
                    f.write("\n")
                print(f"-> Berhasil menambahkan {added} soal untuk level {lv_num}.")
            else:
                print(f"-> Tidak ada soal valid yang ditambahkan untuk level {lv_num}.")
                
            time.sleep(2)
            
        except Exception as e:
            print(f"Gagal pada level {lv_num} ({lv_name}): {e}", file=sys.stderr)
            continue

    if total_added == 0:
        print("Tidak ada soal baru yang berhasil ditambahkan pada eksekusi kali ini.")
    else:
        print(f"Selesai! Total {total_added} soal baru ditambahkan ke {PATH}.")


if __name__ == "__main__":
    main()
