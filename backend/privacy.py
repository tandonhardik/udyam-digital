"""Masking helpers + a logger that masks identifiers automatically (DPDP data minimisation)."""
import logging
import re

_GSTIN = re.compile(r"\b(\d{2})[A-Z0-9]{9}([A-Z0-9]{4})\b")
_UDYAM = re.compile(r"\bUDYAM-[A-Z]{2}-\d{2}-(\d{7})\b", re.I)
_PHONE = re.compile(r"\b(\d{2})\d{6}(\d{2})\b")


def mask_id(value: str | None) -> str:
    if not value:
        return ""
    v = str(value)
    return "*" * max(len(v) - 4, 0) + v[-4:]


def mask_text(text: str) -> str:
    text = _GSTIN.sub(lambda m: "*" * 11 + m.group(2), text)
    text = _UDYAM.sub(lambda m: "UDYAM-**-**-***" + m.group(1)[-4:], text)
    return _PHONE.sub(lambda m: m.group(1) + "******" + m.group(2), text)


class MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_text(str(record.getMessage()))
        record.args = ()
        return True


def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        h.addFilter(MaskingFilter())
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log
