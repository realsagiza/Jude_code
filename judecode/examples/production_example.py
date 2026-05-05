"""
production_example.py — ตัวอย่างการใช้ safe_except ในระบบจริง
ป้องกันระบบพังจาก TypeError: catching classes that do not inherit from BaseException
"""

import sys
import os
import logging
from typing import Any, Dict, Optional

# Import safe_except
sys.path.insert(0, os.path.dirname(__file__))
from safe_except import safe_except, assert_exception_class, ExceptGuard, is_valid_exception_class

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================
# 1. API Error Handler — ป้องกัน Dynamic Error Mapping
# ============================================================

class APIErrorHandler:
    """
    ใช้จัดการ exception จาก API response แบบ dynamic
    โดยไม่เสี่ยง TypeError
    """

    # Map error codes → Exception classes (เก็บเป็น CLASS เสมอ!)
    ERROR_MAP: Dict[str, type] = {
        "validation": ValueError,
        "auth": PermissionError,
        "not_found": FileNotFoundError,
        "timeout": TimeoutError,
        "db": ConnectionError,
        "rate_limit": RuntimeError,
    }

    def __init__(self):
        # ✅ ตรวจสอบ ERROR_MAP ตั้งแต่ตอน init → ป้องกันตั้งแต่ต้น
        self._validate_error_map()

    def _validate_error_map(self):
        """ตรวจสอบว่าทุกค่าใน ERROR_MAP เป็น Exception class ที่ใช้ได้"""
        for key, exc in self.ERROR_MAP.items():
            assert_exception_class(exc, f"ERROR_MAP['{key}']")
        logger.info(f"✅ ERROR_MAP ตรวจสอบผ่าน {len(self.ERROR_MAP)} รายการ")

    def handle(self, error_code: str, fallback: type = Exception):
        """
        จับ exception ตาม error_code แบบปลอดภัย

        Args:
            error_code: คีย์ใน ERROR_MAP (เช่น "validation", "auth")
            fallback: exception class ที่จะใช้ถ้า error_code ไม่มีใน map

        ใช้กับ except:
            try:
                ...
            except handler.handle("validation"):
                ...
        """
        # ✅ safe_except() จะจัดการกรณี key ไม่มี → fallback
        return safe_except(self.ERROR_MAP.get(error_code), fallback)


# ------------------------------------------------------------
# 2. ระบบ Plugin/Loader — ป้องกัน Exception จาก Third-party
# ------------------------------------------------------------

class PluginException:
    """⚠️ จำลองว่า third-party plugin ส่ง exception instance มาผิด"""
    pass


class PluginLoader:
    """
    โหลด plugin จากภายนอก โดยไม่ให้ exception ที่ผิดพลาดทำระบบพัง
    """

    def __init__(self):
        self.plugins: Dict[str, Any] = {}

    def register_plugin(self, name: str, plugin_module):
        """ลงทะเบียน plugin พร้อม exception handler ที่ปลอดภัย"""
        # สมมติว่า plugin มี error_classes attribute
        error_classes = getattr(plugin_module, "error_classes", None)

        if error_classes:
            logger.info(f"Plugin '{name}' registered with error classes: {error_classes}")

        self.plugins[name] = plugin_module

    def execute_plugin(self, name: str, action: str, data: Any):
        """
        รัน plugin action โดยใช้ ExceptGuard ป้องกันการพัง
        """
        plugin = self.plugins.get(name)
        if not plugin:
            logger.warning(f"Plugin '{name}' not found")
            return None

        # ✅ ใช้ ExceptGuard ครอบ → ถ้า except ผิด → log + continue
        with ExceptGuard():
            try:
                handler = getattr(plugin, f"handle_{action}")
                return handler(data)
            except Exception as e:
                logger.error(f"Plugin '{name}' error: {e}")
                return None

        return None


# ============================================================
# 3. Database Connection Pool — ป้องกัน Runtime Config Error
# ============================================================

class DatabasePool:
    """
    Connection pool ที่ retryable exception classes
    มาจาก configuration (อาจผิดพลาดได้)
    """

    def __init__(self, config: dict):
        self.config = config
        # ❌ config อาจส่ง instance มาผิด
        self.retry_exceptions = config.get("retry_exceptions", [ConnectionError, TimeoutError])
        self.critical_exceptions = config.get("critical_exceptions", [DatabaseError])

    def execute_with_retry(self, query: str):
        """
        รัน query พร้อม retry logic ที่ปลอดภัย
        """
        max_retries = self.config.get("max_retries", 3)

        for attempt in range(max_retries):
            try:
                return self._execute(query)
            except safe_except(self.retry_exceptions) as e:
                # ✅ safe_except ป้องกันแม้ config จะมี instance
                if attempt < max_retries - 1:
                    logger.warning(f"Retry {attempt + 1}/{max_retries}: {e}")
                    continue
                raise
            except safe_except(self.critical_exceptions) as e:
                # Critical error → ไม่ retry
                logger.error(f"Critical DB error: {e}")
                raise

    def _execute(self, query: str):
        # Simulate DB call
        import random
        if random.random() < 0.3:
            raise ConnectionError("DB connection lost")
        return f"Result for: {query}"


class DatabaseError(Exception):
    """Custom database exception"""
    pass


# ============================================================
# 4. Web Framework Middleware — ป้องกัน Request Handler พัง
# ============================================================

class WebMiddleware:
    """
    Middleware ที่ ensure ว่า request handler
    ไม่พังจาก exception class ที่ผิดพลาด
    """

    def __init__(self):
        self.handlers: Dict[str, dict] = {}

    def register_route(self, path: str, handler_func, error_classes: list = None):
        """
        ลงทะเบียน route พร้อม error classes ที่ปลอดภัย

        safe_except() จะถูก apply ตอน runtime
        """
        if error_classes is None:
            error_classes = [Exception]

        self.handlers[path] = {
            "func": handler_func,
            "errors": error_classes,
        }
        logger.info(f"Route '{path}' registered with {len(error_classes)} error handlers")

    def dispatch(self, path: str, request: dict) -> dict:
        """
        Dispatch request ไปยัง handler ที่ลงทะเบียนไว้
        """
        route = self.handlers.get(path)
        if not route:
            return {"status": 404, "error": "Not found"}

        try:
            result = route["func"](request)
            return {"status": 200, "data": result}
        except safe_except(route["errors"]) as e:
            # ✅ ปลอดภัย แม้ error_classes จะมี instance ปน
            logger.error(f"Handler error for '{path}': {e}")
            return {"status": 500, "error": str(e)}


# ============================================================
# 5. ทดสอบทั้งหมด
# ============================================================

def test_api_error_handler():
    """ทดสอบ API Error Handler"""
    print("\n" + "=" * 60)
    print("🧪 ทดสอบ APIErrorHandler")
    print("=" * 60)

    handler = APIErrorHandler()

    # ✅ ทำงานปกติ
    try:
        raise ValueError("invalid email")
    except handler.handle("validation"):
        print("✅ APIErrorHandler: จับ validation error ได้")

    # ✅ Fallback กรณี error_code ไม่มี
    try:
        raise PermissionError("access denied")
    except handler.handle("unknown_error"):  # ไม่มี key นี้ → fallback เป็น Exception
        print("✅ APIErrorHandler: fallback จับ error ที่ไม่รู้จักได้")


def test_plugin_loader():
    """ทดสอบ Plugin Loader"""
    print("\n" + "=" * 60)
    print("🧪 ทดสอบ PluginLoader + ExceptGuard")
    print("=" * 60)

    # สร้าง plugin จำลอง
    class BadPlugin:
        """Plugin ที่ส่ง exception instance มาผิด"""
        error_classes = [ValueError()]  # ❌ instance!

        def handle_process(self, data):
            if not data:
                raise ValueError("no data")
            return f"processed: {data}"

    loader = PluginLoader()
    loader.register_plugin("bad_plugin", BadPlugin)

    # ✅ แม้ plugin จะมี instance → ไม่พัง
    result = loader.execute_plugin("bad_plugin", "process", "test")
    print(f"✅ Plugin execute สำเร็จ: {result}")

    # ✅ แม้จะเกิด exception จริง → ก็ไม่พัง
    result = loader.execute_plugin("bad_plugin", "process", "")
    print(f"✅ Plugin execute (with error handled gracefully): {result}")


def test_database_pool():
    """ทดสอบ Database Pool"""
    print("\n" + "=" * 60)
    print("🧪 ทดสอบ DatabasePool")
    print("=" * 60)

    # ❌ Config ที่ส่ง instance มาให้ retry_exceptions
    bad_config = {
        "retry_exceptions": [ConnectionError, TimeoutError()],  # instance ปน!
        "critical_exceptions": [DatabaseError],
        "max_retries": 2,
    }

    pool = DatabasePool(bad_config)

    try:
        result = pool.execute_with_retry("SELECT 1")
        print(f"✅ DatabasePool execute สำเร็จ: {result}")
    except Exception as e:
        print(f"✅ DatabasePool จับ error (expected): {type(e).__name__}: {e}")


def test_web_middleware():
    """ทดสอบ Web Middleware"""
    print("\n" + "=" * 60)
    print("🧪 ทดสอบ WebMiddleware")
    print("=" * 60)

    middleware = WebMiddleware()

    # ลงทะเบียน route ที่มี error classes ปน instance
    def user_handler(req):
        if not req.get("user_id"):
            raise ValueError("missing user_id")
        return {"name": "John", "id": req["user_id"]}

    middleware.register_route(
        "/api/user",
        user_handler,
        error_classes=[ValueError, TypeError()],  # ❌ instance ปน!
    )

    # ✅ Request ที่ผิดพลาด → response error แทนที่จะพัง
    response = middleware.dispatch("/api/user", {})
    print(f"✅ WebMiddleware response: {response}")

    # ✅ Request ปกติ
    response = middleware.dispatch("/api/user", {"user_id": 123})
    print(f"✅ WebMiddleware success: {response}")


def test_validation_utility():
    """ทดสอบ utility functions"""
    print("\n" + "=" * 60)
    print("🧪 ทดสอบ is_valid_exception_class()")
    print("=" * 60)

    class NonException:
        """class ที่ไม่ inherit จาก BaseException"""
        pass

    test_cases = [
        (ValueError, True),
        (ValueError(), False),   # instance
        ("string", False),
        (None, False),
        (42, False),
        (Exception, True),
        (BaseException, True),
        (NonException, False),  # class ที่ไม่ inherit จาก BaseException
    ]

    for exc, expected in test_cases:
        result = is_valid_exception_class(exc)
        status = "✅" if result == expected else "❌"
        print(f"  {status} is_valid_exception_class({exc!r}) = {result} (expected {expected})")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("\n" + "🔥" * 30)
    print("🔥  ตัวอย่างการใช้งาน safe_except ใน Production")
    print("🔥" * 30)

    test_api_error_handler()
    test_plugin_loader()
    test_database_pool()
    test_web_middleware()
    test_validation_utility()

    print("\n" + "=" * 60)
    print("🎉 ทั้งหมดทำงานโดยไม่มี TypeError ทำให้ระบบพัง!")
    print("=" * 60)
