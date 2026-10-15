# Sandbox-first: พัฒนาและทดสอบก่อนอนุมัติ production

ตั้งแต่รุ่นนี้ทุก entrypoint ปกติของ Jude Code (Terminal/Classic/TUI/Telegram และ
scheduled workers) ใช้ sandbox-first เป็นค่าเริ่มต้น การตรวจอยู่ใน AgentEngine
ก่อนเรียกเครื่องมือ ไม่ได้พึ่งข้อความ system prompt เพียงอย่างเดียว

## พฤติกรรมหลัก

1. เปิด Jude Code ในโฟลเดอร์โปรเจค โปรแกรมทำสำเนา source ลง
   `~/.judecode/workspaces/<id>/project` เมื่อเริ่มใช้ไฟล์
   ไม่คัดลอก .git, .env*, key files ที่รู้จัก, symlinks, node_modules/virtualenv
2. read/write/edit/delete/search ใช้สำเนานี้; ระหว่าง container ทำงาน I/O ทำใน
   namespace ของ container เพื่อไม่ให้ symlink พา host file tools ออกนอกพื้นที่
   ไฟล์ต้นฉบับไม่ได้เปลี่ยน และไม่ copy กลับอัตโนมัติ
3. `sandbox_environment` สร้าง runtime app และบริการประกอบได้สูงสุด 8 ตัว
   เช่น MongoDB/PostgreSQL/Redis/worker/mock API แต่ละตัวเป็น container ใหม่
   ชื่อ service เป็น DNS ภายใน sandbox; ใช้ test env/test data ที่กำหนดเท่านั้น
4. เครือข่าย Docker ใช้ internal + isolated gateway (ต้อง Docker Engine 28+)
   ไม่มี route ไป Internet/LAN/production หรือ host gateway; ไม่มี Docker socket
   และไม่อนุญาต custom host mounts/Compose/network options จากโมเดล
5. `shell` และ `project_verify` ทำงานใน app container เท่านั้น
   ถ้ายังไม่เปิด environment จะปฏิเสธ ไม่มีการ fallback ไป host
6. ใช้ `sandbox_preview` เปิดเว็บผ่าน HTTP relay ที่ผูก localhost
   relay ส่งต่อได้เฉพาะ app:port ที่เลือก ไม่ใช่ generic proxy;
   CSP จำกัด browser assets/requests/forms ให้อยู่ origin เดียวและบล็อก external redirects
7. สร้าง project_plan ให้ครอบคลุมหน้าเว็บ/API/database/worker/integration mocks,
   persistence, invalid inputs, permission checks และ user journeys ที่ร้องขอ
   ทดสอบผ่านไม่เท่ากับขึ้น production; container เปิดได้ไม่เท่ากับทดสอบครบระบบ

ตัวอย่างคำสั่งในแชต:

> สร้าง sandbox ให้ครบทั้ง frontend, API, ฐานข้อมูล และบริการจำลองที่ต้องใช้
> ใส่ข้อมูลทดสอบ เปิด preview แล้วทดสอบทุก user journey ก่อน โดยยังไม่ deploy

โมเดลต้องสำรวจโปรเจคและกำหนดบริการ/คำสั่ง/ข้อมูลทดสอบให้ตรงกับระบบนั้น
ไม่มีวิธีจำลอง production ทุกประเภทอัตโนมัติจากชื่อโปรเจคเพียงอย่างเดียว

## Dependencies และบริการภายนอก

- ใช้ runtime/service images ที่เตรียม dependencies ไว้แล้วได้
- Python 3.12 มี `sandbox_python_packages` รับรายชื่อแพ็กเกจ/ช่วงเวอร์ชัน
  downloader ใช้ image Python แยก ไม่เห็น source หรือ credentials
  ดาวน์โหลด binary wheels จาก PyPI แล้ว install แบบ offline ใน app
  ไม่อนุญาต source distributions/build scripts หรือ package URL
- การ pull images และ downloader เป็น network ขั้นเตรียมที่แยกจาก application
  โค้ดโปรเจคไม่มีสิทธิ์ใช้ helper นี้เป็น shell ออก Internet
- Node20 มี sandbox_node_packages รับ package names/version constraints;
  helper ไม่เห็น source/credentials และ npm install ใช้ --ignore-scripts
  จากนั้น copy node_modules ไป runtime ที่เลือก; native modules ที่ต้อง rebuild
  ให้รันใน sandbox แบบ offline เครื่องมือนี้ไม่แก้ package.json ของ source
- ใช้ service spec workspace=true สำหรับ frontend/worker ที่ต้องอ่าน source copy;
  sandbox_exec(service=...) รันคำสั่งใน runtime นั้น และ sandbox_preview(service=...)
  เปิดเว็บของ service ที่เลือกได้
- ระบบภาษา/OS อื่นที่ต้อง build dependencies ใช้ image ที่เตรียมไว้ก่อน
  ยังไม่มี apt downloader หรือ arbitrary online build ใน strict mode
- Email, payment, production databases และ third-party APIs ต้องใช้ mock services
  หรือ test fixtures ในเครือข่ายนี้ ไม่คัดลอก credentials จริงมาใช้
- ตัวกรอง source ไม่ใช่ secret scanner; credentials ที่ hardcode อยู่ใน source
  ยังอาจถูกคัดลอกได้ แต่ application egress ถูกปิด

## ขึ้นระบบจริงเฉพาะผู้ใช้สั่ง

ผู้ใช้สั่ง deploy → Jude รัน acceptance checks → เรียก `production_prepare` ด้วย
คำสั่ง host ที่จะรันจริง, destination และ rollback instructions
โปรแกรมหยุด test services เพื่อหยุด background writers แล้วผูกแผนกับ SHA256 ของไฟล์

Jude ต้องแสดงแผนเต็มให้ผู้ใช้ตรวจ จากนั้นผู้ใช้พิมพ์:

```text
deploy แผนนี้
```

- คำสั่งนี้ใช้ได้โดยตรงใน chat input ของ Terminal/TUI/Telegram ไม่มี slash ข้างหน้า
- รันเฉพาะคำสั่งที่เตรียมไว้ ด้วย cwd เป็นสำเนา sandbox ที่ทดสอบแล้ว
- ใช้ `DEPLOY <รหัสจากแผน>` ได้เช่นเดิม หรือ `ยืนยัน deploy` / `deploy this plan`
- ต้องผ่าน checks, ยืนยันแผนปัจจุบัน, ไม่หมดอายุ (15 นาที), และ source fingerprint ยังตรง
- ใช้ครั้งเดียวและลบทิ้งก่อนเริ่มรัน; ข้อความโมเดล/tool/scheduler/ประวัติ/resume
  ไม่สามารถอนุมัติได้ และ authorization ไม่ถูกบันทึกเพื่อ restore
- คำสั่ง deploy ทำงานบน host ด้วยสิทธิ์บัญชี Jude Code **เฉพาะการอนุมัตินี้**
  ผู้ใช้ควรเห็น command/destination/rollback ทั้งหมดก่อนส่งรหัส
- ถ้า timeout จะรายงาน outcome unknown; ไม่ retry อัตโนมัติ
- จบการ deploy แล้วเครื่องมือทุกตัวกลับอยู่ภายใต้ sandbox-first เช่นเดิม
- คำสั่ง “แก้ให้เสร็จ”, “ทดสอบผ่านแล้ว”, “ทำต่อ” ไม่อนุญาต deploy

นี่ไม่ใช่ deployment pipeline ที่รู้ rollback ของทุกระบบอัตโนมัติ
โมเดลต้องเตรียมแผนให้ตรงเป้าหมาย ผู้ใช้ตรวจแผนก่อนรัน

## อายุข้อมูลและการกู้คืน

- environment คงอยู่ระหว่างข้อความจนสั่ง sandbox_stop; ไม่ปิดตาม TTL ของ preview รุ่นเก่า
- sandbox_stop ลบ containers/network/anonymous volumes; ข้อมูล DB ทดสอบหาย
  แต่ไฟล์ใน private workspace เก็บไว้ ให้ seed ข้อมูลใหม่เมื่อเริ่ม environment ใหม่
- สถานะอยู่ใน environment.json; transcript เก็บ workspace ID;
  /resume กลับ workspace เดิมและตรวจใหม่ ไม่กู้สิทธิ์ deploy
- ปิด legacy host auto-rollback และ /rollback ใน strict mode; ให้ย้อนแก้ไฟล์ภายใน sandbox
- /sandbox ใน Legacy แสดงสถานะ strict mode; /sandbox stop ปิดบริการทดสอบ
  /sandbox apply ไม่ได้คัดลอกงานไปต้นฉบับ
- ยังไม่มี websocket/HMR proxy; ใช้ HTTP preview และ reload หน้า
- ใช้ local Docker เท่านั้น; URL localhost เปิดบนเครื่องรัน Jude Code
- บริการใช้ image ที่มี application/worker พร้อมแล้ว หรือ workspace=true เพื่อ mount
  private workspace เดียวกับ app; ไม่สามารถเลือก arbitrary host paths ได้
- Runtime/DB/environment ไม่ได้ถูกบรรจุไปกับ deployment โดยอัตโนมัติ:
  แผน deployment ต้องระบุ artifacts และ config ปลายทางให้ชัด

## หลักฐานตรวจ

- ชุดทดสอบ boundary: original files unchanged, path/symlink/grep-option escape,
  host tool bypass, no host shell fallback/evaluator, acceptance routing,
  trusted/untrusted approvals, single-use/expiry/stale source, resume/no authorization
- ทดลอง Docker จริง: app + MongoDB ใหม่ → insert/read → HTTP localhost ผ่าน
  ตรวจ Internet/LAN/host.docker.internal เข้าถึงไม่ได้; original source ไม่เปลี่ยน
- ยังไม่ได้ยืนยัน full HR user journeys หรือ production deployment ใด ๆ
  สิ่งเหล่านั้นต้องใช้ environment/config/checks เฉพาะโปรเจคและคำสั่งผู้ใช้

อ้างอิง: [Docker isolated gateway](https://docs.docker.com/engine/network/port-publishing/)

ผลตรวจสุดท้าย: 445 tests ผ่านบน clean HEAD + เฉพาะ patch นี้
(legacy host/staging tests ระบุ opt-out ใน test constructor โดยตรง;
entrypoints ปกติทั้งหมดใช้ strict default และมี 37 boundary tests แยกต่างหาก)
มี live AgentEngine write/edit/read ที่ไม่เปลี่ยน source และ live MongoDB/HTTP smoke test
เรียกทดสอบ Docker แบบ opt-in ได้ด้วย:

```sh
python scripts/verify_sandbox_first.py
```

script นี้สร้าง MongoDB ใหม่พร้อม test data, ดาวน์โหลด pymongo binary wheels,
ตรวจว่าไม่มี default route, ตรวจ API→DB→HTTP และล้าง test containers/network หลังจบ
ไม่ใช่ benchmark ของระบบ HR หรือการอนุมัติ deploy ระบบใด

ทดสอบเพิ่ม: Node20/Express frontend → Python API → MongoDB → localhost HTTP preview
ด้วย runtime แยกกันและ private source mount; downloader ไม่รัน install scripts

ตัวอย่าง full-stack แบบ opt-in: `python scripts/verify_sandbox_fullstack.py`


## แก้เส้นทาง deployment (15 ตุลาคม 2026)

`production_context` บอก private workspace บน host และเครื่องมือที่ติดตั้ง เช่น
ssh/rsync/bash โดยไม่รัน project code และไม่เชื่อมต่อ production
`production_prepare` รับ `cwd`, `required_commands`, `timeout` (ค่าเริ่มต้น 600 วินาที;
สูงสุด 3600) ตรวจเครื่องมือที่ระบุและพาธก่อนหยุด preview/ขออนุมัติ

ตัวอย่างเมื่อมี `hr-system/release-artifact.sh` ที่ทดสอบแล้ว:

```json
{
  "command": "bash release-artifact.sh",
  "cwd": "/workspace/hr-system",
  "destination": "server:/application",
  "rollback": "restore the backup created by release-artifact.sh",
  "required_commands": ["bash", "ssh", "rsync", "curl"]
}
```

แปลง cwd ไปยัง private workspace บน host ให้เอง รองรับชื่อภาษาไทยและช่องว่าง
และรองรับรูปแบบเดิม `cd /workspace/subdir && bash release-artifact.sh`
ไม่แปลง shell syntax ซับซ้อนหรือพาธที่ฝังในสคริปต์โดยเดา

ต้อง build/test ใน sandbox แล้วส่ง artifact ที่ตรวจแล้ว สคริปต์เดิมที่ใช้
`git fetch/status/rev-parse` จะถูกแจ้งให้ปรับก่อนอนุมัติ เพราะสำเนาไม่มี `.git`
Jude ต้องเขียนสคริปต์ส่ง artifact พร้อม backup/activate/health check ให้ตรงระบบ
ไม่สร้าง Git ปลอม ไม่ใช้ source ต้นฉบับที่ยังไม่มีงานแก้ และไม่ให้ผู้ใช้หลบไป
รันคำสั่งเองใน terminal เป็นวิธีแก้ปัญหา
การตรวจสคริปต์เป็นการตรวจเบื้องต้น ไม่ใช่การวิเคราะห์ shell/dependencies ทุกชนิด

รองรับ symlink ภายใน workspace เช่น npm `.bin` โดย hash ทั้งลิงก์และไฟล์เป้าหมาย
ยังปฏิเสธลิงก์ออกนอกพื้นที่หรือปลายทางไม่มีอยู่จริง
ผลล้มเหลวแสดง exit code/stderr และแนวทางซ่อมแผน ไม่ retry production อัตโนมัติ

ผลตรวจหลังแก้ deployment: 462 tests ผ่านบน clean HEAD + patch ของงานนี้
และ Docker build → ยืนยันแผน → ส่ง artifact ไปปลายทาง local จริง ผ่าน
รันทดลองซ้ำได้ด้วย `python scripts/verify_sandbox_release.py`
ยังไม่ได้ deploy หรือยืนยันความพร้อมของระบบ HR จริง

## ป้องกันการวนซ้ำระหว่างเตรียม deploy

ตัวตรวจการเรียกซ้ำครอบคลุมเครื่องมือ sandbox และ project ด้วย เมื่อ arguments/result
เหมือนเดิม 6 ครั้งในรอบงานเดียวจะหยุดเป็น unverified; package installers,
environment setup และ production_prepare ใช้ขีดจำกัด 3 ครั้ง การเรียกที่เหลือใน batch
จะไม่ทำงานต่อหลังตรวจพบ loop ข้อความเตือน verification จะแสดงเมื่อสถานะเปลี่ยน
แทนการแทรกข้อความเดิมทุกเทิร์น

เมื่อ production_prepare สำเร็จ จะหยุดรอมนุษย์ทันที รวมถึงข้าม tools ที่เหลือใน batch
และ /continue จะเพียงแสดงแผนรอยืนยัน ข้อความคำสั่งใหม่จากมนุษย์ที่ไม่ใช่การอนุมัติ
ยกเลิกแผนค้างเพื่อให้แก้ไขงานต่อได้ การหยุด loop ไม่ได้อนุมัติ production

ตรวจ regression 468 tests ผ่าน รวมแชตจำลองที่ติดตั้งแพ็กเกจวนและต้องจบหลัง 3 ครั้ง
ไม่มีการส่งคำขอโมเดลเพิ่มเติม ต้องเปิด process Jude ใหม่เพื่อโหลดการแก้ไข
