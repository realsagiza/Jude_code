# Jude Code — Native macOS App

Jude Code รันเป็น **native macOS app** (PyObjC / Cocoa) ได้แล้ว — ไม่ต้องเปิด Terminal อีกต่อไป

## วิธี Build

```bash
# ใช้ venv ที่มี PyObjC + PyInstaller
./scripts/build_mac_dmg.sh
```

ผลลัพธ์:
- `dist/JudeCode.app` — แอปพร้อมเปิด
- `dist/JudeCode-Installer.dmg` — installer สำหรับแจกจ่าย (drag-to-Applications)

## วิธีติดตั้ง (End User)

1. เปิด `JudeCode-Installer.dmg`
2. ลาก `JudeCode` ไปที่ `Applications`
3. เปิดจาก Launchpad หรือ `/Applications`
4. กด `⌘,` เพื่อเปิด Settings → เลือก provider → วาง API key
   - หรือกดปุ่ม **"Import from .env…"** เพื่อโหลดจากไฟล์ `.env` ที่มีอยู่
5. กด Save → เริ่มคุยกับ AI ได้เลย

## ฟีเจอร์

- **Native Cocoa UI** — ใช้ PyObjC/AppKit, ไม่ใช่ Electron/Tkinter
- **System colors** — adaptive ตาม light/dark mode อัตโนมัติ อ่านง่ายทุกธีม
- **Settings sheet** — เลือก provider (DeepSeek / Anthropic / Z.AI), แก้ API key, model, base URL
- **Auto-import .env** — โหลดค่าจาก `.env` ของโปรเจกต์อัตโนมัติตอนเปิดครั้งแรก
- **Import .env button** — เลือกไฟล์ .env จากที่ไหนก็ได้
- **Config persistence** — บันทึกที่ `~/Library/Application Support/JudeCode/config.env`
- **Streaming chat** — แสดงผลแบบ real-time พร้อมสีสันจาก Rich (ANSI → NSAttributedString)

## โครงสร้างไฟล์

| ไฟล์ | หน้าที่ |
|------|---------|
| `judecode/ui/mac_app.py` | Native Cocoa app หลัก (window, settings, menu, worker) |
| `judecode/ui/mac_ansi.py` | ANSI escape → NSAttributedString parser |
| `judecode/ui/mac_config.py` | จัดการ config (load/save/import .env) |
| `runtime_hook_mac.py` | โหลด config เข้า env ตอน .app เริ่มทำงาน |
| `judecode_mac.spec` | PyInstaller spec สำหรับ build .app |
| `scripts/build_mac_dmg.sh` | Build script (.app + .dmg) |

## โหมดพัฒนา (รันจาก source)

```bash
.venv/bin/python -m judecode --mac-ui
```

ใช้ flag `--mac-ui` เพื่อเปิด native UI แทน terminal CLI
