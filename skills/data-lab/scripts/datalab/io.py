"""Read explicit local data files. Never fetch URLs or execute workbook macros."""
from __future__ import annotations
import csv
import io
import json
import re
from pathlib import Path
import pandas as pd


def _headers(values):
    names = [str(v).strip() if v is not None else "" for v in values]
    if not names or any(not x for x in names):
        raise ValueError("表头不能为空；请指定单一矩形数据表，首行为字段名。")
    if len(set(names)) != len(names):
        raise ValueError("字段名重复（含去除首尾空格后的重复），请先明确各列含义。")
    return names


def _infer_safe_numbers(df):
    for col in df:
        series = df[col]
        if not pd.api.types.is_object_dtype(series.dtype) and not pd.api.types.is_string_dtype(series.dtype):
            continue
        if re.search(r"(^id$|_id$|编号|编码|代码|邮编|电话|手机)", str(col), re.I):
            continue
        values = series.dropna().astype(str)
        if values.empty:
            continue
        # Preserve leading zeros, percentages, currencies, and mixed text; no silent rescaling.
        if values.str.match(r"^[+-]?0\d").any():
            continue
        if values.str.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?").all():
            df[col] = pd.to_numeric(series, errors="raise")
    return df


def load_frame(path, sheet=None):
    from .safety import register_input
    register_input(path, raw=True)
    path = Path(path).expanduser()
    if not path.is_file():
        raise ValueError(f"找不到数据文件: {path}")
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("CSV/TSV 需为 UTF-8；请显式转换原编码后再读入，避免乱码。") from exc
        sep = "\t" if suffix == ".tsv" else ","
        # Parse once with the CSV grammar. Splitting physical lines would break
        # quoted multiline fields, while pandas can infer an index from an
        # over-wide row and silently discard its leading fields.
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=sep, strict=True)
        try:
            header = next(row for row in reader if row)
            names = _headers(header)
            records = []
            for row in reader:
                if not row:  # A physically empty record is not a missing-data row.
                    continue
                if len(row) != len(names):
                    raise ValueError(
                        f"CSV/TSV 记录字段数与表头不符（结束行 {reader.line_num}）："
                        f"预期 {len(names)} 个字段，实际 {len(row)} 个。"
                    )
                records.append(row)
        except StopIteration as exc:
            raise ValueError("数据文件为空。") from exc
        except csv.Error as exc:
            raise ValueError(f"CSV/TSV 格式无效（行 {reader.line_num}）：{exc}") from exc
        # Preserve legitimate 'NA', identifiers and quoted text; only empty
        # cells are missing. Do not let a second parser reinterpret the rows.
        df = pd.DataFrame(records, columns=names, dtype=object)
        df = df.mask(df.eq(""), pd.NA)
    elif suffix == ".xlsx":
        from openpyxl import load_workbook
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            if sheet is None:
                if len(workbook.sheetnames) != 1:
                    raise ValueError("Excel 有多个工作表，请用 --sheet 指定: " + ", ".join(workbook.sheetnames))
                chosen = workbook.sheetnames[0]
            elif isinstance(sheet, int) or (isinstance(sheet, str) and sheet.isdigit() and sheet not in workbook.sheetnames):
                index = int(sheet)
                if index < 0 or index >= len(workbook.sheetnames):
                    raise ValueError("工作表索引越界（从 0 开始）。")
                chosen = workbook.sheetnames[index]
            else:
                chosen = str(sheet)
                if chosen not in workbook.sheetnames:
                    raise ValueError(f"工作表不存在: {chosen}")
            rows = workbook[chosen].iter_rows(values_only=True)
            names = _headers(next(rows, []))
            df = pd.DataFrame(list(rows), columns=names)
            df = df.dropna(how="all")
        finally:
            workbook.close()
    elif suffix in (".json", ".jsonl", ".ndjson"):
        text = path.read_text(encoding="utf-8-sig")
        data = [json.loads(line) for line in text.splitlines() if line.strip()] if suffix != ".json" else json.loads(text)
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise ValueError("JSON 输入必须为对象数组；嵌套文档请先选定要分析的记录集合。")
        df = pd.DataFrame(data)
        for col in df:
            df[col] = df[col].map(lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (dict, list)) else v)
        df.columns = _headers(df.columns)
    elif suffix == ".parquet":
        try:
            df = pd.read_parquet(path)
        except ImportError as exc:
            raise ValueError("Parquet 需要可选依赖 pyarrow：安装项目的 [parquet] 扩展。") from exc
        df.columns = _headers(df.columns)
    else:
        raise ValueError("支持 .csv、.tsv、.xlsx、.json、.jsonl、.ndjson、.parquet；旧 .xls 请先另存为 .xlsx。")
    if df.empty:
        raise ValueError("数据表没有可分析的记录。")
    return _infer_safe_numbers(df).reset_index(drop=True)
