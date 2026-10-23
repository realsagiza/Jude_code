# ผู้กำกับหนังใหม่: Hugging Face → Runpod → ไฟล์ในเครื่อง

ผู้กำกับชุดนี้สร้างแยกจาก `media_director` / `media_tools` เดิม และเส้นทางสร้างคลิป
เสียงพูด และเสียงประกอบ **ไม่เรียก OpenRouter media API** ผู้ใช้คุยกับ Jude ตัวเดิม
แต่การสร้างสื่อทำบน GPU Pod ของ Runpod ด้วยโมเดลสาธารณะจาก Hugging Face เท่านั้น

> โมเดลที่ Jude ใช้คิด/สนทนาเป็น provider แยกต่างหาก การแยกนี้รับประกันว่า **media
> generation** ไม่ผ่าน OpenRouter ไม่ได้เปลี่ยน provider ของแชต Jude Code

## บทบาท

- **Film Director** — คิด brief, continuity bible, จังหวะเรื่อง, shot prompt, narration
  และ sound prompt; บันทึก storyboard ผ่าน `film_plan`.
- **Runpod Operations specialist** — โค้ด Python ที่ deterministic (ไม่เรียก LLM เพิ่ม)
  ตรวจ metadata/license/revision/weights ของ HF, stock/ราคา Secure GPU, CUDA/VRAM/disk,
  สร้าง Pod, เปิดเฉพาะ HTTP port ที่ต้องใช้, ตรวจ readiness, ดาวน์โหลดไฟล์ และ terminate
  เฉพาะ Pod ที่มี owner marker ของงานนั้น.
- **Local assembler** — หลังไฟล์ทั้งหมดลงเครื่องและ Pod ถูก terminate แล้ว ใช้ FFmpeg
  ในเครื่องรวมคลิป/เสียงเป็น MP4; ไม่ส่งวิดีโอกลับขึ้น Cloud เพื่อประกอบ.

## ข้อจำกัดการเลือกรุ่น

ค้นได้จาก HF Hub แต่รันเฉพาะ pipeline ที่ worker รองรับ:

| งาน | Hugging Face pipeline | Runtime |
|---|---|---|
| คลิป text-to-video / image-to-video | `text-to-video`, `image-to-video` | Diffusers |
| เสียงพากย์ | `text-to-speech` | Transformers |
| เพลง/เสียงประกอบ | `text-to-audio` | Transformers หรือ Diffusers |

ผู้กำกับใหม่สร้าง **คลิปเคลื่อนไหวกับเสียง/เสียงพากย์** เท่านั้น ไม่ได้สร้าง still image
หรือโปสเตอร์; รุ่น `image-to-video` ต้องแนบไฟล์อ้างอิง PNG/JPEG/WebP จากเครื่องเอง
(ไม่เกิน 20 MiB/ภาพและไม่เกิน 50 ล้านพิกเซล). รุ่น `text-to-video` ใช้ prompt โดยตรง.

ใช้เฉพาะ repo สาธารณะที่มี license, revision และ `safetensors`; model-card Python,
`trust_remote_code` และคำสั่งติดตั้งจาก repository จะไม่ถูกเรียกใช้ รุ่น gated/private,
ไม่มี license หรือไม่มี weights ที่รองรับจะถูกปฏิเสธก่อนสร้าง Pod ผู้กำกับควรเลือกภาษา
และ license ให้ตรงกับงาน; หากเงื่อนไขใช้งานไม่ชัดให้ถามก่อน ไม่เดาว่าใช้เชิงพาณิชย์ได้

VRAM ที่ HF model metadata ใช้วางแผนเป็น **ค่าประมาณแบบเผื่อ** ไม่ใช่ผลวัดจริง; worker
อาจไม่รองรับโมเดล/เวอร์ชันที่เพิ่งเผยแพร่แม้มี pipeline tag ถูกต้อง ถ้า model ไม่โหลด
จะบันทึก error, หยุด ไม่สลับรุ่นหรือส่งงานคิดเงินซ้ำอัตโนมัติ

## ติดตั้งครั้งแรก

1. สร้าง/push image ตามคู่มือ [`plugins/runpod/film-worker/README.md`](../plugins/runpod/film-worker/README.md)
   สำหรับ Linux/amd64; image registry และสิทธิ์ pull ต้องเป็นของผู้ใช้เอง.
2. ตั้งค่าในไฟล์ `.env` ที่ ignored ในเครื่อง:

   ```dotenv
   RUNPOD_API_KEY=...
   JUDECODE_FILM_WORKER_IMAGE=registry.example/ของคุณ/jude-film-worker:version
   JUDECODE_FILM_MAX_RUNTIME_SECONDS=3600
   JUDECODE_FILM_MAX_COST_USD=5.00
   JUDECODE_FILM_HARD_MAX_COST_USD=25.00
   JUDECODE_FILM_MAX_DISK_GB=200
   JUDECODE_FILM_MIN_CUDA_VERSION=12.8
   ```

   ชื่อ image ข้างบนเป็นตัวอย่างรูปแบบเท่านั้น ต้องใส่ image ที่ build/push จริง
   ห้ามใส่ Runpod API key ใน image, Git หรือ model worker.
3. Restart Jude Code ให้โหลด environment/config และ tools ใหม่.

ไม่ต้องตั้ง Hugging Face token สำหรับโมเดลสาธารณะที่เข้าเกณฑ์ ระบบปฏิเสธ gated/private
model แทนการส่ง token เข้า Pod.

## วิธีใช้งาน

1. สั่งผู้กำกับด้วยภาษาปกติ เช่น “ทำหนังสั้น 3 ฉาก แมวหาทางกลับบ้าน ภาพแนวตั้ง
   มีเสียงพากย์ไทยและเสียงฝน”.
2. Agent เรียก `film_research` สำหรับ video/voice/sound, อ่าน license/ภาษา/VRAM/ขนาด
   และ stock/ราคาจากข้อมูลปัจจุบัน.
3. Agent สร้าง storyboard ผ่าน `film_plan`; ยังไม่มีการสร้าง Pod หรือคิดค่า GPU.
4. เมื่อเริ่ม `film_produce` จะใช้ 1 Secure GPU Pod สำหรับงานที่ขาด:
   - ไม่สร้าง Network Volume หรือ persistent disk; weights อยู่บน container disk ชั่วคราว.
   - มี `8000/http` เท่านั้น; ไม่มี SSH/Jupyter. Worker ตรวจ bearer token ต่อ request.
   - ส่ง job แบบ idempotency ID, poll งานเดิม, โหลด MP4/WAV ลงเครื่อง และตรวจ SHA-256.
   - terminate Pod หลังโหลดเสร็จ; `finally` พยายาม terminate แม้เกิด error.
   - รวมหนังด้วย FFmpeg ในเครื่อง หลังยืนยันการ cleanup.
5. ไฟล์อยู่ใต้ `JUDECODE_FILM_OUTPUT_DIR/<project_id>/`; project/checkpoints ที่ไม่มี
   secret อยู่ใต้ `JUDECODE_FILM_STATE_DIR/<project_id>/`.

Final assembly ต้องมี `ffmpeg` ใน PATH หรือให้ติดตั้ง `imageio-ffmpeg` ใน environment
ของ Jude ด้วย `python -m pip install -r requirements-media.txt`.

`film_status` อ่านแผน/receipt/local paths ได้โดยไม่ติดต่อ Cloud. ถ้าหยุดโปรแกรมกลางคัน
ให้เช็กสถานะก่อน; ถ้า Pod ยังค้าง ให้เรียก `film_cleanup(project_id)` ซึ่งลบได้เฉพาะ Pod
ที่ owner marker ตรงกัน. ผลงานที่โหลดลงเครื่องแล้วจะไม่ถูกลบตอน cleanup.

## ค่าใช้จ่ายและขอบเขตการ cleanup

- ระบบเลือก GPU จาก current Secure Pod catalog ที่มี stock และราคา, กรอง VRAM/CUDA
  แล้วคิดประมาณการ `ราคา GPU/ชั่วโมง × runtime cap` ก่อนสร้าง Pod; แจ้งค่าประมาณ,
  รุ่น GPU, rate และ cap ในผลลัพธ์.
- ค่านี้ **ไม่ใช่ hard billing cap จาก Runpod** และอาจไม่ตรงใบแจ้งหนี้/ส่วนลด/ค่า disk;
  cleanup ในแอปบวก runtime timeout ลดความเสี่ยง แต่ host crash หรือ Runpod API outage
  อาจทำให้ terminate ไม่สำเร็จ. ถ้า `cleanup_status=cleanup_pending` ให้เรียก
  `film_cleanup` และตรวจหน้า Runpod ก่อนเริ่มงานใหม่.
- งานปกติไม่มี persistent storage; Pod ถูก terminate แล้วข้อมูลบน Pod หายทั้งหมด.
  มีเพียง MP4/WAV ที่โหลดกลับมาและ manifest ในเครื่องที่คงอยู่.
- การดาวน์โหลดไม่สำเร็จจะไม่ส่ง generation ซ้ำเอง; cleanup Pod เพื่อหยุดค่าใช้จ่ายและ
  แจ้งสถานะ/asset ที่ขาด. การลองสร้างใหม่ต้องได้รับคำสั่งจากผู้ใช้.

## สถานะการตรวจสอบ

- **Authored:** controller, worker API, Dockerfile, local assembler, model discovery,
  Pod lease/cleanup และ tests.
- **Local/static:** ต้องรายงานตาม test ที่รันจริงหลังแก้ไข.
- **Image build / GPU / live Runpod:** ยังไม่ยืนยันจนกว่าจะ build/push image และมีการทดสอบ
  workload จริงที่ผู้ใช้อนุญาต; mock tests ไม่เท่ากับ GPU acceptance.
