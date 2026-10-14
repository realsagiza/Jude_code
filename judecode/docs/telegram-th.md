# Telegram สำหรับ Ubuntu Agent

สถานะ deployment ล่าสุด 2026-10-14: ผู้ใช้ขอสิทธิ์ดูแล host เต็มรูปแบบ ตัว scheduler รัน root และตั้ง `JUDECODE_TELEGRAM_DEFAULT_MODE=work`; `/observe` ใช้จำกัดงานเป็น read-only แยกครั้ง ดูส่วน Administrator profile ใน `ubuntu-agent-th.md` ข้อจำกัด sandbox ของ Agent ที่กล่าวด้านล่างเป็น baseline เดิม ส่วน bridge ยังใช้ sandbox/UID แยกอยู่

Bridge ใช้ Telegram long polling ไม่เปิดพอร์ต รองรับข้อความส่วนตัวของ numeric owner ID คนเดียว ไม่รับกลุ่ม ข้อความส่งต่อ ผู้ส่งที่เป็น bot หรือ edited messages และไม่มี first-user pairing

## วิธีใช้

เปิดบอตแล้วกด Start จากบัญชีที่กำหนดไว้:

- ข้อความปกติ → โหมดจาก JUDECODE_TELEGRAM_DEFAULT_MODE (ค่ามาตรฐาน observe; deployment นี้ work)
- `/observe ข้อความ` → อ่านข้อมูลเท่านั้น
- `/work แก้ไฟล์...` → งาน work; ผ่าน permission profile ของ service (deployment นี้อนุญาต auto)
- `/after 10 ตรวจ TODO` → อีก 10 นาที
- `/daily 09:00 ตรวจ TODO` → ทุกวันเวลาไทย
- `/status` หรือ `/jobs` → สถานะ/ID/เวลารอบถัดไป
- `/result ID` → อ่านผลล่าสุดอีกครั้ง
- `/pause ID`, `/resume ID`, `/cancel ID` → จัดการเฉพาะงานที่บอตนี้รับจากเจ้าของ
- `/stop` → ยกเลิกงาน Telegram ที่ยังไม่จบทั้งหมด; ไม่ rollback สิ่งที่ทำไปแล้ว
- `/new` → ล้างบริบทสำหรับคำสั่งใหม่ แต่ไม่ยกเลิกตารางเดิม

ข้อความแต่ละครั้งเป็น goal แยก มีบริบทสรุปผลล่าสุดไม่เกิน 3 งาน ไม่ใช่การส่ง transcript ทั้งหมด งานหนึ่งนัดตัวเองทำต่อได้สูงสุด 10 รอบตามคำสั่งผู้ใช้; รอบละ 10 นาทีและงบประมาณประมาณ $0.25 (ไม่ใช่ provider hard cap) มีคิว active สูงสุด 5 งานและรับสูงสุด 20 งานใน 24 ชั่วโมง

รับแล้วส่ง ID กลับทันที ผลจาก worker จะถูกส่งเมื่อจบรอบ ไม่มีการรายงาน token-by-token และไม่มีการส่งผลของงาน local อื่นที่ไม่ได้มาจากบอตนี้

## ความปลอดภัยและการส่งซ้ำ

- Token แยกจาก OpenRouter key: bridge ใช้ UID `judecode-bot`; Agent ใช้ UID `judecode`; ไม่มี key ของโมเดลใน environment ของ bridge และไม่มี bot token ใน environment ของ Agent; เมื่อ Agent เป็น root มันสามารถอ่านไฟล์/process ของบัญชีอื่นได้ จึงไม่ถือว่า token ถูกแยกจาก root Agent
- `/etc/judecode/telegram.env` root0600 ถูกซ่อนจาก service หลัง systemd อ่าน environment
- Bridge HOME `/var/lib/judecode-telegram` 0700 เก็บ offset, ownership และ receipt; ไม่มี raw Telegram payload ใน application log
- ห้าม log HTTP URL/exception body เพราะ URL ของ Telegram มี token; ใช้ plain text และปิด link preview
- แชร์เฉพาะ scheduler directory ผ่าน group `judecode`: parent directories/workspace ต้องมี group traversal (`0710`), scheduler directory `2770`, files `0660`; scheduler ใช้ drop-in UMask0007
- Bridge systemd unit เขียนได้เฉพาะ HOME ตัวเองและ scheduler directory มี sandbox/egress restrictions เช่นเดียวกับ Agent
- Unknown owner ID, bot identity mismatch หรือ webhook เดิมทำให้ start ล้ม ไม่แก้ webhook ของผู้อื่นเอง
- ข้ามข้อความก่อนเริ่ม bridge ครั้งแรกหรือเก่ากว่า 1 ชั่วโมง; หลังหยุดบริการนานให้ส่งคำสั่งใหม่
- บันทึก update ก่อนทำงานเพื่อไม่ execute ซ้ำหลัง restart; crash ระหว่างรับคำสั่งอาจทำให้คำสั่งไม่ถูกทำ ให้ตรวจ /status ก่อนส่งใหม่
- บันทึก ownership ก่อน enqueue เพื่อไม่ให้ job ที่สร้างแล้วหลุดจาก /status; enqueue มี deterministic request key ป้องกันซ้ำ
- การส่งผลอัตโนมัติเป็น at-most-once attempt: timeout/crash ไม่ retry แบบสุ่มเสี่ยงส่งซ้ำ; ใช้ /result ID เพื่อเรียกผลเอง
- บัญชี Telegram และเครื่อง host ยังเป็นขอบเขตความเชื่อถือ; การยึดบัญชีเจ้าของเท่ากับสิทธิ์สั่งงานตาม policy ที่กำหนด

## ติดตั้งบน Ubuntu

ต้องมี deployment ใน `ubuntu-agent-th.md` ก่อน แล้วติดตั้งโมดูล `telegram_bridge.py` และ scheduler รุ่นที่รองรับ request_key จาก commit เดียวกัน

1. สร้าง system user `judecode-bot`, primary group `judecode`, HOME `/var/lib/judecode-telegram` 0700, shell nologin; ไม่มี sudo
2. หยุด scheduler ชั่วคราว ตั้ง group traversal และ scheduler directory/files ตามด้านบน โดยไม่เปิด HOME/ไฟล์อื่นให้ group อ่าน
3. ติดตั้ง `deploy/systemd/judecode-scheduler.service.d/shared-scheduler.conf` ใน `/etc/systemd/system/judecode-scheduler.service.d/`
4. สร้าง root-only `/etc/judecode/telegram.env` จาก example ใส่ token, numeric owner ID และ username โดยไม่ใส่ค่าเหล่านี้ใน Git
5. ติดตั้ง `judecode-telegram.service` ใน `/etc/systemd/system/`; verify unit, daemon-reload, start scheduler และ enable --now bridge
6. ทดสอบ /start จากเจ้าของ, งานตัวอย่าง, /status, /result และการส่งข้อความจากผู้ใช้คนอื่นต้องไม่สร้างงานหรือได้รับคำตอบ

```sh
systemctl status judecode-telegram judecode-scheduler --no-pager
journalctl -u judecode-telegram -n 30 --no-pager
systemctl restart judecode-telegram
```

หยุดรับคำสั่งและหยุด worker ด้วย `systemctl stop judecode-telegram judecode-scheduler` การหยุดเฉพาะ bridge ไม่หยุดงานที่อยู่ใน scheduler แล้ว

ทดสอบอัตโนมัติใน `tests/test_telegram_bridge.py` และ `tests/test_scheduler.py`: authorization, replay, bounded queue, scheduling, ownership, result delivery, uncertain sends, API token redaction และ engine headless flow

อ้างอิง API: [Telegram Bot API](https://core.telegram.org/bots/api#getupdates)
