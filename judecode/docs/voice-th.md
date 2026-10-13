# พูดคุยภาษาไทยกับ JudeCode ผ่าน OpenRouter

ใช้ได้ในหน้าจอหลัก `judecode` (TUI) เท่านั้น เปิดโปรแกรมใหม่หลังอัปเดต
ใช้ OpenRouter สำหรับทั้งถอดเสียงและสร้างเสียง โดยใช้ API key เดิม
โมเดลเขียนโค้ดและประวัติสนทนายังทำงานตาม config เดิม

## เริ่มใช้

ติดตั้ง dependency ไมค์ใน Python environment ที่รัน JudeCode:

```sh
python -m pip install -r requirements-voice.txt
judecode
```

- `/voice on` เปิดอ่านคำตอบใหม่ด้วยเสียง AI; เริ่มต้นจะปิดเสมอ
- `/listen` อัดไมค์ 8 วินาที หรือ `/listen 15` อัด 15 วินาที (สูงสุด 60)
- พูดภาษาไทยหรือไทยปนอังกฤษ แล้วรอข้อความในช่องพิมพ์ ตรวจแก้ก่อนกด Enter ส่งให้ agent
- `/voice stop` หยุดอัด/ถอดเสียง/เล่นเสียงรอบนี้
- `/voice off` ปิดอ่านคำตอบอัตโนมัติ
- `/voice status` ดูสถานะ
- Esc หรือ `/stop` หยุดเสียงพร้อมขอหยุด agent; Ctrl+C หยุดเสียงเมื่อไม่มีข้อความเลือก

ไมค์เปิดเฉพาะตอนสั่ง `/listen` ไม่ได้ฟังตลอดเวลา และไม่ใช่การคุยสดแบบพูดแทรก
ถ้าพิมพ์ข้อความระหว่างถอดเสียง จะรักษาข้อความที่พิมพ์ไว้และแสดงผลถอดเสียงใน Output แทน
รอ agent ว่างก่อนสั่ง `/listen` เพื่อหลีกเลี่ยงเสียงตอบกลับเข้าไมค์

## ตั้งค่า

ตั้งใน `.env` เดิมของ JudeCode (ไม่ต้องมีคีย์ OpenAI แยก):

```dotenv
JUDECODE_OPENROUTER_API_KEY=your-openrouter-key
JUDECODE_VOICE_STT_MODEL=openai/gpt-4o-mini-transcribe
JUDECODE_VOICE_TTS_MODEL=google/gemini-3.8-flash-tts
JUDECODE_VOICE_NAME=Kore
JUDECODE_VOICE_SAMPLE_RATE=24000
```

รองรับ `OPENROUTER_API_KEY` เป็นค่า fallback เช่นเดียวกับระบบเดิม
STT ส่ง language hint `th`; ชื่อเสียงและภาษาที่รองรับขึ้นกับโมเดลที่เลือก
รับเสียงแบบ PCM 16-bit mono แล้วห่อ WAV; หากเปลี่ยนโมเดลให้ตั้ง sample rate ตรงกับ provider
เสียงอ่านเฉพาะคำตอบสุดท้าย ข้าม tool output และโค้ดใน fenced blocks
คำตอบยาวอ่านได้ไม่เกินประมาณ 3,000 ตัวอักษร พร้อมแจ้งให้อ่านส่วนที่เหลือบนหน้าจอ

macOS: อนุญาต Microphone ให้ Terminal/iTerm ที่ใช้งาน เล่นเสียงด้วย `afplay` ที่ติดมากับระบบ
Windows/Linux: ติดตั้ง FFmpeg ให้คำสั่ง `ffplay` อยู่ใน PATH
Linux อาจต้องติดตั้ง PortAudio ของระบบเพิ่มสำหรับ sounddevice

เสียงไมค์ถูกส่งไป OpenRouter เมื่ออัดเสร็จ; คำตอบถูกส่งไปสร้างเสียงเมื่อเปิด voice
เสียงอัดอยู่ในหน่วยความจำ; ไฟล์เล่นเสียงเป็นไฟล์ชั่วคราวและลบหลังเล่นหรือยกเลิก
ข้อความที่กดส่งถูกเก็บในประวัติตามระบบสนทนาเดิม
ค่า STT/TTS เพิ่มจากการคุยปกติ และ **ยังไม่รวมใน `/budget`**; ตรวจยอดจริงใน OpenRouter
ยกเลิกหลังส่ง API แล้วอาจยังมีค่าใช้จ่ายของคำขอนั้น

## เอกสาร API

- [OpenRouter transcription](https://openrouter.ai/blog/tutorials/transcription-on-openrouter/)
- [OpenRouter text-to-speech](https://openrouter.ai/docs/guides/overview/multimodal/tts)

หาก HTTP 400/404 ให้ตรวจว่าบัญชีและ provider ยังรองรับโมเดลที่ตั้งไว้
หาก HTTP 401/402 ให้ตรวจคีย์และเครดิต; ความผิดพลาดของเสียงไม่หยุดการคุยด้วยข้อความ
