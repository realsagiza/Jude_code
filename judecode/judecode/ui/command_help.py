"""Shared Thai command guide, filtered by the commands each UI implements."""
from rich.markup import escape

MODES = {'tui': 'หน้าจอหลัก (TUI)', 'classic': 'Classic (--classic)', 'legacy': 'Legacy (--legacy)'}
ALL = ('tui', 'classic', 'legacy')
# category, usage, description, example, modes, aliases
COMMANDS = [
    ('Sandbox-first', 'deploy แผนนี้', 'อนุมัติแผน deploy ล่าสุดครั้งเดียว (หรือ DEPLOY <token>); ปกติแก้โค้ด/ทดสอบใน sandbox เสมอ', 'deploy แผนนี้', ALL, ()),
    ("ตารางงานอัตโนมัติ", "/schedule [status|list|history|pause id|resume id|cancel id]", "ดูตาราง/ผลลัพธ์และควบคุมงานปลุกตัวเอง; สร้างงานโดยบอก AI ด้วยภาษาปกติ", "/schedule", ALL, ()),
    ('เสียงภาษาไทย', '/listen [วินาที]', 'อัดเสียง 1–60 วินาที (ค่าเริ่มต้น 8) แล้วถอดเสียงผ่าน OpenRouter ใส่ช่องพิมพ์ให้ตรวจและกด Enter; Esc ยกเลิก', '/listen 8', ('tui',), ()),
    ('เสียงภาษาไทย', '/voice on|off|read|stop|status', 'on เปิดสนทนาต่อเนื่อง พูดจบส่งอัตโนมัติและพูดตอบ; read อ่านคำตอบอย่างเดียว; พูด หยุดงาน หรือ ปิดโหมดเสียง ได้; ค่าเสียงนอก /budget', '/voice on', ('tui',), ()),
    ('เริ่มต้น', '/help', 'ดูคำสั่งในโหมดปัจจุบัน; /help all ดูทุกโหมด; /help คำค้น ค้นชื่อหรือคำอธิบาย', '/help resume', ALL, ()),
    ('เริ่มต้น', '/model', 'ดู provider, โมเดล และ API ที่ตั้งค่าไว้ (ไม่ได้เปลี่ยนโมเดล)', '/model', ALL, ()),
    ('เริ่มต้น', '/quit', 'ออกจากโปรแกรม; Classic ขณะทำงานต้องสั่งซ้ำเพื่อยืนยัน', '/quit', ALL, ('/exit', ':q')),
    ('ควบคุมงาน', '/stop', 'ขอหยุดงาน AI; การกระทำที่เริ่มแล้วอาจต้องรอให้จบก่อน', '/stop', ALL, ('/pause',)),
    ('ควบคุมงาน', '/continue', 'ให้ AI ทำงานเดิมต่อ; อาจเรียกเครื่องมือและแก้ไฟล์ต่อ', '/continue', ALL, ()),
    ('ควบคุมงาน', '/status', 'ดูจำนวนครั้งที่ทำต่อและสถานะการใช้งาน; Legacy แสดงสถานะ autonomous เพิ่มด้วย', '/status', ALL, ()),
    ('ควบคุมงาน', '/budget', 'ดูประมาณการ token และค่าใช้จ่าย ไม่ใช่ยอดเรียกเก็บจริงหรือเพดานบังคับ', '/budget', ALL, ()),
    ('ควบคุมงาน', '/queue', 'ดูข้อความที่รอ AI ทำงานต่อ', '/queue', ('tui', 'classic'), ()),
    ('บทสนทนา', '/sessions', 'แสดงบทสนทนาที่บันทึกไว้ของโฟลเดอร์โปรเจคปัจจุบัน', '/sessions', ('tui', 'legacy'), ()),
    ('บทสนทนา', '/resume ID', 'กู้บทสนทนาเป็น session ใหม่โดยยังไม่รันงาน; ใช้ latest แทน ID ได้; /resume เปล่าแสดงรายการ', '/resume latest', ('tui', 'legacy'), ()),
    ('บทสนทนา', '/clear', 'เริ่มบทสนทนาใหม่; TUI/Legacy เก็บประวัติเดิมไว้ ส่วน Classic ล้างบริบทในหน่วยความจำ', '/clear', ALL, ()),
    ('บทสนทนา', '/copy', 'คัดลอกข้อความทั้งหมดจากช่อง Output', '/copy', ('tui',), ()),
    ('บทสนทนา', '/compact', 'ย่อประวัติเก่าด้วยข้อความตัดสั้น รายละเอียดบางส่วนอาจหาย (ไม่ใช่การสรุปครบถ้วน)', '/compact', ('legacy',), ()),
    ('ตรวจและกู้ไฟล์', '/checkpoint', 'ดูรายการจุดบันทึกไฟล์ของ session ปัจจุบัน', '/checkpoint', ('legacy',), ()),
    ('ตรวจและกู้ไฟล์', '/diff [step หรือ path]', 'ดูความต่างของไฟล์กับ checkpoint; ไม่ใส่ค่าเพื่อดูภาพรวม', '/diff 3', ('legacy',), ()),
    ('ตรวจและกู้ไฟล์', '/rollback [step]', 'ย้อนการเปลี่ยนแปลงไฟล์ตาม checkpoint; เปลี่ยนไฟล์จริง ควรดู /diff ก่อน', '/rollback 3', ('legacy',), ()),
    ('ความจำ', '/decisions', 'ดูบันทึกการตัดสินใจและรายการล่าสุด', '/decisions', ('legacy',), ()),
    ('ความจำ', '/decisions search คำค้น', 'ค้นบันทึกการตัดสินใจ', '/decisions search error', ('legacy',), ()),
    ('ความจำ', '/memory', 'ดูวิธีเรียกข้อมูลความจำข้าม session', '/memory', ('legacy',), ()),
    ('ความจำ', '/memory patterns', 'ดูรูปแบบการทำงานที่สำเร็จและล้มเหลว', '/memory patterns', ('legacy',), ()),
    ('ความจำ', '/memory sessions', 'ดูสรุปงานใน session เก่า; ใช้ /sessions เมื่อต้องการกู้บทสนทนา', '/memory sessions', ('legacy',), ()),
    ('Sandbox และสิทธิ์', '/sandbox', 'สลับโหมดทดลองแก้ไฟล์ โดยเก็บการเปลี่ยนแปลงรอ apply; ไม่รองรับทุกเครื่องมือ', '/sandbox', ('legacy',), ()),
    ('Sandbox และสิทธิ์', '/sandbox apply', 'นำการเปลี่ยนแปลงที่พักไว้ไปเขียนไฟล์จริง', '/sandbox apply', ('legacy',), ()),
    ('Sandbox และสิทธิ์', '/sandbox discard', 'ทิ้งการเปลี่ยนแปลงที่ยังไม่ได้ apply', '/sandbox discard', ('legacy',), ()),
    ('Sandbox และสิทธิ์', '/permissions', 'ดูระดับสิทธิ์ที่ตั้งไว้', '/permissions', ('legacy',), ()),
    ('Sandbox และสิทธิ์', '/permissions set หมวด=ระดับ', 'ตั้งสิทธิ์ auto / ask / deny; เปลี่ยนนโยบายการเรียกเครื่องมือ', '/permissions set delete=ask', ('legacy',), ()),
    ('งานเบื้องหลัง', '/daemon', 'ดูสถานะงานเบื้องหลังและตำแหน่ง log', '/daemon', ('legacy',), ()),
    ('งานเบื้องหลัง', '/daemon start เป้าหมาย', 'เริ่ม agent เบื้องหลังตามเป้าหมาย อาจเรียก API และแก้ไฟล์', '/daemon start ตรวจสอบผลทดสอบ', ('legacy',), ()),
    ('งานเบื้องหลัง', '/daemon stop', 'หยุด daemon', '/daemon stop', ('legacy',), ()),
    ('งานเบื้องหลัง', '/daemon logs', 'ดู log ของ daemon ล่าสุด 30 บรรทัด', '/daemon logs', ('legacy',), ()),
    ('งานเบื้องหลัง', '/notify', 'ทดสอบระบบแจ้งเตือน', '/notify', ('legacy',), ()),
]


def is_help_command(text):
    parts = text.strip().split(maxsplit=1)
    return bool(parts) and parts[0].lower() in ('/help', '/commands', '/?')


def render_help(mode, command='/help'):
    parts = command.strip().split(maxsplit=1)
    query = parts[1].strip().lower() if len(parts) > 1 else ''
    rows = [row for row in COMMANDS if (not query and mode in row[4]) or query == 'all'
            or (query and query.lstrip('/') in ' '.join((row[1], row[2], *row[5])).lower())]
    lines = [f'\nJudeCode — คู่มือคำสั่ง | {MODES[mode]}',
             'พิมพ์คำสั่งในช่องข้อความแล้ว Enter; ข้อความทั่วไปใช้สั่ง AI ได้ตามปกติ',
             '/help = โหมดนี้ • /help all = ทุกโหมด • /help resume = ค้นคำสั่ง',
             'ชื่ออื่นของ help: /commands, /? • วงเล็บเหลี่ยมหมายถึงค่าที่ไม่จำเป็นต้องใส่']
    if query and query != 'all':
        lines.append('ค้นหา: ' + query)
    category = None
    for group, usage, description, example, modes, aliases in rows:
        if group != category:
            lines.append('\n' + group)
            category = group
        availability = '' if mode in modes else ' [ใช้ได้เฉพาะ ' + ', '.join(MODES[m] for m in modes) + ']'
        lines.append('  ' + usage + availability)
        lines.append('    ' + description)
        if aliases:
            lines.append('    ชื่ออื่น: ' + ', '.join(aliases))
        if example != usage:
            lines.append('    ตัวอย่าง: ' + example)
    if not rows:
        lines.append('\nไม่พบคำสั่ง ลอง /help all หรือ /help memory')
    if not query or query == 'all':
        lines.extend(['\nปุ่มลัด'])
        if mode == 'tui':
            lines.extend(['  Esc = ขอหยุดงาน • Ctrl+Q = ออก • Ctrl+L = เริ่มบทสนทนาใหม่',
                          '  ลากเลือกข้อความแล้ว Ctrl+C = คัดลอกส่วนที่เลือก',
                          '  Ctrl+Shift+C = คัดลอกส่วนที่เลือก • Ctrl+Y = คัดลอก Output ทั้งหมด',
                          '  Ctrl+C เมื่อไม่มีข้อความเลือก = ขอหยุดงาน หรือออกเมื่อว่าง'])
        else:
            lines.append('  Ctrl+C = ขอหยุดงาน / ออกเมื่อว่าง')
        lines.extend(['\nโหมดอื่น: ออกจากโปรแกรมก่อน แล้วเปิดด้วย judecode --legacy หรือ judecode --classic',
                      'คำสั่งที่ระบุว่าใช้ได้เฉพาะโหมดอื่นจะยังใช้ในหน้าจอนี้ไม่ได้',
                      'เครื่องมือของ AI เช่น read, write, shell, task_add ไม่ใช่ slash command; สั่งงานด้วยภาษาปกติได้'])
    return escape('\n'.join(lines) + '\n')
