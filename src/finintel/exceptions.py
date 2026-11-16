"""Cây ngoại lệ dùng chung cho các tầng của finintel."""


class FinIntelError(Exception):
    """Lớp cơ sở cho mọi ngoại lệ trong hệ thống."""


class TransientSourceError(FinIntelError):
    """Lỗi mạng tạm thời như timeout, mất kết nối hoặc HTTP 500/502/503/504."""


class RateLimitOrCircuitOpenError(FinIntelError):
    """Nguồn trả HTTP 429 hoặc mở circuit breaker. Dừng hoặc chờ trước khi gọi lại."""


class SourceEntitlementError(FinIntelError):
    """Khóa thiếu, hết hạn hoặc thiếu quyền truy cập (HTTP 401/403). Dừng ngay."""


class ContractViolationError(FinIntelError):
    """Dữ liệu vi phạm schema bắt buộc hoặc hợp đồng giữa các tầng. Dừng ngay."""


class ArchiveIntegrityError(ContractViolationError):
    """A raw archive payload no longer matches its immutable manifest entry."""


class CriticalDataQualityError(FinIntelError):
    """Sai đẳng thức kế toán nghiêm trọng hoặc tỷ lệ bản ghi lỗi vượt ngưỡng batch."""
