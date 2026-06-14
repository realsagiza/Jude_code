# 🪟 Build Jude Code for Windows (.exe)

## สรุปสั้นๆ

**สิ่งที่ทำได้:** ✅ Build เป็น `.exe` ไฟล์เดียวที่รันได้บน Windows โดยไม่ต้องติดตั้ง Python

**สิ่งที่ทำไม่ได้:** ❌ การรวม `pip`, `npm`, `node` ไว้ใน `.exe` เพราะ:
- `pip` เป็น package manager สำหรับ development ไม่ใช่ runtime dependency
- `npm` / `node` เป็นของ JavaScript ecosystem — Jude Code เป็น Python app
- PyInstaller รวม **Python interpreter + dependencies + โค้ด** ไว้ใน .exe ไฟล์เดียวอยู่แล้ว
- Jude Code ใช้ **Cloud AI API** (DeepSeek, Anthropic, Z.AI) ซึ่งเป็น external service ไม่ได้ฝัง AI model ไว้ในตัว

---

## วิธีที่ 1: Build .exe ไฟล์เดียว (ง่ายที่สุด)

### ขั้นตอนบน Windows:

1. **ติดตั้ง Python 3.10+** (ถ้ายังไม่มี)
   - ดาวน์โหลดจาก https://www.python.org/downloads/
   - **ติ๊ก "Add Python to PATH"** ขณะติดตั้ง

2. **เปิด Command Prompt หรือ PowerShell** ในโฟลเดอร์ `judecode/`

3. **รัน build script:**
   ```batch
   build_windows.bat
   ```

4. รอประมาณ 2-5 นาที (ขึ้นอยู่กับเครื่อง)

5. **ไฟล์ .exe จะอยู่ที่:** `dist/judecode.exe`
   - ขนาดประมาณ 50-80 MB
   - สามารถคัดลอกไปเครื่อง Windows อื่นที่ไม่มี Python ก็รันได้

### หรือ build ด้วยคำสั่งตรงๆ:
```batch
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
pip install openpyxl xlsxwriter pdfplumber pdfminer.six pypdfium2
pyinstaller --clean --onefile --console --name "judecode" judecode\__main__.py
```

---

## วิธีที่ 2: สร้าง Installer (สำหรับแจกจ่าย)

หลังจาก build .exe เสร็จแล้ว สามารถสร้าง installer ได้ด้วย **Inno Setup**:

1. ดาวน์โหลด Inno Setup: https://jrsoftware.org/isdl.php
2. เปิดไฟล์ `installer_windows.iss` ด้วย Inno Setup
3. กด Build > Compile
4. จะได้ไฟล์ `installer_output/JudeCode_Setup_v0.1.0.exe`

installer นี้จะ:
- ติดตั้ง judecode.exe ลงใน Program Files
- เพิ่มใน Start Menu
- สามารถเพิ่มใน PATH (รัน `judecode` จาก terminal ไหนก็ได้)

---

## วิธีที่ 3: Cross-compile จาก macOS/Linux (ใช้ Docker)

ถ้าคุณอยู่บน macOS หรือ Linux แต่ต้องการ build .exe:

```bash
# ใช้ Docker รัน Windows + PyInstaller
docker run --rm -v $(pwd):/src -w /src cdrx/pyinstaller-windows:latest \
    "pip install -r requirements.txt && \
     pip install openpyxl xlsxwriter pdfplumber pdfminer.six pypdfium2 && \
     pyinstaller --clean --onefile --console --name 'judecode' judecode/__main__.py"
```

หรือใช้ GitHub Actions (ดูไฟล์ `.github/workflows/build-windows.yml` ข้างล่าง)

---

## คู่มือสำหรับผู้ใช้ Windows ที่ได้ .exe ไป

### การใช้งาน:
1. เปิด **Command Prompt** หรือ **PowerShell**
2. รัน:
   ```batch
   judecode.exe
   ```
3. Jude Code จะเชื่อมต่อไปยัง **Cloud AI API** (DeepSeek, Anthropic, หรือ Z.AI/GLM)
   - ต้องตั้งค่า API Key ใน .env ก่อนใช้งาน

### การตั้งค่า:
สร้างไฟล์ `config.ini` ข้างๆ judecode.exe:
```ini
[JudeCode]
BaseURL=https://api.deepseek.com
Model=deepseek-chat
```

หรือใช้ environment variables:
```batch
set JUDECODE_VAULT=C:\Users\me\.judecode\vault
judecode.exe
```

---

## ไฟล์ในโปรเจกต์นี้

| ไฟล์ | คำอธิบาย |
|------|----------|
| `build_windows.bat` | Build script — รันครั้งเดียวจบ |
| `judecode.spec` | PyInstaller spec file (สำหรับปรับแต่งละเอียด) |
| `installer_windows.iss` | Inno Setup script สำหรับสร้าง installer |
| `BUILD_WINDOWS.md` | คู่มือนี้ |

---

## Troubleshooting

**Q: .exe เปิดแล้วหายไปเลย?**
A: Jude Code เป็น CLI app ต้องรันจาก Command Prompt หรือ PowerShell

**Q: Error "No module named ..."**
A: เปิด `build_windows.bat` แล้วเพิ่ม `--hidden-import "ชื่อโมดูล"` ในคำสั่ง pyinstaller

**Q: ไฟล์ .exe ใหญ่เกินไป?**
A: ปกติ 50-80 MB เพราะรวม Python interpreter + dependencies ทั้งหมด ถ้าต้องการเล็กลง ให้ใช้ UPX compression:
```batch
pyinstaller --clean --onefile --upx-dir "C:\path\to\upx" ...
```

**Q: Antivirus แจ้งว่าเป็นไวรัส?**
A: เป็นเรื่องปกติของ PyInstaller binary (false positive) สามารถส่งให้ vendor review ได้
