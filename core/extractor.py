"""压缩包处理(.zip / .rar / .7z / .exe 自解压)。

对外只暴露一个 ``extract(archive_path, dest_dir, password_provider=None)``。

设计:
- 后端按后缀分派:
    .zip / .exe → zipfile(stdlib;.exe 自解压实际多为 zip)
    .rar        → rarfile(可选)
    .7z         → py7zr(可选)
- 加密包通过 ``password_provider(archive_path, attempt) -> str | None`` 回调获取密码
  - 返回 None / 空 → 视为用户取消,跳过
  - spec 5.2: 默认重试 3 次,记住密码逻辑由调用方在 provider 内部实现
- 损坏 / 后端缺失 / 后端异常都吞掉,失败信息写入 ``ExtractResult``
"""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


# 可选依赖,缺失也能跑(仅相关后缀无法处理)
try:
    import rarfile  # type: ignore
    _HAS_RAR = True
except ImportError:
    rarfile = None  # type: ignore
    _HAS_RAR = False

try:
    import py7zr  # type: ignore
    _HAS_7Z = True
except ImportError:
    py7zr = None  # type: ignore
    _HAS_7Z = False


PasswordProvider = Callable[[Path, int], "str | None"]
DEFAULT_PASSWORD_RETRIES = 3


@dataclass
class ExtractResult:
    """单个压缩包的解压结果。"""

    archive: Path
    dest: Path
    extracted: list[Path] = field(default_factory=list)
    success: bool = False
    error: str | None = None
    used_password: bool = False
    cancelled: bool = False     # 用户取消密码输入

    @property
    def file_count(self) -> int:
        return len(self.extracted)


def extract(
    archive_path: str | Path,
    dest_dir: str | Path,
    password_provider: PasswordProvider | None = None,
    max_password_attempts: int = DEFAULT_PASSWORD_RETRIES,
) -> ExtractResult:
    """把 ``archive_path`` 解压到 ``dest_dir``。

    Args:
        archive_path: 待解压文件
        dest_dir: 解压目标目录(不存在会创建)
        password_provider: 加密时的密码回调;签名 ``(archive_path, attempt_index) -> password|None``
        max_password_attempts: 密码重试次数(spec 5.2 默认 3)

    Returns:
        ``ExtractResult`` —— ``success`` 字段告知调用方下一步动作
    """
    archive = Path(archive_path)
    dest = Path(dest_dir)
    result = ExtractResult(archive=archive, dest=dest)

    if not archive.exists():
        result.error = "压缩包不存在"
        return result

    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        result.error = f"创建解压目录失败: {e}"
        return result

    ext = archive.suffix.lower()
    try:
        if ext in (".zip", ".exe"):
            _extract_zip(archive, dest, password_provider, max_password_attempts, result)
        elif ext == ".rar":
            _extract_rar(archive, dest, password_provider, max_password_attempts, result)
        elif ext == ".7z":
            _extract_7z(archive, dest, password_provider, max_password_attempts, result)
        else:
            result.error = f"不支持的压缩包格式: {ext}"
    except Exception as e:   # noqa: BLE001 — 兜底,详情写日志
        logger.exception("解压未知异常: %s", archive)
        result.error = f"未知异常: {e}"

    return result


# --------- 后端 ---------

def _extract_zip(
    archive: Path,
    dest: Path,
    pwd_provider: PasswordProvider | None,
    max_attempts: int,
    result: ExtractResult,
) -> None:
    try:
        zf = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as e:
        result.error = f"zip 损坏或无法读取: {e}"
        return

    with zf:
        needs_pwd = any(info.flag_bits & 0x1 for info in zf.infolist())
        password: bytes | None = None

        if needs_pwd:
            password = _solicit_password(archive, pwd_provider, max_attempts, result, _try_zip_pwd, zf)
            if password is None:
                return  # cancelled / exhausted
            result.used_password = True

        try:
            for member in zf.namelist():
                extracted = zf.extract(member, path=dest, pwd=password)
                result.extracted.append(Path(extracted))
            result.success = True
        except (RuntimeError, zipfile.BadZipFile, OSError) as e:
            result.error = f"zip 解压失败: {e}"


def _try_zip_pwd(zf: zipfile.ZipFile, pwd: bytes) -> bool:
    """对 zip 试一次密码:挑第一个加密成员尝试 read(1 byte)。"""
    for info in zf.infolist():
        if info.flag_bits & 0x1 and not info.is_dir():
            try:
                with zf.open(info, pwd=pwd) as fh:
                    fh.read(1)
                return True
            except (RuntimeError, zipfile.BadZipFile):
                return False
    return True  # 无加密成员


def _extract_rar(
    archive: Path,
    dest: Path,
    pwd_provider: PasswordProvider | None,
    max_attempts: int,
    result: ExtractResult,
) -> None:
    if not _HAS_RAR:
        result.error = "未安装 rarfile,无法解压 .rar(pip install rarfile)"
        return

    try:
        rf = rarfile.RarFile(archive)  # type: ignore[union-attr]
    except (rarfile.Error, OSError) as e:  # type: ignore[union-attr]
        result.error = f"rar 损坏或无法读取: {e}"
        return

    with rf:
        password: str | None = None
        if rf.needs_password():
            password = _solicit_password(
                archive, pwd_provider, max_attempts, result, _try_rar_pwd, rf
            )
            if password is None:
                return
            result.used_password = True
        try:
            rf.extractall(path=dest, pwd=password)
            for name in rf.namelist():
                result.extracted.append(dest / name)
            result.success = True
        except (rarfile.Error, OSError) as e:  # type: ignore[union-attr]
            result.error = f"rar 解压失败: {e}"


def _try_rar_pwd(rf, pwd: str) -> bool:
    try:
        rf.setpassword(pwd)
        # 试读一个文件头
        for info in rf.infolist():
            if not info.is_dir():
                with rf.open(info, pwd=pwd) as fh:
                    fh.read(1)
                return True
        return True
    except rarfile.Error:  # type: ignore[union-attr]
        return False


def _extract_7z(
    archive: Path,
    dest: Path,
    pwd_provider: PasswordProvider | None,
    max_attempts: int,
    result: ExtractResult,
) -> None:
    if not _HAS_7Z:
        result.error = "未安装 py7zr,无法解压 .7z(pip install py7zr)"
        return

    password: str | None = None
    # py7zr 需在 open 时给密码,因此重试要 reopen
    attempt = 0
    while True:
        try:
            with py7zr.SevenZipFile(archive, mode="r", password=password) as sz:  # type: ignore[union-attr]
                sz.extractall(path=dest)
                for name in sz.getnames():
                    result.extracted.append(dest / name)
                result.success = True
                if password is not None:
                    result.used_password = True
                return
        except py7zr.exceptions.PasswordRequired:  # type: ignore[union-attr]
            pass
        except py7zr.exceptions.UnsupportedCompressionMethodError as e:  # type: ignore[union-attr]
            result.error = f"7z 不支持的压缩方法: {e}"
            return
        except (py7zr.exceptions.Bad7zFile, OSError) as e:  # type: ignore[union-attr]
            # 密码错也走这里
            if password is None:
                result.error = f"7z 损坏或无法读取: {e}"
                return

        # 走到这里说明 需要密码 / 密码错
        if pwd_provider is None or attempt >= max_attempts:
            result.error = "需要密码但未提供或已用尽重试"
            return
        pwd = pwd_provider(archive, attempt)
        attempt += 1
        if not pwd:
            result.cancelled = True
            return
        password = pwd


# --------- 密码循环(zip / rar 通用) ---------

def _solicit_password(
    archive: Path,
    provider: PasswordProvider | None,
    max_attempts: int,
    result: ExtractResult,
    try_fn,
    handle,
):
    """循环索取密码,验证成功后返回(bytes for zip, str for rar)。

    Returns:
        密码 — zip 是 bytes,rar 是 str。验证失败 / 取消 返回 None,并填 result.error/cancelled。
    """
    if provider is None:
        result.error = "压缩包需要密码,但未提供回调"
        return None

    is_zip = isinstance(handle, zipfile.ZipFile)
    for attempt in range(max_attempts):
        pwd = provider(archive, attempt)
        if not pwd:
            result.cancelled = True
            return None
        pwd_bytes_or_str: object = pwd.encode("utf-8") if is_zip else pwd
        try:
            ok = try_fn(handle, pwd_bytes_or_str)
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            return pwd_bytes_or_str
        logger.info("密码验证失败 — %s,attempt=%d", archive, attempt)

    result.error = f"密码错误超过 {max_attempts} 次"
    return None
