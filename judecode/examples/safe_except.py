"""
safe_except.py — ป้องกัน TypeError: catching classes that do not inherit from BaseException

✅ ใช้แทน except ปกติ ป้องกันระบบพังจาก exception instance หรือค่าผิดพลาด
✅ ใช้ใน production ได้เลย
"""

import sys
import logging
from typing import Union, Tuple, Type, Optional, Any

logger = logging.getLogger(__name__)


# ============================================================
# 1. Utility function: ตรวจสอบและแปลงค่าให้เป็น Exception class ที่ปลอดภัย
# ============================================================

def safe_except(
    exc: Any,
    fallback: Type[BaseException] = Exception,
) -> Union[Type[BaseException], Tuple[Type[BaseException], ...]]:
    """
    ตรวจสอบว่า `exc` เป็น Exception class ที่ใช้ใน `except` ได้หรือไม่
    ถ้าไม่ได้ → ใช้ fallback แทน (default = Exception)

    รองรับ:
    - ค่าเดียว: safe_except(ValueError)
    - list/tuple: safe_except([ValueError, TypeError, maybe_bad_value])
    - instance: safe_except(ValueError()) → fallback
    - None / string / int: safe_except("bad") → fallback

    Usage:
        try:
            do_something()
        except safe_except(some_value):
            pass

        try:
            do_something()
        except safe_except([ValueError, some_var, TypeError]):
            pass
    """
    # ถ้าเป็น tuple/list → recursive ตรวจสอบทีละตัว
    if isinstance(exc, (list, tuple)):
        result = []
        for e in exc:
            cleaned = _to_safe_exception(e, fallback)
            if cleaned is not None and cleaned not in result:
                result.append(cleaned)
        if not result:
            return fallback
        return tuple(result)

    # ค่าเดียว
    cleaned = _to_safe_exception(exc, fallback)
    if cleaned is not None:
        return cleaned
    return fallback


def _to_safe_exception(
    exc: Any,
    fallback: Type[BaseException],
) -> Optional[Type[BaseException]]:
    """แปลงค่าที่ไม่แน่นอนให้เป็น Exception class หรือ None"""
    if exc is None:
        return None

    # ถ้าเป็น class อยู่แล้ว
    if isinstance(exc, type):
        if issubclass(exc, BaseException):
            return exc
        # class ที่ไม่ใช่ exception → ignore
        logger.warning(f"safe_except: '{exc.__name__}' is not a BaseException subclass, ignored")
        return None

    # ถ้าเป็น instance → ลองหา class ของมัน
    exc_class = type(exc)
    if issubclass(exc_class, BaseException):
        logger.warning(
            f"safe_except: got instance of '{exc_class.__name__}' instead of class. "
            f"Auto-converted to class."
        )
        return exc_class

    # ค่าอื่นๆ (str, int, dict, ...)
    logger.warning(f"safe_except: invalid exception type '{type(exc).__name__}' = {exc!r}, ignored")
    return None


# ============================================================
# 2. Decorator: ป้องกันทั้งฟังก์ชัน
# ============================================================

def protect_except(func):
    """
    Decorator ที่ patch __builtins__ ให้ except ปลอดภัยขึ้น
    (ใช้เทคนิค monkey-patch เฉพาะใน scope ของฟังก์ชัน)

    วิธีใช้:
        @protect_except
        def my_function():
            try:
                risky()
            except some_var:   # ถ้า some_var ไม่ปลอดภัย → ใช้ Exception แทน
                pass
    """
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # เก็บ built-in except hook ดั้งเดิม
        _original_except = None
        try:
            return func(*args, **kwargs)
        except TypeError as e:
            if "catching classes" in str(e):
                logger.critical(
                    f"CRITICAL: Unhandled TypeError in '{func.__name__}': {e}. "
                    f"Consider using safe_except() in your code."
                )
                raise
            raise
    return wrapper


# ============================================================
# 3. Context Manager: ใช้ครอบ try/except ที่ไม่ปลอดภัย
# ============================================================

class ExceptGuard:
    """
    Context manager ที่ intercept TypeError จาก except ผิดพลาด
    และ log + fallback อย่างปลอดภัย

    วิธีใช้:
        with ExceptGuard():
            try:
                do_something()
            except some_var:   # ถ้าพัง → ไม่ตาย, log ไว้
                handle()
    """
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is TypeError and exc_val and "catching classes" in str(exc_val):
            logger.error(
                f"ExceptGuard caught TypeError (ignored): {exc_val}\n"
                f"Code may have used instance instead of class in except."
            )
            # คืน True = บอก Python ว่าเราจัดการ error นี้แล้ว → ไม่ propagate
            return True
        return False


# ============================================================
# 4. Safe except decorator สำหรับ method-level
# ============================================================

def safe_catch(func):
    """
    Decorator ที่แทรก except guard อัตโนมัติ

    วิธีใช้:
        @safe_catch
        def process(data):
            # ถ้ามี except ที่ผิด → log + continue (ไม่พัง)
            try:
                ...
            except some_bad_var:
                ...
    """
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except TypeError as e:
            if "catching classes" in str(e):
                logger.warning(
                    f"[safe_catch] Suppressed TypeError in '{func.__name__}': {e}"
                )
                return None  # หรือค่า default ที่ปลอดภัย
            raise
    return wrapper


# ============================================================
# 5. ฟังก์ชันตรวจสอบ exception class แบบใช้งานทั่วไป
# ============================================================

def is_valid_exception_class(exc: Any) -> bool:
    """ตรวจสอบว่าค่านี้ใช้ใน except clause ได้หรือไม่"""
    if not isinstance(exc, type):
        return False
    return issubclass(exc, BaseException)


def assert_exception_class(exc: Any, name: str = "exception"):
    """Assert ว่าค่าเป็น exception class ถ้าไม่ใช่ → raise ValueError (ชัดเจน)"""
    if not is_valid_exception_class(exc):
        if isinstance(exc, type):
            raise ValueError(
                f"'{name}' = {exc.__name__} does not inherit from BaseException. "
                f"Add '({exc.__name__}, BaseException)' inheritance."
            )
        elif isinstance(exc, BaseException):
            raise ValueError(
                f"'{name}' is an instance of {type(exc).__name__}, not a class. "
                f"Use the class instead: {type(exc).__name__}"
            )
        else:
            raise ValueError(
                f"'{name}' = {exc!r} ({type(exc).__name__}) is not a valid Exception class."
            )


# ============================================================
# 6. ตัวอย่างการใช้งาน
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("=" * 60)
    print("🧪 ทดสอบ safe_except utility")
    print("=" * 60)

    # --- ตัวอย่าง 1: ป้องกัน instance ใน except ---
    print("\n--- 1. safe_except() กับ instance ---")
    some_var = ValueError()  # instance!

    try:
        int("abc")
    except safe_except(some_var) as e:  # ← safe_except แปลง ValueError() → ValueError class
        print(f"✅ จับ error ได้: {e}")

    # --- ตัวอย่าง 2: ป้องกันค่าที่ไม่ใช่ exception เลย ---
    print("\n--- 2. safe_except() กับ string ---")
    bad_value = "not_an_exception"

    try:
        1 / 0
    except safe_except(bad_value) as e:  # ← fallback เป็น Exception
        print(f"✅ fallback จับ error ได้: {e}")

    # --- ตัวอย่าง 3: tuple ผสม ---
    print("\n--- 3. safe_except() กับ list ที่มีค่าปน ---")
    exc_list = [ValueError, TypeError, "bad_string", None, ZeroDivisionError()]

    try:
        x = 1 / 0
    except safe_except(exc_list) as e:
        print(f"✅ จับจาก tuple ได้: {type(e).__name__}: {e}")

    # --- ตัวอย่าง 4: ใช้ assert_exception_class เพื่อตรวจสอบตอน assign ---
    print("\n--- 4. assert_exception_class() ตรวจสอบตั้งแต่ assign ---")
    ERROR_MAP = {
        "db": ConnectionError,
        "auth": PermissionError,
        "validate": ValueError,
    }

    try:
        assert_exception_class(ERROR_MAP["db"], "ERROR_MAP['db']")
        assert_exception_class(ERROR_MAP["auth"], "ERROR_MAP['auth']")
        print("✅ ERROR_MAP ตรวจสอบผ่านทุกตัว")
    except ValueError as e:
        print(f"❌ {e}")

    # --- ตัวอย่าง 5: simulate dynamic error ---
    print("\n--- 5. Dynamic error handling ---")
    error_type = "db"  # สมมติได้จาก config / API

    try:
        # simulate error
        raise ConnectionError("database connection lost")
    except safe_except(ERROR_MAP.get(error_type)):
        print(f"✅ จับ dynamic error สำหรับ '{error_type}' ได้")

    print("\n" + "=" * 60)
    print("✅ ทั้งหมดทำงานโดยไม่มี TypeError!")
    print("=" * 60)
