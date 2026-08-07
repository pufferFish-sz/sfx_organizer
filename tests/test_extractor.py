"""extractor 单元测试。

只测 zip 后端(stdlib 内置)。.rar/.7z 在依赖未安装时由 extractor 自身返回友好错误,
这里只做"未安装也能体面失败"的断言。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from core.extractor import extract


def _make_zip(path: Path, members: dict[str, bytes], password: str | None = None) -> None:
    """生成测试 zip。zipfile 写不加密文件,加密需要外部工具,因此密码 zip 我们另外构造。"""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        if password:
            zf.setpassword(password.encode())


def _make_encrypted_zip(path: Path, members: dict[str, bytes], password: str) -> None:
    """zipfile 写入不支持设置加密(只能读)。借助 pyzipper 之类不在依赖里 —
    所以我们手工写一个最小加密 zip:这里采用 zipfile 写入后再加密不可行,
    因此使用预生成的 bytes — 见 fixture。
    """
    raise NotImplementedError  # 占位:见下方 fixture 实现


class TestZipBackend:
    def test_extracts_plain_zip(self, tmp_path: Path):
        archive = tmp_path / "pack.zip"
        _make_zip(archive, {
            "a.wav": b"audio-a",
            "sub/b.mp3": b"audio-b",
        })
        dest = tmp_path / "out"
        result = extract(archive, dest)
        assert result.success
        assert result.error is None
        assert (dest / "a.wav").read_bytes() == b"audio-a"
        assert (dest / "sub" / "b.mp3").read_bytes() == b"audio-b"
        assert result.file_count == 2

    def test_missing_archive(self, tmp_path: Path):
        result = extract(tmp_path / "no_such.zip", tmp_path / "out")
        assert not result.success
        assert "不存在" in (result.error or "")

    def test_corrupt_zip(self, tmp_path: Path):
        archive = tmp_path / "corrupt.zip"
        archive.write_bytes(b"this is not a real zip file at all")
        result = extract(archive, tmp_path / "out")
        assert not result.success
        assert result.error and "损坏" in result.error

    def test_unsupported_extension(self, tmp_path: Path):
        archive = tmp_path / "foo.tar"
        archive.write_bytes(b"x")
        result = extract(archive, tmp_path / "out")
        assert not result.success
        assert result.error and "不支持" in result.error

    def test_creates_dest_dir(self, tmp_path: Path):
        archive = tmp_path / "pack.zip"
        _make_zip(archive, {"a.wav": b"x"})
        dest = tmp_path / "new_dest" / "nested"
        result = extract(archive, dest)
        assert result.success
        assert dest.is_dir()

    def test_chinese_member_names(self, tmp_path: Path):
        archive = tmp_path / "中文包.zip"
        _make_zip(archive, {"森林_鸟鸣.wav": b"x"})
        dest = tmp_path / "out"
        result = extract(archive, dest)
        assert result.success
        assert (dest / "森林_鸟鸣.wav").exists()


class TestPasswordZip:
    """加密 zip 测试 — 用 pyzipper 不在依赖里,用 zipfile 自带写法构造。"""

    @pytest.fixture
    def encrypted_archive(self, tmp_path: Path) -> Path | None:
        """尝试用 pyzipper 写加密 zip;不可用则跳过此整组测试。"""
        try:
            import pyzipper  # type: ignore
        except ImportError:
            pytest.skip("pyzipper 未安装,跳过加密 zip 用例")
        archive = tmp_path / "secret.zip"
        with pyzipper.AESZipFile(archive, "w", compression=pyzipper.ZIP_LZMA, encryption=pyzipper.WZ_AES) as zf:
            zf.setpassword(b"hunter2")
            zf.writestr("secret.wav", b"top secret")
        return archive

    def test_correct_password(self, encrypted_archive: Path, tmp_path: Path):
        provider = lambda _arc, _att: "hunter2"
        result = extract(encrypted_archive, tmp_path / "out", password_provider=provider)
        assert result.success, result.error
        assert result.used_password

    def test_wrong_password_exhausts_retries(self, encrypted_archive: Path, tmp_path: Path):
        calls = []
        def provider(_arc, attempt):
            calls.append(attempt)
            return "wrong"
        result = extract(
            encrypted_archive, tmp_path / "out",
            password_provider=provider, max_password_attempts=3,
        )
        assert not result.success
        assert len(calls) == 3
        assert "密码" in (result.error or "")

    def test_user_cancels_first_attempt(self, encrypted_archive: Path, tmp_path: Path):
        provider = lambda _arc, _att: None
        result = extract(encrypted_archive, tmp_path / "out", password_provider=provider)
        assert not result.success
        assert result.cancelled

    def test_no_provider_when_password_required(self, encrypted_archive: Path, tmp_path: Path):
        result = extract(encrypted_archive, tmp_path / "out", password_provider=None)
        assert not result.success
        assert "回调" in (result.error or "")


class TestOptionalBackends:
    def test_rar_without_backend_friendly_error(self, tmp_path: Path):
        # 测试 rarfile 不存在时返回友好错误。若用户真装了 rarfile,这条会跳过。
        try:
            import rarfile  # noqa: F401
            pytest.skip("rarfile 已安装,跳过此用例")
        except ImportError:
            pass
        archive = tmp_path / "x.rar"
        archive.write_bytes(b"fake")
        result = extract(archive, tmp_path / "out")
        assert not result.success
        assert "rarfile" in (result.error or "")

    def test_7z_without_backend_friendly_error(self, tmp_path: Path):
        try:
            import py7zr  # noqa: F401
            pytest.skip("py7zr 已安装,跳过此用例")
        except ImportError:
            pass
        archive = tmp_path / "x.7z"
        archive.write_bytes(b"fake")
        result = extract(archive, tmp_path / "out")
        assert not result.success
        assert "py7zr" in (result.error or "")
