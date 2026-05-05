---
title: "JudeCode Protocol: Kanban-First Workflow"
created: 2026-04-28
tags: ['protocol', 'kanban', 'workflow', 'judecode-core']
links: ['session-context-active', 'checkpoint-resume-log', 'kanban-plan-template']
---

# JudeCode Protocol: Kanban-First Workflow

## 🎯 หลักการสำคัญ
**"Think → Plan → Do → Log → Resume"**

ทุกครั้งที่ได้รับคำสั่งงาน ให้ทำตาม protocol นี้เสมอ!

---

## 🔷 Phase 0: Pre-Flight Check (1-2 วินาที)

เมื่อได้รับข้อความจาก User:

1. **อ่าน Vault Notes** ที่เกี่ยวข้อง (`vault_search`)
2. **เช็ค checkpoint-resume-log** ว่ามี session ค้างอยู่ไหม
3. **เช็ค session-context-active** ว่ามี context อะไรบ้าง
4. **ระบุประเภทคำขอ**:
   - 🆕 `New Task` → สร้าง Kanban ใหม่
   - 🔄 `Continue` → Resume จาก checkpoint
   - ❓ `Question` → ตอบเลย (ไม่ต้องสร้าง Kanban ถ้าไม่ซับซ้อน)
   - 🐛 `Bug Fix` → Bug Kanban

---

## 🔷 Phase 1: Create Kanban Plan (สำหรับ New Task)

ก่อนทำอะไร ให้สร้าง Kanban ใน Vault ทันที:

### Format
```markdown
# Kanban: [ชื่อโปรเจกต์/งาน]

## 📥 Backlog
## 📋 To Do
## ⚡ In Progress
## ✅ Done
## 🔍 Notes
```

### ขั้นตอน
1. `vault_create_note` → สร้าง Kanban Board
2. แตก Task ย่อย (Break down)
3. ระบุ Priority / Dependencies / Risks
4. อัปเดต `session-context-active` ด้วยเป้าหมายหลัก
5. อัปเดต `checkpoint-resume-log` ด้วย checkpoint แรก

---

## 🔷 Phase 2: Execute with Checkpoints

ขณะทำงาน ให้ทำวน loop นี้:

```
while มี Task ที่ต้องทำ:
    1. เลือก Task ถัดไป
    2. อัปเดต Kanban: To Do → In Progress
    3. อัปเดต checkpoint-resume-log (before action)
    4. ลงมือทำ (execute tool)
    5. ถ้าสำเร็จ → อัปเดต Kanban: In Progress → Done
    6. ถ้าพัง → อัปเดต checkpoint ให้ resume ได้
    7. อัปเดต session-context-active (progress summary)
    8. Git commit (ตามกฎเดิม)
```

### 📍 ทุกครั้งที่ทำ Action สำเร็จ ให้ UPDATE:
1. `checkpoint-resume-log` → จุด Resume ล่าสุด
2. `session-context-active` → Progress, Files, Decisions
3. Kanban Board → Move card

---

## 🔷 Phase 3: Session End (Complete / Pause / Error)

### ✅ เมื่อทำงานเสร็จ:
1. อัปเดต Kanban → ทุกอย่าง Done
2. อัปเดต session-context-active → status = ✅ Complete
3. อัปเดต checkpoint-resume-log → สรุป session
4. Git add → commit → push
5. สรุปผลให้ User ทราบ

### ⏸ เมื่อโดน Pause / Interrupt:
1. บันทึก checkpoint (ตรงไหน, กำลังทำอะไร)
2. อัปเดต session-context-active → status = 🟡 Paused
3. แจ้ง User ว่าค้างไว้ที่ตรงไหน

### 🔴 เมื่อ Error / Token Limit / Crash:
1. เมื่อได้รับ continuation nudge → อ่าน checkpoint-resume-log
2. อ่าน session-context-active
3. ดูว่าค้างไว้ที่ไหน → resume ต่อ
4. **ห้ามเริ่มใหม่!** ใช้ edit/append ต่อจากที่ค้าง

---

## 🔷 Anti-Forget System (กัน AI ลืม)

### Session Context File เก็บอะไรบ้าง:
- 🎯 **Original Request** (คำขอเดิมของ User)
- 📝 **Progress Summary** (ทำอะไรไปแล้วบ้าง)
- 📂 **Files Modified** (ไฟล์ที่แก้ไข)
- 💡 **Key Decisions** (ข้อตกลงสำคัญ)
- 📋 **Remaining Tasks** (สิ่งที่เหลือ)
- 🔄 **Next Action** (ต้องทำอะไรต่อ)

### เมื่อรู้สึกว่า Context ยาวเกินไป:
- สร้าง Note สรุปสั้นๆ (`vault_create_note`)
- Reference ด้วย [[Wiki Link]]

---

## 🔷 Kanban Board Rules

### Card States:
```
📥 Backlog     → ไอเดีย/สิ่งที่ต้องทำทั้งหมด
📋 To Do       → เลือกมาทำรอบนี้
⚡ In Progress  → กำลังทำ
✅ Done         → เสร็จแล้ว
❌ Cancelled    → ยกเลิก (ไม่ต้องทำ)
🔄 Blocked     → ติดปัญหารออย่างอื่น
```

### Priority Labels:
- 🔴 P0 - Critical (ต้องทำเดี๋ยวนี้)
- 🟠 P1 - High (สำคัญ)
- 🟡 P2 - Medium (ทำต่อจาก P1)
- 🟢 P3 - Low (ถ้ามีเวลา)

---

## 🔷 Git Workflow Integration

```bash
# ทุกครั้งที่ Kanban Card เปลี่ยนจาก In Progress → Done
git add .
git commit -m "🎯 [Kanban] Task: [name] - ✅ Done"
git push
```

---

## 🔷 Summary Checklist

ทุกครั้งก่อนจบ response:

- [ ] Kanban Board อัปเดตแล้ว?
- [ ] session-context-active อัปเดตแล้ว?
- [ ] checkpoint-resume-log อัปเดตแล้ว?
- [ ] ถ้ามี code change → commit + push?
- [ ] สรุปผลให้ User รู้?
