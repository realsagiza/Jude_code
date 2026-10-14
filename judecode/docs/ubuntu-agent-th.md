# Jude Code บน Ubuntu: Agent ก่อนเชื่อม Telegram

**สถานะล่าสุด:** ผู้ใช้ขอสิทธิ์ระดับ root เมื่อ 2026-10-14; ใช้ Administrator profile ท้ายเอกสารแทน sandbox baseline ด้านล่าง โดย Telegram bridge ยังแยก UID และ owner allowlist

ติดตั้งครั้งแรก 2026-10-14 บน `root@10.0.0.250` (Ubuntu 24.04, Python 3.12) จาก application commit `f5f0d75` และ deployment files ใน commit ของเอกสารนี้ ไม่รวมการแก้ไขที่ยังไม่ commit บน Mac

## สถานะและขอบเขต

- `judecode-scheduler.service` เปิดหลังบูตและ restart เมื่อ process ล้ม รันเป็น system user `judecode`, shell `nologin`, ไม่มี sudo
- โปรแกรม/venv อยู่ `/opt/judecode` และเป็นของ root; HOME `/var/lib/judecode`; พื้นที่งาน `/var/lib/judecode/workspaces`
- ตารางถาวร `/var/lib/judecode/.judecode/scheduler/jobs.sqlite3`; ขณะส่งมอบไม่มีงานที่ตั้งไว้
- ใช้ OpenRouter key เดิมตามคำอนุญาตของผู้ใช้ โดยย้ายเฉพาะ key/model ไม่ย้าย `.env` ทั้งไฟล์, SSH keys, Vault หรือข้อมูลโปรเจคเดิม
- `/etc/judecode/agent.env` เป็น root:root 0600; service รับผ่าน EnvironmentFile แล้วซ่อน directory จาก process ภายใน service
- Key ยังอยู่ใน environment ของ Agent และ child processes ตามการทำงานของ provider; ไม่ใช่ credential broker และไม่ป้องกัน Agent ที่ถูกยึดจากใช้ key ของตัวเอง ควรใช้ key แยก/วงเงิน provider เมื่อพร้อม
- ปิด expert handoff ใน deployment นี้; primary model เดิม; ไม่เปิด Telegram และยังไม่มี bot token/User ID
- Shell/network tools ต้องขอสิทธิ์ จึงไม่รันใน scheduled worker; delete/deploy/system ถูกปฏิเสธ; read/write และ checkpoint ใช้ได้ภายใน OS permissions
- Scheduler รองรับงาน observe และการนัดต่อ งาน work ที่ต้อง shell จะต้องปรับสิทธิ์โดยผู้ดูแลก่อน

## ขอบเขตการป้องกัน

Unit ต้นแบบ: `deploy/systemd/judecode-scheduler.service`

ใช้ NoNewPrivileges, ตัด capabilities, filesystem read-only ยกเว้น `/var/lib/judecode` และ private temp, ซ่อน home ของผู้ใช้อื่น, ป้องกัน kernel/devices, ห้าม bind/listen ผ่าน seccomp, จำกัด memory 2 GiB / CPU 2 cores / 128 tasks และหยุด child processes พร้อม service

บล็อกปลายทาง LAN, loopback และ metadata IP ผ่าน systemd IP filters ยกเว้น DNS stub 127.0.0.53/54 ดังนั้นยังตรวจเครื่องอื่นใน LAN ไม่ได้ ต้องเปิดปลายทางที่จำเป็นอย่างเจาะจงเมื่อผู้ใช้เลือกงาน ไม่ได้เปิดพอร์ต/firewall เพิ่ม และไม่ได้เปลี่ยน SSH ที่ใช้อยู่

ข้อจำกัดเหล่านี้ใช้กับ process ใน systemd unit และลูกของมัน การรัน CLI ด้วย runuser โดยตรงไม่มี sandbox ของ unit; ใช้ตรง ๆ เฉพาะจัดการตาราง ไม่ใช้แทน sandbox เพื่อสั่ง Agent งานทั่วไป โปรแกรมยังออก public internet ได้และอ่านไฟล์ที่ OS อนุญาต ไม่ใช่การแยกเครื่องเต็มรูปแบบ

เครื่องมี UFW active/default deny incoming, Fail2ban sshd active, SSH password และ keyboard-interactive ปิด, automatic security updates เปิดอยู่ก่อนติดตั้ง มีสถานะต้อง reboot ค้างอยู่; ยังไม่ได้ reboot หรือรับรองว่าเครื่องปลอดการบุกรุกย้อนหลัง

อ้างอิงการตั้งค่า: [systemd execution sandbox](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml) และ [Ubuntu security updates](https://documentation.ubuntu.com/security/security-updates/)

## ดูแลบนเซิร์ฟเวอร์ (ผ่านบัญชีผู้ดูแล)

```sh
systemctl status judecode-scheduler --no-pager
journalctl -u judecode-scheduler -n 50 --no-pager
systemctl restart judecode-scheduler
systemctl stop judecode-scheduler
```

ดูงานโดยไม่เรียกโมเดล:

```sh
cd /var/lib/judecode/workspaces
runuser -u judecode -- /opt/judecode/venv/bin/python -m judecode.agent.schedule_service list
runuser -u judecode -- /opt/judecode/venv/bin/python -m judecode.agent.schedule_service history
```

ยังไม่สร้างตารางงานให้เอง เมื่อผู้ใช้ระบุ project/เป้าหมายจึงเพิ่มตารางตาม `docs/scheduling-th.md`; ใช้ `Asia/Bangkok` อย่างชัดเจน หากต้องหยุดถาวรใช้ `systemctl disable --now judecode-scheduler` ซึ่งเก็บฐานข้อมูลงานไว้

แก้ key ผ่าน editor บนเซิร์ฟเวอร์ที่ `/etc/judecode/agent.env` โดยไม่ paste ลง chat/command line และ restart service; คง chmod 600/root ownership

## บันทึกการติดตั้งซ้ำ/อัปเดต

1. ใช้ commit ที่ตรวจแล้วและ `git archive`; ไม่คัดลอก working tree ที่มี secrets หรือ pending edits
2. สร้าง system user และ workspace ตาม paths ข้างบน ติดตั้ง python3-venv จาก Ubuntu repositories
3. สร้าง `/opt/judecode/venv`; ติดตั้ง package จาก source ด้วยบัญชี unprivileged ในช่วง build แล้วคืน ownership ทั้ง source/venv ให้ root ก่อนเปิด service
4. ปัจจุบัน wheel ของ judecode ไม่รวม sibling `task_manager`: ต้องนำ `task_manager/` จาก commit เดียวกันไปไว้ใน venv site-packages ด้วย ไม่เช่นนั้น engine import ล้ม เหลือเป็นงาน packaging ที่ควรแก้แยก
5. อัปเดต pip และตรวจ dependencies; เก็บ versions จริงใน `/opt/judecode/installed-requirements.txt`
6. ติดตั้ง unit ใน `/etc/systemd/system/`, env จาก example ใน `/etc/judecode/agent.env` root:root 0600 และ permissions.json ใน HOME/.judecode เป็น root:judecode 0640
7. `systemd-analyze verify`, `systemctl daemon-reload`, `systemctl enable --now judecode-scheduler`
8. ทดสอบด้วย unit แบบ oneshot ที่คัดลอกข้อจำกัดเดียวกันและเปลี่ยน ExecStart เป็น `/opt/judecode/venv/bin/python /opt/judecode/verify_sandbox.py` (คัดลอกจาก deploy/systemd); ตั้ง Restart=no

เก็บสำเนา unit/env อย่างปลอดภัยก่อนอัปเดตของเดิม อย่าทับ key ด้วย example ที่ว่าง ทดสอบก่อน restart และไม่แก้ application/venv ระหว่างมี worker ทำงาน

## การตรวจสอบครั้งนี้

- Dependency consistency ผ่าน `pip check`
- Scheduler tests: 35 passed บน Ubuntu ใช้ fake model และ isolated test directories; ไม่เรียก paid model
- Unit sandbox smoke: engine import แบบ headless, non-root/no capabilities/no privilege escalation, protected paths, workspace write, bind/listen blocked, private destinations unreachable, public DNS/HTTPS สำเร็จ
- OpenRouter key ตรวจผ่าน authenticated `/auth/key` โดยไม่พิมพ์ key หรือ response; ไม่ใช่การทดสอบ inference ของโมเดลจริง
- pip-audit หลังอัปเดต pip: ไม่พบ known vulnerabilities ในแพ็กเกจที่ตรวจ ณ เวลานั้น; judecode/task_manager เป็น local code ไม่ได้มี advisory audit ครอบคลุม
- Restart service สำเร็จและ enable หลังบูต; ไม่ได้ reboot เครื่องทดสอบ

ผลทดสอบและ audit อยู่ `/var/lib/judecode/scheduler-tests.log`, `dependency-audit.json`, `dependency-audit.log`; journal ของ `judecode-verify` เก็บ smoke result และการแก้ปัญหาก่อนผ่าน

อัปเดตหลังติดตั้งครั้งแรก: เพิ่ม Telegram bridge แบบแยก UID และ numeric owner allowlist แล้ว ดู `telegram-th.md`; สถานะ “ยังไม่มี Telegram” ด้านบนเป็นบันทึกการติดตั้งฐานก่อนเพิ่ม bridge


## Administrator profile — ผู้ใช้อนุญาตสิทธิ์ host เต็มรูปแบบ

2026-10-14 เปลี่ยน scheduler เป็น root ตามคำขอผู้ใช้ โดยติดตั้ง `deploy/systemd/judecode-scheduler-root.service` เป็น `/etc/systemd/system/judecode-scheduler.service`

- `User=root`, `Group=judecode`, HOME/workspace เดิม; ไม่มี filesystem sandbox, capability restrictions, NoNewPrivileges, private devices หรือ LAN egress block สำหรับ Agent
- Read/write/shell/delete/deploy/system/network permission categories ตั้ง auto; ยังใช้ tool allowlist ของ scheduled worker และตัวตรวจจับ dangerous shell patterns เดิม
- งาน deploy และการกระทำภายนอกยังต้องมีคำสั่งชัดเจนใน goal; root access ไม่ใช่คำสั่งให้ทำงานนอกเป้าหมายหรือ deploy เอง
- Telegram default mode เป็น work รวม /after และ /daily; `/observe` จำกัดเครื่องมืออ่านข้อมูลได้เป็นรายงาน
- คงเพดานรอบ/เวลา/งบประมาณประมาณเดิม; หยุดงานด้วย /stop
- Telegram bridge ยังเป็น judecode-bot/non-root, owner-private-only และ sandbox เดิม ไม่มีการเปิดพอร์ตหรือเปลี่ยน SSH/firewall
- Root Agent สามารถอ่านข้อมูลลับและ credentials ทั้ง host รวมถึง bot token ได้ แม้ bridge แยก UID; การยึดบัญชี Telegram เจ้าของจึงเท่ากับสิทธิ์ root ตามคำสั่งที่ส่ง
- เข้าถึงเครื่องอื่นได้ตาม network routing/firewall/SSH credentials ที่ host มี ไม่ได้สร้างสิทธิ์บนเครื่องปลายทางใหม่ให้เอง

Backup ก่อนเปลี่ยนอยู่ `/root/judecode-backup-before-admin` mode0700 มี unit เดิม, agent.env, telegram.env และ permissions.json; ห้ามนำ directory นี้ขึ้น Git

การกลับ baseline: หยุด bridge/scheduler, restore ไฟล์จาก backup ไปตำแหน่งเดิม (env root0600, permissions root:judecode0640), daemon-reload และ start ทั้งสองบริการ โหมด Telegram จะกลับ observe และสิทธิ์ shell/network จะกลับ ask

การทดสอบ Administrator profile: 59 tests passed; live Agent เรียก shell `id -u` ได้ 0 และ `systemctl is-active ssh` ได้ active จริง (exit0 ทั้งคู่) ตัวประเมินผลเดิมยังจัด shell-only task ที่ไม่มี project test commands เป็น unverified จึงเก็บ smoke job เป็น needs_attention แม้คำสั่งตรวจสิทธิ์ผ่าน ไม่ได้ข้ามตัวประเมินหรือแก้ฐานข้อมูลให้ดูสำเร็จ
