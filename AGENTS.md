# 🤖 AGENTS.md — Jude Code Boot Protocol

> ⚠️ **ไฟล์นี้ Jude Code อ่านอัตโนมัติเมื่อเริ่ม session ใหม่**  
> 📅 Created: 2026-07-18 | 🔄 Last Updated: 2026-07-18

---

## 🚀 BOOT PROTOCOL — ทำทันทีเมื่อเปิด session ใหม่

### Step 1: อ่าน Memory (ทำก่อนตอบผู้ใช้)
อ่าน notes เหล่านี้จาก Knowledge Vault ตามลำดับ:

1. **`jude-code-memory-index`** ⭐ — สถานะปัจจุบันของทุกโปรเจค, TODO ค้าง, last session
2. **`jude-code-operating-rules`** ⭐ — กฎบังคับ 7 ข้อ
3. **`jude-code-session-log`** ⭐ (อ่าน entry ล่าสุด) — รู้ว่า session ที่แล้วทำอะไรถึงไหน
4. **`user-profile-thanaphol`** — เข้าใจผู้ใช้และสไตล์การทำงาน

```python
# ตัวอย่างการอ่าน (ใช้ tools):
vault_read_note(title="jude-code-memory-index")
vault_read_note(title="jude-code-operating-rules")
vault_read_note(title="jude-code-session-log")  # อ่าน entry ล่าสุด
vault_read_note(title="user-profile-thanaphol")
```

### Step 2: ทักทาย + สรุปสถานะ
หลังอ่าน memory แล้ว ทักทายผู้ใช้พร้อมสรุป:
- Session ที่แล้วทำอะไร
- TODO ที่ค้างอยู่
- ถามว่าวันนี้จะทำอะไร

**รูปแบบ:**
```
👋 สวัสดีครับ! ผมจำได้ว่า session ที่แล้ว (วันที่ X) เราทำ [Y] กันถึง [Z]
📌 งานที่ค้างอยู่: [TODO]
🚀 วันนี้จะทำอะไรต่อดีครับ?
```

---

## 📜 กฎบังคับ (Operating Rules)

ดูรายละเอียดเต็ม: `vault_read_note(title="jude-code-operating-rules")`

| # | กฎ | เมื่อไหร่ |
|---|-----|---------|
| 1 | อ่าน Memory ก่อนเริ่ม | ทุก session ใหม่ |
| 2 | Commit + Push | ทุกครั้งที่แก้โค้ดเสร็จ |
| 3 | จดบันทึก | ทุกครั้งที่ทำงานสำคัญ |
| 4 | สรุป Session | เมื่อจบ session |
| 5 | Plan-First | ทุกครั้งที่ได้คำสั่ง |
| 6 | Clean Output | ตลอดเวลา |
| 7 | Safety First | ตลอดเวลา |

---

## 📝 การจดบันทึก (ต้องทำทุกครั้ง)

### เมื่อทำงานสำคัญเสร็จ:
1. **Append ลง Session Log:**
   ```
   vault_append_note(
     title="jude-code-session-log",
     content="## [YYYY-MM-DD HH:MM] <หัวข้อ>\n- 🎯 ...\n- ✅ ...\n- 🔖 Commit: <hash>\n- ⏭️ TODO: ..."
   )
   ```

2. **อัพเดท Memory Index** (ถ้าสถานะโปรเจคเปลี่ยน):
   ```
   vault_update_note(title="jude-code-memory-index", content="...")
   ```

### เมื่อจบ session:
1. สรุปสิ่งที่ทำ
2. Append สรุปลง `jude-code-session-log`
3. อัพเดท `jude-code-memory-index` ส่วน Last Session + TODO
4. Commit + Push (ถ้ามีการเปลี่ยนแปลง)

---

## 🗂️ โปรเจคนี้: Jude Code (ตัวเอง)

- **Path:** `~/Code/Jude_code`
- **ภาษา:** Python
- **คำอธิบาย:** AI coding assistant ตัวช่วยเอง
- **Identity:** `vault_read_note(title="agent-identity-jude-code-จดโคด")`
- **Roadmap:** `JudeCode_Agentic_Roadmap.md`

### โครงสร้างสำคัญ
```
~/Code/Jude_code/
├── AGENTS.md                    ← ไฟล์นี้ (Boot Protocol)
├── judecode/                    ← Source code หลัก
├── task_manager/                ← Task/Kanban system
├── JudeCode_Agentic_Roadmap.md  ← Roadmap
└── tests/                       ← Tests
```

---

## 🔗 Quick Links (Vault Notes สำคัญ)

| Note | ใช้สำหรับ |
|------|----------|
| `jude-code-memory-index` | ⭐ Master — สถานะทุกโปรเจค |
| `jude-code-operating-rules` | ⭐ กฎบังคับ |
| `jude-code-session-log` | ⭐ Log ทุก session |
| `user-profile-thanaphol` | โปรไฟล์ผู้ใช้ |
| `master-project-index-ทกโปรเจคของ-thanaphol` | Index 52 โปรเจค |
| `agent-identity-jude-code-จดโคด` | Identity ของ Jude Code |

---

## 💡 หมายเหตุ

- ไฟล์นี้คือ "จุดเริ่มต้น" — ถ้า Jude Code เปิดมาและเห็นไฟล์นี้ ให้อ่าน Memory ตาม Step 1 ทันที
- ถ้าย้ายไปโปรเจคอื่น ให้สร้าง `AGENTS.md` ในโปรเจคนั้นด้วย (ปรับเนื้อหาตาม context)
- ระบบนี้ทำงานร่วมกับ Knowledge Vault ที่ `~/.judecode/vault/`
