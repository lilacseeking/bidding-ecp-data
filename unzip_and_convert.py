#!/usr/bin/env python3
"""
解压招标/采购文件压缩包并使用 markitdown 将 Word/PDF 文档转为 Markdown。

支持两个数据集目录：
  - data:       data/excels/zip → data/excels/unzip
  - data_jibei: data_jibei/excels/zip → data_jibei/excels/unzip

用法：
  python unzip_and_convert.py --dataset data
  python unzip_and_convert.py --dataset data_jibei
"""

import argparse
import io
import json
import logging
import os
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
import zlib
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# 需要转换为 Markdown 的文件扩展名
CONVERT_EXTS = {".docx", ".doc", ".pdf"}


# ── 工具函数 ─────────────────────────────────────────────────────────

def decode_filename(raw: str) -> str:
    """尝试将 zip 中的 GBK 编码文件名解码为可读中文。"""
    try:
        return raw.encode("cp437").decode("gbk")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return raw


def read_raw_from_zip(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """
    绕过 Python zipfile 的文件名校验，直接从本地文件头偏移处读取原始数据。
    用于处理 data_jibei 中外层 zip 文件名不匹配的问题。
    """
    fp = zf.fp
    fp.seek(info.header_offset)
    header = fp.read(30)
    fname_len = struct.unpack("<H", header[26:28])[0]
    extra_len = struct.unpack("<H", header[28:30])[0]
    data_offset = info.header_offset + 30 + fname_len + extra_len

    compress_method = struct.unpack("<H", header[8:10])[0]
    fp.seek(data_offset)
    raw_compressed = fp.read(info.compress_size)

    if compress_method == 0:  # stored
        return raw_compressed
    elif compress_method == 8:  # deflated
        return zlib.decompress(raw_compressed, -15)
    else:
        raise ValueError(f"不支持的压缩方式: {compress_method}")


# ── 解压逻辑 ─────────────────────────────────────────────────────────

def extract_data_zip(zip_path: Path, dest_dir: Path) -> list[Path]:
    """
    解压 data 数据集的 zip（扁平结构，GBK 文件名）。
    返回解压出的文件路径列表。
    """
    extracted = []
    stem = zip_path.stem  # zip 文件名（不含 .zip）
    out_dir = dest_dir / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.file_size == 0:
                continue
            name = decode_filename(info.filename)
            # 去掉可能的目录前缀，只保留文件名
            basename = Path(name).name
            if not basename:
                continue
            target = out_dir / basename
            try:
                data = read_raw_from_zip(zf, info)
            except Exception as e:
                log.warning(f"  读取失败 {basename}: {e}")
                continue
            target.write_bytes(data)
            extracted.append(target)
            log.debug(f"  解压: {basename}")

    return extracted


def extract_jibei_zip(zip_path: Path, dest_dir: Path) -> list[Path]:
    """
    解压 data_jibei 数据集的 zip（嵌套结构：外层 zip → 内层 zip → 实际文件）。
    外层 zip 存在文件名编码不匹配问题，使用底层读取绕过校验。
    返回解压出的文件路径列表。
    """
    extracted = []
    stem = zip_path.stem
    out_dir = dest_dir / stem

    with zipfile.ZipFile(zip_path) as outer_zf:
        for info in outer_zf.infolist():
            if info.file_size == 0:
                continue
            name = decode_filename(info.filename)

            if name.lower().endswith(".zip"):
                # 内层 zip：用底层读取绕过文件名校验
                try:
                    raw = read_raw_from_zip(outer_zf, info)
                except Exception as e:
                    log.warning(f"  读取内层 zip 失败 {name}: {e}")
                    continue

                inner_zf = zipfile.ZipFile(io.BytesIO(raw))
                for inner_info in inner_zf.infolist():
                    if inner_info.file_size == 0:
                        continue
                    inner_name = decode_filename(inner_info.filename)
                    # 保留内层目录结构
                    rel = Path(inner_name)
                    if rel.is_absolute():
                        rel = Path(*rel.parts[1:])
                    target = out_dir / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with inner_zf.open(inner_info, "r") as src:
                            data = src.read()
                    except zipfile.BadZipFile:
                        data = read_raw_from_zip(inner_zf, inner_info)
                    target.write_bytes(data)
                    extracted.append(target)
                    log.debug(f"  解压: {rel}")
            else:
                # 非 zip 文件：直接解压
                basename = Path(name).name
                if not basename:
                    continue
                out_dir.mkdir(parents=True, exist_ok=True)
                target = out_dir / basename
                try:
                    raw = read_raw_from_zip(outer_zf, info)
                except Exception:
                    with outer_zf.open(info, "r") as src:
                        raw = src.read()
                target.write_bytes(raw)
                extracted.append(target)
                log.debug(f"  解压: {basename}")

    return extracted


# ── 索引管理 ──────────────────────────────────────────────────────────

_index_lock = threading.Lock()


def _load_index(index_path: Path) -> dict:
    """加载转换索引 JSON 文件。"""
    if index_path.exists():
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            log.warning(f"索引文件损坏，将重建: {index_path}")
    return {}


def _save_index(index_path: Path, index: dict):
    """保存转换索引 JSON 文件。"""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Markdown 转换 ────────────────────────────────────────────────────

def _fallback_doc_to_text(fp: Path) -> str | None:
    """
    对 markitdown 无法处理的 .doc (OLE2) 文件，使用 olefile 提取纯文本。
    返回提取到的文本，若失败返回 None。
    """
    try:
        import olefile
        if not olefile.isOleFile(str(fp)):
            return None
        ole = olefile.OleFileIO(str(fp))
        # Word 二进制文档的文本在 WordDocument 流中
        text_parts = []
        for stream_name in ole.listdir():
            name = "/".join(stream_name)
            if name in ("WordDocument", "1Table", "0Table"):
                try:
                    data = ole.openstream(stream_name).read()
                    # 尝试 UTF-16LE 解码，忽略不可解码字节
                    decoded = data.decode("utf-16-le", errors="ignore")
                    # 过滤掉控制字符，保留可打印字符和换行
                    cleaned = "".join(
                        c for c in decoded
                        if c == "\n" or c == "\r" or c == "\t" or (ord(c) >= 32)
                    )
                    if len(cleaned.strip()) > 50:  # 至少要有有意义的内容
                        text_parts.append(cleaned.strip())
                except Exception:
                    continue
        ole.close()
        if text_parts:
            # 返回最长的文本块（通常 WordDocument 流的内容最多）
            return max(text_parts, key=len)
    except Exception:
        pass
    return None


def _convert_single_file(
    fp: Path,
    md_path: Path,
    md: "MarkItDown",
) -> tuple[bool, str]:
    """
    转换单个文件，返回 (是否成功, 信息描述)。
    此函数线程安全（markitdown 实例内部无状态）。
    """
    # 尝试 1: markitdown 直接转换
    try:
        result = md.convert(str(fp))
        content = result.text_content
        if content and content.strip():
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(content, encoding="utf-8")
            return True, f"✓ {fp.name} → {md_path.relative_to(md_path.parents[2])}"
        else:
            pass  # 尝试 fallback
    except BaseException:
        pass  # 尝试 fallback

    # 尝试 2: 对 .doc 文件使用 olefile fallback
    fallback_text = _fallback_doc_to_text(fp)
    if fallback_text and fallback_text.strip():
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(fallback_text, encoding="utf-8")
        return True, f"✓ {fp.name} → {md_path.relative_to(md_path.parents[2])} (fallback)"

    return False, f"✗ {fp.name}: 所有转换方式均失败"


def convert_to_markdown(
    files: list[Path],
    md_dir: Path,
    index_path: Path,
    max_workers: int = 20,
) -> int:
    """
    使用 markitdown 将 Word/PDF 文件转为 Markdown，输出到独立的 md 目录。
    使用 JSON 索引跳过已转换文件，使用线程池并行转换。
    返回本次成功转换的文件数。
    """
    try:
        from markitdown import MarkItDown
    except ImportError:
        log.error("markitdown 未安装，请运行: pip install markitdown")
        return 0

    # 加载索引
    index = _load_index(index_path)

    # 收集需要转换的文件
    tasks: list[tuple[Path, Path]] = []  # (源文件路径, 目标 md 路径)
    skipped = 0
    total_convertible = 0

    for fp in files:
        ext = fp.suffix.lower()
        if ext not in CONVERT_EXTS:
            continue
        total_convertible += 1

        # 计算目标路径: md_dir/{项目文件夹名}/{文档名}.md
        project_dir = fp.parent  # e.g. unzip/0711-19OTL170_xxx/
        project_name = project_dir.name  # e.g. 0711-19OTL170_xxx
        out_path = md_dir / project_name / fp.with_suffix(".md").name

        # 索引键: 源文件相对路径
        source_key = str(fp.relative_to(fp.parents[3]))  # relative to excels/
        source_mtime = fp.stat().st_mtime

        # 检查索引: 输出文件已存在则跳过（MD 输出在独立目录，不受 unzip 重建影响）
        if source_key in index:
            entry = index[source_key]
            if entry.get("output") and Path(entry["output"]).exists():
                skipped += 1
                continue

        tasks.append((fp, out_path))

    if skipped > 0:
        log.info(f"已跳过 {skipped} 个已转换文件（索引命中）")

    if not tasks:
        log.info("没有需要转换的文件")
        return 0

    log.info(f"待转换: {len(tasks)} 个文件，使用 {max_workers} 线程")
    log.info("")

    # 每个线程拥有自己的 MarkItDown 实例（线程安全）
    _thread_local = threading.local()

    def _get_md() -> "MarkItDown":
        if not hasattr(_thread_local, "md"):
            _thread_local.md = MarkItDown()
        return _thread_local.md

    converted = 0
    failed_files: list[tuple[str, str]] = []
    done_count = 0
    total_tasks = len(tasks)

    def _do_convert(fp: Path, out_path: Path) -> tuple[Path, Path, bool, str]:
        md = _get_md()
        ok, msg = _convert_single_file(fp, out_path, md)
        return fp, out_path, ok, msg

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_do_convert, fp, out_path): (fp, out_path)
            for fp, out_path in tasks
        }

        for future in as_completed(futures):
            fp, out_path, ok, msg = future.result()
            done_count += 1

            if ok:
                converted += 1
                log.info(f"  [{done_count}/{total_tasks}] {msg}")

                # 更新索引（线程安全）
                source_key = str(fp.relative_to(fp.parents[3]))
                with _index_lock:
                    index[source_key] = {
                        "source": str(fp),
                        "output": str(out_path),
                        "source_mtime": fp.stat().st_mtime,
                        "converted_at": datetime.now().isoformat(),
                    }
            else:
                failed_files.append((fp.name, msg))
                log.warning(f"  [{done_count}/{total_tasks}] {msg}")

    # 保存索引
    with _index_lock:
        _save_index(index_path, index)
    log.info(f"索引已保存: {index_path}")

    # 打印转换统计
    log.info("")
    log.info(f"--- 转换统计 ---")
    log.info(f"可转换文档总数: {total_convertible}")
    log.info(f"本次转换: {converted}")
    log.info(f"跳过（已转换）: {skipped}")
    log.info(f"转换失败: {len(failed_files)}")
    if total_convertible > 0:
        success_total = converted + skipped
        rate = success_total / total_convertible * 100
        log.info(f"成功率: {rate:.1f}%")
    if failed_files:
        log.info(f"失败文件列表:")
        for name, err in failed_files:
            log.info(f"  - {name}: {err}")

    return converted


# ── README 生成 ───────────────────────────────────────────────────────

def generate_readme(md_dir: Path, dataset: str, zip_count: int,
                    file_count: int, md_count: int) -> Path:
    """在 md 目录下生成 README.md。"""
    readme_path = md_dir / "README.md"

    content = f"""\
# {dataset} 文档转换结果

本目录由 `unzip_and_convert.py` 脚本自动生成。

## 处理统计

- 数据集：`{dataset}`
- 处理的压缩包数量：{zip_count}
- 解压出的文件总数：{file_count}
- 成功转换为 Markdown 的文档数：{md_count}

## 目录结构

每个项目以项目文件夹名（压缩包文件名去掉 `.zip`）作为子目录名，
内部包含该项目所有转换后的 `.md` 文件。

## 使用方法

在项目根目录下执行以下命令：

```bash
# 处理 data 数据集（国家电网总部招标/采购文件）
python unzip_and_convert.py --dataset data

# 处理 data_jibei 数据集（国网冀北电力采购文件）
python unzip_and_convert.py --dataset data_jibei
```

## 依赖

- Python 3.10+
- markitdown：`pip install markitdown[all]`

## 转换规则

脚本会将以下格式的文件自动转换为 `.md` 文件：

- `.docx` → `.md`（Word 文档）
- `.doc`  → `.md`（旧版 Word 文档）
- `.pdf`  → `.md`（PDF 文档）

`.xlsx`、`.xls` 等表格文件保持原样不做转换。
"""
    readme_path.write_text(content, encoding="utf-8")
    log.info(f"README 已生成: {readme_path}")
    return readme_path


# ── 安全删除目录 ──────────────────────────────────────────────────────

def _remove_readonly(func, path, excinfo):
    """shutil.rmtree 的错误回调：移除只读属性后重试。"""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def _safe_rmtree(path: Path, retries: int = 3):
    """安全删除目录，处理 Windows 上的文件锁定和只读问题。"""
    for attempt in range(retries):
        try:
            shutil.rmtree(str(path), onerror=_remove_readonly)
            if not path.exists():
                return
        except Exception as e:
            log.warning(f"删除失败(尝试 {attempt+1}/{retries}): {e}")

        # 尝试用 Windows rmdir 强制删除
        if sys.platform == "win32" and path.exists():
            try:
                subprocess.run(
                    ["cmd", "/c", "rmdir", "/s", "/q", str(path)],
                    capture_output=True, timeout=30,
                )
                if not path.exists():
                    return
            except Exception:
                pass

        if attempt < retries - 1:
            time.sleep(2)

    if path.exists():
        log.error(f"无法完全删除目录 {path}，将尝试继续覆盖")
        # 不 raise，允许后续 mkdir(exist_ok=True) 覆盖


# ── 主流程 ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="解压招标/采购文件压缩包并使用 markitdown 转为 Markdown",
    )
    parser.add_argument(
        "--dataset",
        required=True,
        choices=["data", "data_jibei"],
        help="选择数据集: data（总部）或 data_jibei（冀北）",
    )
    parser.add_argument(
        "--skip-convert",
        action="store_true",
        help="仅解压，跳过 Markdown 转换",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=20,
        help="转换线程数（默认 20）",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent
    dataset_root = project_root / args.dataset
    zip_dir = dataset_root / "excels" / "zip"
    unzip_dir = dataset_root / "excels" / "unzip"
    md_dir = dataset_root / "md"
    index_path = md_dir / "index.json"

    if not zip_dir.is_dir():
        log.error(f"zip 目录不存在: {zip_dir}")
        sys.exit(1)

    if unzip_dir.exists():
        log.info(f"清理已有解压目录: {unzip_dir}")
        _safe_rmtree(unzip_dir)
    unzip_dir.mkdir(parents=True, exist_ok=True)

    zip_files = sorted(zip_dir.glob("*.zip"))
    if not zip_files:
        log.error(f"未找到 zip 文件: {zip_dir}")
        sys.exit(1)

    log.info(f"数据集: {args.dataset}")
    log.info(f"zip 目录: {zip_dir}")
    log.info(f"解压目录: {unzip_dir}")
    log.info(f"MD 输出目录: {md_dir}")
    log.info(f"待处理压缩包: {len(zip_files)} 个")
    log.info("")

    all_extracted: list[Path] = []
    extract_fn = extract_jibei_zip if args.dataset == "data_jibei" else extract_data_zip

    for i, zf_path in enumerate(zip_files, 1):
        log.info(f"[{i}/{len(zip_files)}] 解压: {zf_path.name}")
        try:
            files = extract_fn(zf_path, unzip_dir)
            all_extracted.extend(files)
            log.info(f"  → {len(files)} 个文件")
        except Exception as e:
            log.error(f"  ✗ 解压失败: {e}")

    log.info("")
    log.info(f"解压完成，共 {len(all_extracted)} 个文件")

    md_count = 0
    if not args.skip_convert:
        md_dir.mkdir(parents=True, exist_ok=True)
        log.info("")
        log.info(f"开始转换 Word/PDF → Markdown（{args.workers} 线程）...")
        log.info("")
        md_count = convert_to_markdown(
            all_extracted, md_dir, index_path, max_workers=args.workers,
        )
        log.info("")
        log.info(f"转换完成: {md_count} 个文档已转为 Markdown")

    if not args.skip_convert:
        generate_readme(md_dir, args.dataset, len(zip_files),
                        len(all_extracted), md_count)

    log.info("")
    log.info("全部完成！")


if __name__ == "__main__":
    main()
