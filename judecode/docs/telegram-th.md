# Telegram เป็นหน้าสนทนาของ Jude Code บน Ubuntu

## สถาปัตยกรรมปัจจุบัน

Telegram → owner-only transport → `AgentEngine(SYSTEM_PROMPT, api)` → `agent.chat(user_message)` → คำตอบกลับ Telegram

ใช้ AgentEngine, system prompt, tools registry, context compaction, native transcripts, expert routing และ permission checks เดียวกับ terminal บน Mac ไม่มี ScheduledEngine/ข้อความ wake-up/checkpoint/การสร้าง goal ครอบข้อความทั่วไป โมเดลเลือกตอบเองหรือเรียก tool ตามข้อความผู้ใช้

- `judecode-telegram.service`: รับ/ส่ง Telegram ด้วย UID judecode-bot และ bot token แยก
- `judecode-chat.service`: Agent สนทนาแบบต่อเนื่อง ใช้ root ตามสิทธิ์ที่ผู้ใช้อนุญาต และ OpenRouter key แยกของ Ubuntu
- `judecode-scheduler.service`: ใช้เฉพาะ goals ที่ตั้งเวลาไว้ เมื่อ Agent เลือกเรียก schedule_create ตามคำสั่งผู้ใช้

ฐานข้อมูล `chat.sqlite3` ใน scheduler directory เป็นเพียงกล่องรับส่งข้อความระหว่าง process ไม่ใช่ฐานข้อมูลงานตามเวลา ไม่มี budget/checkpoint/job ID ต่อข้อความ ผู้ใช้ไม่ต้องเลือกว่าจะเป็นทักทายหรือสั่งงานก่อนส่ง

## การคุย

พิมพ์ภาษาปกติ เช่น “สวัสดี”, “เครื่องนี้มีพื้นที่เหลือเท่าไร”, “อ่านไฟล์นี้ให้หน่อย”, “พรุ่งนี้9โมงตรวจระบบแล้วบอกฉัน”

Agent instance เดียวเก็บ user/assistant/tool messages ต่อเนื่องเหมือน terminal; เมื่อ service restart ใช้ save_transcript/resume_session ของ engine เดิม ไม่ได้เก็บแค่สรุป6คู่และไม่ replay คำสั่งที่ผลไม่แน่นอน Context compaction ยังคงเป็นกลไกของ engine ตามปกติ

- `/new`: สร้างบทสนทนาใหม่ผ่าน native new_conversation
- `/continue`: native continue_task
- `/stop`: ส่ง stop flag ให้ engine หยุดหลังการกระทำปัจจุบัน และยกเลิกข้อความที่ยังรอ; shell ที่กำลังรันอาจยังไม่หยุดทันที เช่นเดียวกับข้อจำกัด engine เดิม
- `/status`: ดูว่ากำลังตอบหรือพร้อมคุย
- `/jobs`, `/result ID`, `/pause ID`, `/resume ID`, `/cancel ID`: ดูแล scheduled goals ที่เชื่อมกับ Telegram
- `/after`, `/daily`: aliases แปลงเป็นข้อความขอนัดหมายส่งให้ Agent ตัดสินใจใช้ scheduling tools

ไม่ต้องใช้ /work สำหรับงานปกติแล้ว; /observe เป็นข้อความกำกับให้ Agent อ่านอย่างเดียว ไม่ใช่ OS sandbox แยก คำสั่งนัดหมายก็พิมพ์เป็นภาษาปกติได้

ผลของ schedule_create ที่เกิดจาก native tool loop จะถูกเชื่อมกับช่องสนทนาเพื่อส่งรายงานเมื่อ scheduler ทำงานเสร็จ คำถามและการทักทายไม่สร้าง scheduled goals โดยอัตโนมัติ

## สิทธิ์และการส่งข้อความ

รับเฉพาะ numeric owner ID ใน private chat ปฏิเสธ groups/forwarded/edited/bot messages; deduplicate Telegram update ID และไม่ replay interrupted chat turns หลัง crash ข้อความที่มาถึงระหว่างตอบจะรอให้การตอบปัจจุบันจบ โดยไม่แทรกเป็นคำสั่งกลาง tool call

Bot token อยู่ /etc/judecode/telegram.env root0600; model key อยู่ /etc/judecode/agent.env root0600 ตัว bridge ใช้ sandbox/non-root ไม่มี model key ใน environment ส่วน Agent เป็น root จึงสามารถอ่าน secrets ทั้ง host ได้ตามธรรมชาติของสิทธิ์ root

ใช้ model/expert/session-budget/max-turn settings เดียวกับ Mac ที่ตรวจตอน deploy โดยรักษา dedicated Ubuntu API key ไม่คัดลอก key ของ Mac ไม่ใช้เพดาน 20 tasks/วันหรือ10wakeupsของ bridgeเก่ากับการคุย แต่ใช้ขอบเขตของ native engine ตาม config แทน Scheduled goals ยังคงเพดานของ scheduler

Reply delivery เป็น at-most-once attempt; timeout/crashอาจทำให้ข้อความไม่ถึง ไม่มี auto resend ที่ทำให้ข้อความซ้ำ Native transcripts ยังอยู่บนเครื่อง ไม่รับ voice/files จาก Telegram ใน adapter รุ่นนี้ Tools ที่พึ่ง GUI/hardware/โปรแกรมภายนอกยังขึ้นกับ Ubuntu environment

## ดูแล

```sh
systemctl status judecode-chat judecode-telegram judecode-scheduler --no-pager
journalctl -u judecode-chat -n 30 --no-pager
```

หยุดการรับข้อความก่อน maintenance ด้วย `systemctl stop judecode-telegram`; รอ chat/workerปัจจุบันจบก่อน restart Agent หาก kill ระหว่าง tool ทำงาน ผลอาจไม่แน่นอนและจะไม่ replay อัตโนมัติ

Unit: deploy/systemd/judecode-chat.service; ทำงานร่วมกับ shared-scheduler.conf และ group traversal ของ deployment เดิม สำรอง config ก่อนแก้และเก็บ env filesเป็นroot0600

การติดตั้งเก่าแบบทุกข้อความ→scheduler ถูกแทนด้วย ConversationalBridge แล้ว แต่เก็บฐานข้อมูลงานเก่า/ผลลัพธ์ไว้ ไม่แปลง transcriptเทียมจาก job goalsเก่าเข้าบทสนทนาใหม่

## การตรวจสอบ

Tests71ข้อรวม native AgentEngine: greetingไม่มีtools/ไม่มีschedule, historyต่อเนื่อง, read toolจริงเมื่อโมเดลเรียก, schedule_createเฉพาะtoolcall, native transcriptrestoreไม่replay, ownerallowlist, duplicate/crash/stopและscheduler regressions

Live model3turns: ทักทาย+จำคำ(0tools), ถามคำเดิม(0tools), ขออ่านไฟล์(1readtool) และ schedulerไม่มีjobเพิ่ม เก็บผลที่ /var/lib/judecode/native-chat-live-check.log

อ้างอิง transport: [Telegram Bot API](https://core.telegram.org/bots/api#getupdates)
