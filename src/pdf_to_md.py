"""使用 MarkItDown 将 book 目录下的 PDF 文件转换为 Markdown 格式"""

import os
from pathlib import Path
from markitdown import MarkItDown


def convert_pdfs_to_md(book_dir: str):
    """将指定目录下所有 PDF 转换为 md 文件，保存在同一目录"""
    book_path = Path(book_dir)
    md = MarkItDown()

    pdf_files = list(book_path.glob("*.pdf"))
    if not pdf_files:
        print("book 目录下没有找到 PDF 文件")
        return

    print(f"找到 {len(pdf_files)} 个 PDF 文件，开始转换...\n")

    for pdf_file in pdf_files:
        md_file = pdf_file.with_suffix(".md")
        print(f"正在转换: {pdf_file.name}")
        try:
            result = md.convert(str(pdf_file))
            md_file.write_text(result.text_content, encoding="utf-8")
            print(f"  -> 已保存: {md_file.name}\n")
        except Exception as e:
            print(f"  -> 转换失败: {e}\n")

    print("全部转换完成！")


if __name__ == "__main__":
    book_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "book")
    convert_pdfs_to_md(book_dir)
