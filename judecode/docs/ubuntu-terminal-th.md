# ใช้ JudeCode ผ่าน SSH Terminal บน Ubuntu

ติดตั้ง launcher จาก `deploy/bin/judecode-ubuntu` เป็น `/usr/local/bin/judecode`
(root:root, mode 0755) ใช้ Python/package เดิมที่ `/opt/judecode/venv`
และโหลด provider/model/key จาก `/etc/judecode/agent.env` โดยไม่แสดงค่าลับ

```bash
ssh root@10.0.0.250
cd /var/lib/judecode/workspaces
judecode
```

ใช้ `judecode --classic` สำหรับหน้าจอแชตแบบเลื่อนธรรมดา, `judecode --help`
เพื่อดูตัวเลือก ออกจากโหมด classic ด้วย `/exit`; full-screen ใช้ Ctrl+Q

คำสั่งไม่เปลี่ยน working directory จึงเข้าโฟลเดอร์โปรเจคก่อนเรียกได้
Terminal เก็บประวัติใน HOME ของผู้ใช้ SSH แยกจาก Telegram ที่ใช้
`/var/lib/judecode` การเปิด Terminal ไม่ restart หรือหยุดบริการ Telegram
หลีกเลี่ยงให้ทั้งสองช่องทางแก้ไฟล์โปรเจคเดียวกันพร้อมกัน

การติดตั้งนี้ใช้ config เซิร์ฟเวอร์ที่ root อ่านได้เท่านั้น บัญชีอื่นใช้
`sudo judecode` (ต้องมีสิทธิ์ sudo อยู่แล้ว) launcher ไม่เพิ่มสิทธิ์ให้บัญชีใด
