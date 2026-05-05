---
title: "Workflow: Plan-First Execution Protocol"
created: 2026-04-28T22:02:51.594785
updated: 2026-04-28
tags: ['workflow', 'planning', 'protocol', 'execution', 'best-practice', 'kanban']
links: ['coding-rule-frequent-git-commits', 'system-preference-always-show-thinking-process', 'judecode-protocol-kanban-workflow', 'kanban-plan-template', 'session-context-active', 'checkpoint-resume-log']
---
# Workflow: Plan-First Execution Protocol

## Overview
ทุกครั้งที่ได้รับคำสั่ง ต้องวางแผนก่อนลงมือทำงานเสมอ ไม่กระโดดไปทำทันที  
**ระบบนี้ทำงานร่วมกับ Kanban Planning System**

---

## 🌐 Integration with Kanban System

ขั้นตอนทั้งหมดเชื่อมกับ 3 ระบบใน Vault:
- `kanban-plan-template` 📋 → วางแผน
- `session-context-active` 🧠 → กันลืม
- `checkpoint-resume-log` 🔄 → กันล่ม

---

## Steps

### Phase 0: Pre-Flight (เปิด Session)
1. เช็ค `checkpoint-resume-log` ว่ามี session ค้างไหม
2. เช็ค `session-context-active` ว่ามี context อะไรบ้าง
3. ถ้ามี session ค้าง → **Resume ทันที** (กระโดดไป Phase 3)
4. ถ้าเป็นงานใหม่ → ไป Phase 1

### Phase 1: Analysis (วิเคราะห์)
1. อ่านและทำความเข้าใจคำสั่งให้ชัดเจน
2. ตรวจสอบ Knowledge Vault ว่ามี context หรือประวัติที่เกี่ยวข้องไหม
3. ระบุเป้าหมายสุดท้ายที่ต้องการ

### Phase 2: Planning + Kanban (วางแผน)
1. **สร้าง Kanban Board** ใน Vault (ใช้ template)
2. แตกย่อยงานเป็นขั้นตอนย่อยๆ (Task Breakdown)
3. ระบุลำดับความสำคัญและ dependencies
4. **อัปเดต session-context-active**: เป้าหมายหลัก, คำขอเดิม
5. **บันทึก checkpoint แรก** ใน checkpoint-resume-log
6. เลือกเครื่องมือ/คำสั่งที่เหมาะสม
7. ประเมินความเสี่ยงหรือปัญหาที่อาจเกิด
8. เสนอแผนให้ผู้ใช้เห็นก่อน (ถ้างานซับซ้อน)

### Phase 3: Execution + Checkpoints (ลงมือ)
1. **Move Kanban Card**: 📋 To Do → ⚡ In Progress
2. **Save Checkpoint** ก่อนลงมือแต่ละขั้นตอน
3. ทำตามแผนทีละขั้นตอน
4. **อัปเดต session-context-active** หลังแต่ละขั้นตอน
5. Commit + Push ทุกครั้งที่มีการเปลี่ยนแปลง (ตามกฎเดิม)
6. รายงานผลหลังแต่ละขั้นตอน
7. **Move Kanban Card**: ⚡ In Progress → ✅ Done
8. ถ้าติดปัญหา → **บันทึก checkpoint** และปรึกษาผู้ใช้

### Phase 4: Verification + Close (ตรวจสอบ)
1. ตรวจสอบว่าผลลัพธ์ตรงตามแผน
2. อัปเดต Kanban Board → ทุกอย่าง Done
3. อัปเดต session-context-active → status = ✅ Complete
4. อัปเดต checkpoint-resume-log → สรุป session
5. Commit + Push ครั้งสุดท้าย
6. สรุปงานที่ทำเสร็จให้ผู้ใช้

---

## Format การนำเสนอแผน
```
📋 **แผนการทำงาน (Kanban):**
📥 Backlog: [รวมทั้งหมด]
📋 To Do: [รอบนี้]
⚡ In Progress: [กำลังทำ]
✅ Done: [เสร็จแล้ว]

⚠️ **ความเสี่ยงที่คาดการณ์:**
- [ปัญหาที่อาจเกิด]

❓ **ต้องการยืนยันแผนก่อนเริ่มไหมครับ?**
```

## Priority
HIGH - ใช้ทุกครั้งที่ได้รับคำสั่งงาน



## Update: Output Format Rule (เพิ่มเติม)

### ปัญหาที่พบ
Output ยาวเกินไปเมื่อรันคำสั่งที่มีผลลัพธ์มาก (เช่น git merge แสดงไฟล์ 35 ไฟล์) ทำให้ดูรกและ "พ่น" ออกมาไม่เป็นระเบียบ

### กฎใหม่: Clean Output
1. **สรุปผลลัพธ์** แทนการแสดงทุกบรรทัด
2. **ถ้าผลลัพธ์ยาว > 10 บรรทัด** → แสดงเฉพาะ:
   - สถานะสำเร็จ/ล้มเหลว
   - จำนวนไฟล์ที่เปลี่ยนแปลง (โดยสรุป)
   - ข้อความสำคัญที่ต้องรู้
3. **เก็บ log เต็ม** ไว้ในไฟล์ `.log` ถ้าต้องการดูย้อนหลัง
4. ใช้ตารางสรุปแทนการแสดงรายการยาวๆ

### ตัวอย่างที่ถูกต้อง
```
✅ Push สำเร็จ (merge 35 ไฟล์จาก remote)
Commit: 139db4e | Branch: main -> origin/main
```
แทนที่จะแสดงรายชื่อไฟล์ทั้งหมด
