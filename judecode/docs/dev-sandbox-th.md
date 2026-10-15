# เครื่องทดสอบระหว่างพัฒนา

> โหมดปกติเปลี่ยนเป็น [sandbox-first](sandbox-first-th.md) แล้ว: เครื่องมือ legacy
> dev_sandbox_run/dev_preview_* ที่เปิด outbound ถูกบล็อกใน AgentEngine ปกติ
> ใช้ sandbox_environment/shell/sandbox_preview ตามคู่มือใหม่ ข้อมูลด้านล่างเป็นรุ่นเดิม

Jude Code มี `dev_sandbox_run` ให้โมเดลสร้าง Linux container ชั่วคราวจาก snapshot
ของโปรเจค ติดตั้ง dependencies รัน test/build/API/browser test แล้วล้าง container
บน Mac ใช้ Docker ผ่าน Colima VM; Linux ใช้ Docker Engine ที่กำลังทำงาน
นี่เป็นเครื่อง Linux สำหรับทดสอบ ไม่ได้จำลอง production infrastructure ทุกส่วน

## ใช้ในแชต

“ทดสอบโปรเจคนี้ใน dev sandbox ก่อน deploy ใช้ Python 3.12
ติดตั้ง requirements และ pytest แล้วรัน tests ถ้าไม่ผ่านให้แก้และทดสอบใหม่”

โมเดลเรียกเครื่องมือด้วย root, command และเลือก image/setup/network/timeout
เช่น image=`python:3.12-slim`, setup=`pip install -r requirements.txt pytest`,
command=`python -m pytest -q`, network=true, timeout=120
Dependencies ต้องเปิด network หรือใช้ image ที่เตรียมไว้แล้ว
เลือก image ที่ตรง runtime ของโปรเจค เช่น `node:22-slim` สำหรับ Node
Browser tests ต้องเลือก image ที่มี browser และ dependencies ตรงกับ test runner

## ใช้โดยตรง / ผูกกับ project_plan

ใช้ Python ตัวเดียวกับที่ติดตั้ง Jude Code:

```sh
python -m judecode.agent.dev_sandbox --root /path/to/project \
  --image python:3.12-slim --network \
  --setup 'pip install -r requirements.txt pytest' \
  'python -m pytest -q'
```

เพิ่มคำสั่งนี้เป็น check ใน `project_plan` เพื่อให้ `project_verify` บันทึกผลจริง
CLI exit 1 เมื่อ test/setup/cleanup ล้มเหลว, timeout หรือพบ empty-suite summary
เลือก timeout ให้เหมาะกับ outer verification budget (คำสั่ง project_verify จำกัด 120s)
การเรียก tool อย่างเดียวไม่แทน acceptance contract และไม่อนุญาต deploy โดยอัตโนมัติ
แนวทางถูกเพิ่มใน system prompt; ไม่มีตัวดักคำสั่ง deploy ทุกชนิดแบบบังคับระดับ OS

## ขอบเขตและหลักฐาน

- snapshot ใหม่ทุกครั้ง รวมไฟล์แก้ที่ยังไม่ commit; ไม่แก้ไฟล์ต้นฉบับ
- ไม่ mount host directory, Docker socket หรือส่ง environment ของ host
- ข้าม .env*, key files ที่รู้จัก, symlinks, .git, virtualenv, node_modules และ local state
  ตัวกรองนี้ไม่ใช่ secret scanner: อย่าเก็บ credentials ใน source หรือคำสั่ง
- ปิด network เป็นค่าเริ่มต้น; network=true เปิด outbound รวม LAN
  ใช้ dependency install/test services และข้อมูลทดสอบ ห้ามชี้ production
- จำกัด CPU 2, RAM 1GiB, 128 processes, drop capabilities และ no-new-privileges
- Docker image ต้องเชื่อถือได้ มี /bin/sh, sleep, tar และ writable /workspace
- เลือก image digest หากต้องการทำซ้ำกับ image เดิมแน่นอน
- เก็บ command, setup, source fingerprint, resolved image ID, exit code,
  output (ท้ายสุดไม่เกิน 24KB ต่อขั้นตอน), cleanup status ในผล tool/ประวัติแชต
- source ไม่เกิน 256MiB; timeout 1–600 วินาทีต่อ setup/test (pull สูงสุด 300s)
- test เปิด server แล้วเรียก localhost ภายใน container เดียวกันได้
  dev_sandbox_run ไม่มี port เปิดออก host; web preview ใช้เครื่องมือแยกด้านล่าง
  ยังไม่มี database companion หรือ Compose
- ผลผ่านยืนยันเฉพาะ assertions ที่รัน ต้องเขียน user-journey tests ตามงานจริง
- ถ้า daemon ล่มระหว่าง cleanup ผลจะเป็น failed พร้อมชื่อ container สำหรับตรวจสอบ
  ถ้า process ถูก SIGKILL อาจเหลือ container: ตรวจ `docker ps -a --filter label=judecode.dev-sandbox=true`

## Runtime

Mac เครื่องนี้เปิด Colima แล้ว (2 CPU, RAM 4GiB, disk 30GiB)
หลัง reboot ถ้ายังไม่รัน ให้เปิด `colima start`; ตรวจ `docker info`
เปิดแชต/โปรเซส Jude Code ใหม่เพื่อโหลด tools และ prompt ใหม่
Ubuntu ต้องติดตั้ง source รุ่นนี้และมี Docker daemon แยกต่างหาก

อ้างอิงข้อจำกัด container: [Docker run](https://docs.docker.com/reference/cli/docker/container/run/)
และ [network none](https://docs.docker.com/engine/network/drivers/none/)

## ผลตรวจรุ่นแรก (2026-10-15)

- 110 tests ผ่านบน clean HEAD + เฉพาะ patch นี้ (sandbox, project contract,
  reliability, interruption, continuation, expert routing)
- Docker/Colima จริง: HTTP server + request ภายใน container ผ่าน;
  ยืนยันไม่มี .env/host socket, offline มีเฉพาะ loopback, source เดิมไม่เปลี่ยน
- live assertion failure, timeout, empty suite ถูกปฏิเสธ; ล้างครบทุก container
- live pip install pytest แล้ว import ได้เมื่อเปิด network
- ยังไม่ได้ benchmark การเลือกเครื่องมือโดยโมเดลจริงหรือ browser UI E2E;
  ยังไม่ได้ติดตั้ง source รุ่นนี้บน Ubuntu หรือ rebuild legacy .app


## Web preview (เพิ่ม 2026-10-15)

สั่งในแชต: “เปิด web preview ของโปรเจคนี้ให้ดูหน่อย”
Jude Code มีเครื่องมือ `dev_preview_start`, `dev_preview_status`, `dev_preview_stop`
และจะส่ง URL เช่น `http://127.0.0.1:32768/` ให้กดเปิดใน browser

ตัวอย่าง arguments ของ dev_preview_start:

```json
{
  "root": "/path/to/web-project",
  "image": "node:22-slim",
  "setup": "npm ci",
  "command": "npm run dev -- --host 0.0.0.0 --port 5173",
  "port": 5173,
  "timeout": 120,
  "ttl": 3600
}
```

- server ต้องรัน foreground และ bind `0.0.0.0` ภายใน container
- Docker เลือก host port ว่างอัตโนมัติ ผูกเฉพาะ `127.0.0.1`
- ใช้ bridge network เพื่อ publish port จึงมี outbound/LAN access; ใช้ test data เท่านั้น
- URL ใช้บนเครื่องที่รัน Jude Code; ไม่ใช่ public URL และเปิดจากมือถือผ่าน Telegram ไม่ได้
- start รอ HTTP 2xx/3xx ที่ ready_path (default `/`, ไม่ follow redirects); timeout แล้วล้าง container
  HTTP 4xx/5xx ถือว่ายังไม่พร้อม หากแอปไม่มี route `/` ให้ตั้ง ready_path เช่น `/docs` หรือ `/health`
- status แสดง URL, HTTP status, expiry และ logs ล่าสุด; readiness ไม่ใช่ test success
- หมดอายุในหนึ่งชั่วโมง ปรับได้ 5 วินาที–4 ชั่วโมง; timer รวมช่วงติดตั้ง dependencies
- ปิดด้วย dev_preview_stop โดยใช้ชื่อ container จากผล start
  ลบได้เฉพาะชื่อและ label ของ preview; container ที่หมดอายุแล้วจะรายงานว่าไม่พบ
- snapshot แยกจาก source ไม่มี live reload จาก host: แก้โค้ดแล้ว stop/start ใหม่
- ใช้ Docker daemon ในเครื่องเท่านั้น ไม่รองรับ remote Docker context
- ทดสอบจริง: เปิด HTTP/read content, status, stop, expiry, readiness timeout และ setup failure ผ่าน

การผูกพอร์ตอ้างอิง [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/)
ใช้ Docker Engine 28 ขึ้นไปเพื่อหลีกเลี่ยงข้อจำกัด localhost publishing ของรุ่นเก่า


## แก้ปัญหาเปิด sandbox ไม่ได้ (2026-10-15)

- ติดตั้ง pip/npm dependencies: กำหนด network=true และใช้ setup ติดตั้ง requirements ของโปรเจค
- apt/apk: เพิ่ม system_packages=true (CLI: --system-packages) เพื่อให้สิทธิ์จำกัด
  CHOWN/DAC_OVERRIDE/FOWNER/SETUID/SETGID ใน container เท่านั้น; default ยัง drop ทั้งหมด
- การติดตั้งโดยปิด network หรือไม่เปิดสิทธิ์ OS packages จะคืนคำแนะนำก่อนสร้าง container
- ไม่ซ่อน output ของ setup ด้วย redirect หรือ pipe ผ่าน tail ซึ่งอาจกลบ failure
- Preview ที่ setup/server จบทันที จะเก็บ logs + startup_exit_code ให้เครื่องมืออ่านก่อน cleanup
  ขณะที่ไม่มี caller มารับ error container ยังถูก TTL ล้างตามเดิม
- API ที่ไม่มีหน้า root ใช้ ready_path=/docs หรือ endpoint สุขภาพที่มีจริง
- ผลตรวจ: apt-get install curl จริงผ่าน, log ของ setup failure/uvicorn import failure
  ไม่หาย; HTTP/status/stop/TTL/readiness timeout ยังผ่าน
