# เครื่องทดสอบระหว่างพัฒนา

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
  ไม่มี port เปิดออก host, interactive preview, database companion หรือ Compose ในรุ่นนี้
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
