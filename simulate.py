#!/usr/bin/env python3
"""
Simülasyon Launcher — Tek bilgisayarda server + çoklu client çalıştırır.

Kullanım:
    python simulate.py                     # 1 server + 3 client (varsayılan)
    python simulate.py --clients 5         # 1 server + 5 client
    python simulate.py --clients 2 --windowed  # Pencere modunda

Her client ayrı bir Pygame penceresi açar ve farklı screen_id ile çalışır.
Server otomatik olarak arka planda başlar.
Dashboard: http://localhost:8000
"""
import subprocess
import sys
import os
import time
import json
import signal
import argparse
import tempfile
import shutil


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(ROOT_DIR, "thief_server")
CLIENT_DIR = os.path.join(ROOT_DIR, "thief_client")

# Her client kendi config dosyasını alır
TEMP_CONFIG_DIR = os.path.join(ROOT_DIR, "_sim_configs")


def load_base_client_config() -> dict:
    """Temel client config'ini yükle"""
    config_path = os.path.join(CLIENT_DIR, "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def create_client_config(screen_id: int, num_clients: int, windowed: bool = True) -> str:
    """
    Her client için özel config dosyası oluşturur.
    Pencereleri yan yana dizer.
    """
    config = load_base_client_config()

    # Client-özel ayarlar
    config["screen_id"] = screen_id
    config["server_url"] = "http://localhost:8000/event"
    config["server_base_url"] = "http://localhost:8000"
    config["server_controlled"] = True
    config["debug"] = True

    if windowed:
        config["fullscreen"] = False

        # Pencere boyutu hesapla (yan yana sığdır)
        # Her bir pencere ~640x480 veya daha küçük
        win_w = min(640, 1920 // min(num_clients, 4))
        win_h = min(480, int(win_w * 9 / 16))

        config["screen_width"] = win_w
        config["screen_height"] = win_h

        # Spawn/reset pozisyonlarını pencereye göre ayarla
        config["spawn_x"] = win_w + 50
        config["reset_x"] = -50

        # Band pozisyonlarını pencereye göre ayarla
        if config.get("band_enabled", False):
            config["band_x_min"] = int(win_w * 0.45)
            config["band_x_max"] = int(win_w * 0.55)

        # Hırsız pozisyonunu ayarla
        config["thief_y"] = int(win_h * 0.85)
        config["thief_scale"] = max(2, config.get("thief_scale", 6) // 2)

    # Config dosyasını yaz
    os.makedirs(TEMP_CONFIG_DIR, exist_ok=True)
    config_path = os.path.join(TEMP_CONFIG_DIR, f"config_screen{screen_id}.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    return config_path


def start_server():
    """Server'ı arka planda başlat"""
    print("🚀 Server başlatılıyor...")

    process = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=SERVER_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
    )

    # Server'ın başlamasını bekle
    time.sleep(2)

    if process.poll() is not None:
        output = process.stdout.read().decode("utf-8", errors="ignore")
        print(f"❌ Server başlatılamadı!\n{output}")
        sys.exit(1)

    print(f"✅ Server başlatıldı (PID: {process.pid})")
    print(f"📊 Dashboard: http://localhost:8000")
    return process


def start_client(screen_id: int, config_path: str):
    """Client'ı ayrı pencerede başlat"""
    print(f"   📺 Client #{screen_id} başlatılıyor...")

    env = os.environ.copy()
    # SDL pencere pozisyonu (yan yana diz)
    col = (screen_id - 1) % 4
    row = (screen_id - 1) // 4
    x = 50 + col * 660
    y = 50 + row * 520
    env["SDL_VIDEO_WINDOW_POS"] = f"{x},{y}"

    process = subprocess.Popen(
        [sys.executable, "main.py", config_path],
        cwd=CLIENT_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
    )

    time.sleep(0.5)

    if process.poll() is not None:
        output = process.stdout.read().decode("utf-8", errors="ignore")
        print(f"   ❌ Client #{screen_id} hatası: {output[:200]}")
        return None

    print(f"   ✅ Client #{screen_id} (PID: {process.pid})")
    return process


def cleanup(processes: list):
    """Tüm process'leri temizle"""
    print("\n🛑 Simülasyon durduruluyor...")

    for p in processes:
        if p and p.poll() is None:
            try:
                if os.name == 'nt':
                    p.terminate()
                else:
                    os.kill(p.pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass

    # Kalan process'leri bekle
    for p in processes:
        if p:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()

    # Temp config dosyalarını temizle
    if os.path.exists(TEMP_CONFIG_DIR):
        shutil.rmtree(TEMP_CONFIG_DIR, ignore_errors=True)

    print("✅ Temizlendi.")


def main():
    parser = argparse.ArgumentParser(description="Hırsız Oyunu Simülatörü")
    parser.add_argument(
        "--clients", "-c",
        type=int, default=3,
        help="Kaç client penceresi açılacak (varsayılan: 3)"
    )
    parser.add_argument(
        "--windowed", "-w",
        action="store_true", default=True,
        help="Pencere modunda çalıştır (varsayılan: True)"
    )
    parser.add_argument(
        "--fullscreen", "-f",
        action="store_true",
        help="Tam ekran modunda çalıştır (sadece 1 client için önerilir)"
    )
    args = parser.parse_args()

    windowed = not args.fullscreen
    num_clients = args.clients

    processes = []

    print("=" * 50)
    print("🎮 Hırsız Oyunu — Simülasyon Modu")
    print(f"   Server: localhost:8000")
    print(f"   Client sayısı: {num_clients}")
    print(f"   Mod: {'Pencere' if windowed else 'Tam ekran'}")
    print("=" * 50)

    try:
        # 1) Server başlat
        server = start_server()
        processes.append(server)

        # 2) Client config'lerini oluştur ve başlat
        print(f"\n📺 {num_clients} client başlatılıyor...")
        for i in range(1, num_clients + 1):
            config_path = create_client_config(i, num_clients, windowed)
            client = start_client(i, config_path)
            if client:
                processes.append(client)

        print(f"\n{'=' * 50}")
        print("✅ Simülasyon çalışıyor!")
        print()
        print("📋 Kullanım:")
        print("   1. Dashboard'u aç: http://localhost:8000")
        print("   2. Çocuk sayısı + süre gir → ▶ Oyunu Başlat")
        print("   3. Her client penceresinde SPACE ile hit")
        print("   4. Dashboard'dan skorları ve spawn'ları izle")
        print(f"\n   Kapatmak için CTRL+C")
        print("=" * 50)

        # Process'leri izle
        while True:
            time.sleep(1)

            # Server çöktü mü?
            if server.poll() is not None:
                print("❌ Server kapandı!")
                break

            # Tüm client'lar kapandı mı?
            alive = [p for p in processes[1:] if p and p.poll() is None]
            if not alive and len(processes) > 1:
                print("📺 Tüm client'lar kapandı.")
                break

    except KeyboardInterrupt:
        pass
    finally:
        cleanup(processes)


if __name__ == "__main__":
    main()
