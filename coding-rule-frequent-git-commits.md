---
title: "Coding Rule: Frequent Git Commits"
created: 2026-04-28T21:44:05.929723
tags: ['coding-rule', 'git', 'workflow', 'backup', 'best-practice']
links: ['git-workflow', 'error-handling', 'development-standards']
---
# Coding Rule: Frequent Git Commits + Push

## Rule (Updated)
ทุกครั้งที่เขียนโค้ด หรือปรับปรุงอะไรก็ตาม ต้อง **commit และ push** ทันที ไม่ปล่อยให้โค้ดค้างโดยไม่มี checkpoint บน remote

## เหตุผล
- ถ้าเขียนผิดหรือเกิด error ขึ้นมา จะสามารถ rollback กลับไปยัง commit ก่อนหน้าได้ทันที
- ป้องกันความยุ่งยากในการกู้คืนงาน
- มีประวัติการเปลี่ยนแปลงที่ติดตามได้บน remote repository
- สำรองข้อมูลบน cloud ไม่ให้หายถ้าเครื่องมีปัญหา

## ขั้นตอนที่ต้องทำ
1. หลังจากแก้ไขโค้ดสำคัญ หรือเพิ่มฟีเจอร์ใหม่
2. รันคำสั่ง:
   ```bash
   git add . && git commit -m "feat: <คำอธิบายสั้นๆ>" && git push
   ```
3. ถ้าเป็นการแก้ไขเล็กน้อย ให้ commit + push ทันทีหลังแก้ไขเสร็จ
4. ใช้ message ที่อธิบายได้ชัดเจน:
   - `feat:` สำหรับฟีเจอร์ใหม่
   - `fix:` สำหรับการแก้ไขบั๊ก
   - `refactor:` สำหรับปรับปรุงโครงสร้างโค้ดโดยไม่เปลี่ยนพฤติกรรม
   - `docs:` สำหรับเอกสารหรือคอมเมนต์
   - `chore:` สำหรับงานทั่วไปที่ไม่ใช่โค้ดหลัก

## Priority
CRITICAL - ทำทุกครั้งที่เขียนโค้ด ไม่เว้นแม้แต่คำถามง่ายๆ
