---
title: "Bug Report: Terminal Input Hanging in JudeCode App"
created: 2026-04-28T22:11:48.603332
tags: ['bug', 'terminal', 'input', 'judecode', 'thai-ime', 'workaround']
links: ['workflow-plan-first-execution-protocol']
---
# Bug Report: Terminal Input Hanging

## Problem
พิมพ์ข้อความยาวๆ ใน terminal ผ่าน judecode app แล้วเกิดอาการค้าง ไม่สามารถพิมพ์ต่อได้

## Environment
- Terminal: Apple Terminal (xterm-256color)
- Shell: zsh 5.9 (arm64-apple-darwin25.0)
- Columns: 241 (เพียงพอ)
- **App: Python judecode (PID 16154)** ← ตัวการ!

## Root Cause Analysis
Terminal และ Shell ไม่มีปัญหา แต่ **judecode Python app** ที่เป็น wrapper รอบ terminal มีปัญหากับ:
1. **Input buffer limit** — Python `input()` หรือ TUI library จำกัดความยาว
2. **Thai IME + TUI conflict** — escape sequences ชนกันระหว่างภาษาไทยกับ app
3. **Line wrapping calculation** — UI อาจคำนวณ cursor position ผิดพลาด

## Workarounds
| วิธี | รายละเอียด |
|------|------------|
| แบ่งข้อความ | พิมพ์สั้นๆ แล้วกด Enter หลายครั้ง |
| ใช้ Editor | แต่งใน VS Code/Editor แล้ว paste |
| Ctrl+C | ถ้าค้าง ให้ยกเลิกแล้วเริ่มใหม่ |

## Status
- Priority: MEDIUM
- รอแก้ไขใน judecode core
- บันทึกไว้เพื่อตรวจสอบ input handler ในอนาคต
