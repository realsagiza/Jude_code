# 🎯 JudeCode Kanban Planning System

## วิธีใช้ระบบนี้

ระบบนี้ถูกออกแบบมาให้ **JudeCode (AI)** ใช้เองอัตโนมัติทุกครั้งที่คุณขอให้ทำงาน

---

## 🤖 AI จะทำอะไรให้คุณโดยอัตโนมัติ

ทุกครั้งที่คุณพิมพ์คำสั่ง:

### 1️⃣ สร้าง Kanban Board
AI จะสร้าง board ใน Knowledge Vault โดยอัตโนมัติ
```
📥 Backlog → 📋 To Do → ⚡ In Progress → ✅ Done
```

### 2️⃣ บันทึก Session Context
กัน AI ลืม! บันทึกว่า:
- คุณขออะไรไว้ (Original Request)
- ทำอะไรไปแล้วบ้าง (Progress)
- ตัดสินใจอะไรไว้แล้ว (Key Decisions)
- แก้ไขไฟล์อะไรบ้าง (Files Modified)

### 3️⃣ Checkpoint ทุกครั้ง
บันทึกจุด Resume ไว้ทุกครั้งที่ทำงานเสร็จ 1 ขั้นตอน
- ถ้าระบบล่ม → อ่าน checkpoint แล้วกลับมาทำต่อ
- ถ้า Token Limit → อ่าน checkpoint แล้วมาต่อ

### 4️⃣ Commit + Push เมื่อทุกอย่างเสร็จ
ทำ Git commit โดยอัตโนมัติเมื่อ Kanban Card เสร็จ

---

## 🔍 อยากดู Kanban Board ปัจจุบัน?

ใช้คำสั่ง:
```bash
ls ~/.judecode/vault/
cat ~/.judecode/vault/kanban-<ชื่อ>.md
```

หรือให้ AI `vault_list_notes` / `vault_search` ให้

---

## 📂 โครงสร้างระบบ

```
~/.judecode/vault/
├── kanban-plan-template.md        ← Template สำหรับ Kanban
├── session-context-active.md       ← Context ปัจจุบัน (กันลืม)
├── checkpoint-resume-log.md        ← จุด Resume (กันล่ม)
├── kanban-<project>.md             ← Kanban Board ของแต่ละงาน

/your-project/
├── judecode-protocol-kanban-workflow.md  ← Protocol หลัก
├── kanban-workflow-readme.md             ← README นี้
└── judecode/plugins/kanban_automation.py ← Script อัตโนมัติ
```

---

## 🔄 เมื่อระบบล่ม / Error

**ไม่ต้องตกใจ!** เมื่อ AI กลับมา มันจะ:
1. อ่าน `checkpoint-resume-log.md`
2. อ่าน `session-context-active.md`
3. ดูว่าค้างไว้ที่ไหน → **ทำต่อทันที**
4. ไม่ต้องเริ่มใหม่!

---

## 💡 Tips

- **งานเล็ก** (ตอบคำถามสั้นๆ) → AI อาจไม่ต้องสร้าง Kanban
- **งานใหญ่** (เขียนโค้ด, แก้หลายไฟล์) → AI จะสร้าง Kanban ให้เอง
- ถ้าอยากให้ AI ทำตามแผนเป๊ะๆ → พิมพ์ `"ทำตามแผนที่วางไว้"`

---

*ระบบนี้ถูกออกแบบโดยคุณ (User) และ Implement โดย JudeCode*
