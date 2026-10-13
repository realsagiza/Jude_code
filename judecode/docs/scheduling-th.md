# JudeCode วางแผนและตื่นมาทำงานเอง

เปิด JudeCode ใหม่ แล้วสั่งด้วยภาษาปกติ เช่น:

> พรุ่งนี้ 9 โมงให้ตรวจ TODO ของโปรเจคนี้ วางแผนลำดับงาน แล้วนัดตัวเองมาตรวจต่อทุกวันตอน 9 โมง เวลาไทย อ่านอย่างเดียว ไม่แก้โค้ด ไม่เกิน 20 รอบ งบประมาณรอบละ 1 ดอลลาร์

> อีก 10 นาทีเริ่มแก้ปัญหาตามเป้าหมายนี้ได้ อนุญาตแก้โค้ดและรัน tests แบ่งงานเป็นรอบ บันทึกสิ่งที่ทำและเลือกเวลาทำต่อเอง ไม่เกิน 10 รอบ รอบละ 15 นาที

AI ใช้ `schedule_create` เก็บเป้าหมายและเงื่อนไข เมื่อถึงเวลาจะเปิด agent session ใหม่ใน project เดิม ส่งความคืบหน้ารอบก่อนให้ แล้วให้วางแผนและทำงานหนึ่งรอบ `schedule_checkpoint` บันทึกหลักฐาน/งานค้างและเลือกเวลาครั้งหน้าได้เอง:

- `continue`: ทำต่อเมื่อถึง `next_at` (มี timezone และห่างอย่างน้อย 60 วินาที)
- `complete`: เป้าหมายทั้งหมดเสร็จ หยุดรวมถึงงานวนซ้ำ
- `blocked`: หยุดเพื่อให้ผู้ใช้ตรวจสอบ

ตาราง/ผลลัพธ์อยู่ถาวรที่ `~/.judecode/scheduler/jobs.sqlite3` มี transcript แยกและ log ต่อรอบ ไม่ต้องเปิดหน้าจอแชตทิ้งไว้ แต่ต้องเปิดเครื่องและ login ไว้ เครื่อง sleep/ปิดอยู่จะทำงานไม่ได้ เมื่อกลับมาจะเก็บงานที่เลยเวลาไว้ทำหนึ่งรอบ ไม่ไล่รันย้อนหลังทุกครั้ง

## เปิดระบบเบื้องหลัง

macOS ติดตั้ง LaunchAgent ด้วย Python environment ที่ติดตั้ง JudeCode ไว้:

```sh
judecode schedule install
judecode schedule status
```

เปิดอัตโนมัติหลัง login และ restart เมื่อ service ล้ม ไม่มีการเรียก AI ขณะไม่มีงานถึงกำหนด ใช้ provider/model และ `.env` เดิมของ JudeCode ไม่ใส่ API key ใน plist หากย้ายโฟลเดอร์/venv ให้ install อีกครั้ง

Linux หรือรันเองใน terminal:

```sh
judecode schedule serve
```

หยุด service ที่รันเองด้วย Ctrl+C; macOS หยุดและถอน service โดยเก็บงานเดิม:

```sh
judecode schedule uninstall
```

งานที่ถูกเก็บไว้จะกลับมามีสิทธิ์รันเมื่อเปิด service ใหม่ หากไม่ต้องการให้รันอีกให้ `cancel` งานนั้น

## ดูและจัดการ

ทุกหน้าจอแชตรองรับ `/schedule`, `/schedule list`, `/schedule history`, `/schedule history ID`, `/schedule pause ID`, `/schedule resume ID`, `/schedule cancel ID` โดยไม่ต้องเรียกโมเดล ผลลัพธ์อยู่ใน history; ไม่มีการส่งอีเมล/ข้อความภายนอกอัตโนมัติ

CLI ใช้ได้เช่นกัน:

```sh
judecode schedule add --name "ทบทวนงาน" \
  --goal "อ่าน TODO วางแผนลำดับงานและบันทึกผล ตรวจซ้ำทุกวันจนกว่าจะครบจำนวนรอบ" \
  --daily 09:00 --timezone Asia/Bangkok --project /absolute/project \
  --mode observe --max-runs 20 --budget 1 --timeout-minutes 15
judecode schedule list
judecode schedule history --job-id ID
judecode schedule pause ID
judecode schedule resume ID --run-at 2030-01-01T09:00:00+07:00
judecode schedule cancel ID
```

`--run-at` สำหรับครั้งเดียวหรือกำหนดครั้งแรก; `--daily HH:MM` ตามเขตเวลา หรือ `--interval-minutes N` นับจากรอบก่อนจบ (เลือก daily/interval อย่างใดอย่างหนึ่ง) AI อาจเปลี่ยนเวลารอบถัดไปผ่าน checkpoint ภายในเป้าหมายเดิม

## พฤติกรรมและข้อจำกัด

- รันทีละงานทั้ง service; SQLite claim และ process lock ป้องกัน scheduler สองตัวรันซ้ำ ไม่ล็อก project จากผู้ใช้หรือโปรแกรมอื่น
- `observe` อนุญาตเครื่องมืออ่านข้อมูลในเครื่อง; `work` เพิ่มแก้ไฟล์ shell และเว็บ ให้ใช้เมื่อผู้ใช้อนุญาตทำงานเหล่านี้ สิทธิ์ `ask`/`deny` จะไม่ถูกรันแบบ unattended
- โหมด work ใช้ shell ภายใต้บัญชีผู้ใช้เดิม ไม่ใช่ OS sandbox; เป้าหมายต้องเชื่อถือได้
- Worker ตั้งเวลาตัวเองต่อได้ แต่ใช้ schedule tools สร้างเป้าหมายเพิ่มหรือเปลี่ยนสิทธิ์/เพดานตัวเองไม่ได้
- ค่าเริ่มต้น 20 wake-ups, 15 นาทีต่อรอบ, สูงสุด 12 model turns ต่อรอบ งบ USD เป็นการประมาณของ BudgetTracker เดิม ตรวจหยุดระหว่างรอบโมเดล อาจเกินจากคำขอล่าสุดและไม่ใช่วงเงิน provider ที่รับประกัน
- ล้มเหลว, timeout, session ยังไม่ verified หรือ crash จะหยุดเป็น `needs_attention` ไม่ retry ผลข้างเคียงที่ไม่แน่นอน ผู้ใช้ตรวจ history/log แล้ว `resume` ได้ จำนวน wake-ups ที่ใช้ไปไม่รีเซ็ต
- Pause/cancel ระหว่างทำงานจะยุติ worker/process group อาจมีผลข้างเคียงเกิดขึ้นแล้ว ไม่มี rollback อัตโนมัติ
- Checkpoint จะมีผลต่อเวลาครั้งหน้าหลัง worker จบสำเร็จเท่านั้น งานครั้งเดียวที่ไม่ยืนยัน complete จะรอตรวจสอบ งานวนซ้ำที่จบสำเร็จแต่ไม่ checkpoint ใช้เวลาตามตารางเดิม
- ระบบใหม่นี้ไม่ย้าย `schedule.yaml` ของ TaskScheduler รุ่นเก่าอัตโนมัติ และไม่ใช้ `/daemon` รุ่นเก่า
- ปัจจุบันรองรับ worker service macOS/Linux; Windows ยังไม่รองรับ process lock/group และ service install นี้
