#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
词库批处理（momo 仓库定制版，改自 aux_go.py）
────────────────────────────────────────
功能：
1. 递归处理 input_dicts/ 下所有词库（.yaml/.yml/.txt），保留子目录结构
2. 剥离上游词库已有辅码，按 TONE_MODE 转换声调格式：
   - "digit"：数字声调（bā→ba1，lǚ→lv3，er→er5）
   - "mark" ：声调符号（ba1→bā，lv3→lǚ，er5→er）
3. 按根目录 aux_code.csv 为每个字重新刷辅码，格式：拼音;辅码
   - 本地码表查不到的字：回退沿用上游词库自带的辅码（保证不丢码）
   - 同时打印告警日志，提示需要补录进 aux_code.csv 的字
4. 输出到 output_dicts/dicts-pro/，文件名不含 .pro. 时自动加 .pro 后缀
5. 黑名单文件（en*、mixed*）与 userdb 格式文件原样复制
"""

import os
import re
import csv
import shutil
import fnmatch
import unicodedata
from typing import Dict, List, Tuple

# ──────────────── 配 置 区 ────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "aux_code.csv")        # 辅码表：字、辅码 两列
INPUT_DIR = os.path.join(BASE_DIR, "input_dicts")         # 输入目录（workflow 下载解压产物）
OUT_ROOT = os.environ.get("AUX_OUT_ROOT", os.path.join(BASE_DIR, "output_dicts"))

BLACKLIST_PATTERNS = ["en*", "mixed*"]                    # 通配黑名单，命中则原样复制
OUTPUT_SUFFIX = ".pro"                                    # 输出文件名后缀（已含 .pro. 的文件不重复加）
SEP = ";"                                                 # 拼音与辅码的分隔符
AUX_SEP_REGEX = r'[;\[]'                                  # 辅码起始分隔符（分号或方括号）

TONE_MODE = os.environ.get("AUX_TONE_MODE", "mark")       # "digit"=数字声调（原行为）；"mark"=声调符号（反向）
STANDARD_JQXY = True                                      # mark 模式：j/q/x/y 后的 ü 按标准正字法写作 u（jū 而非 jǖ）
# ──────────────────────────────────────────

CJK_PATTERN = re.compile(
    r"[〇々の𖿲𖿳\u2e80-\u2fdf\u3400-\u4DBF\u4E00-\u9FFF\U00020000-\U0003347F]"
)
IGNORE_CHARS = set("，。？！、：～·＆“”（）「」『』…")
USERDB_MARKERS = ("#@/db_type\tuserdb", "# Rime user dictionary")
PASSTHROUGH_SET = {"的\td\t1000", "了\tl\t999", "吗\tm\t999", "吧\tb\t999"}

COMBINING_TONE = {
    "\u0304": "1",  # 一声
    "\u0301": "2",  # 二声
    "\u030c": "3",  # 三声
    "\u0300": "4",  # 四声
    "\u0307": "5",  # 轻声
}

TONE_TO_MARK = {
    "1": "\u0304",  # 一声 → 长音符
    "2": "\u0301",  # 二声 → 锐音符
    "3": "\u030c",  # 三声 → 抑扬符
    "4": "\u0300",  # 四声 → 重音符
    "5": "",        # 轻声 → 去掉数字、不加符号
}


# ---------- 基础工具 ----------
def convert_tone(pinyin: str, add_tone5: bool = True) -> str:
    """带声调符号的拼音 → 数字声调；已是数字声调的跳过。（digit 模式用）"""
    if re.search(r"[1-5]$", pinyin):
        return pinyin
    nfd = unicodedata.normalize("NFD", pinyin)
    has_tone = False
    tone_digit = ""
    result = []
    i = 0
    while i < len(nfd):
        char = nfd[i]
        if char in COMBINING_TONE:
            has_tone = True
            tone_digit = COMBINING_TONE[char]
            i += 1
            continue
        if unicodedata.category(char).startswith("M"):  # 其他组合符号（如 ü 的分音符）
            i += 1
            continue
        result.append(char)
        i += 1
    if has_tone:
        result.append(tone_digit)
    elif add_tone5:
        result.append("5")
    # j/q/x/y 后的 u 实为 ü
    if len(result) >= 2 and result[0] in "jqxy" and result[1] == "u":
        result[1] = "v"
    return "".join(result)


# ---------- mark 模式：数字声调 → 声调符号 ----------
def _find_tone_vowel(s: str) -> int:
    """按拼音标调规则定位应标调的元音下标。
    a > o > e 优先；只剩 i/u/ü 时标在最后一个元音（liu→u、gui→i 自然覆盖）。"""
    for v in ("a", "o", "e"):
        p = s.find(v)
        if p != -1:
            return p
    last = -1
    for i, ch in enumerate(s):
        if ch in "iu":
            last = i
    return last


def _place_tone(base: str, mark: str) -> str:
    """在正确的元音上插入声调组合符号，输出 NFC 规范形式（如 bā、lǚ）。"""
    nfd = unicodedata.normalize("NFD", base)
    idx = _find_tone_vowel(nfd)
    if idx == -1:
        return unicodedata.normalize("NFC", base)  # 无元音（如 hm/n），原样返回
    # 跳过元音后紧跟的组合符号（如 ü 的分音符），保证组合顺序正确
    j = idx + 1
    while j < len(nfd) and unicodedata.combining(nfd[j]):
        j += 1
    return unicodedata.normalize("NFC", nfd[:j] + mark + nfd[j:])


def digit_to_mark(pinyin: str) -> str:
    """数字声调 → 声调符号（convert_tone 的逆操作，mark 模式用）。
    ba1→bā，lv3→lǚ，er5→er（轻声去数字不加符）；无数字的段仅做 v→ü 正字处理。"""
    if not pinyin:
        return pinyin
    if pinyin[-1] in "12345":
        tone, base = pinyin[-1], pinyin[:-1]
    else:
        tone, base = "", pinyin
    if STANDARD_JQXY:
        base = re.sub(r"([jqxy])v", r"\1u", base)   # jū 而非 jǖ（标准正字法）
    base = base.replace("v", "\u00fc")              # 其余 v → ü
    mark = TONE_TO_MARK.get(tone, "")
    if mark:
        return _place_tone(base, mark)
    return unicodedata.normalize("NFC", base)


def clean_aux_from_seg(seg: str) -> str:
    """剥离拼音段中已有的辅助码（按 ; 或 [ 切分，取拼音部分）"""
    return re.split(AUX_SEP_REGEX, seg, 1)[0]


def split_seg(seg: str) -> Tuple[str, str]:
    """把 'hao3;f' 拆成 ('hao3', 'f')；无辅码则 ('hao3', '')"""
    parts = re.split(AUX_SEP_REGEX, seg, 1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def is_valid_han_word(word: str) -> bool:
    return all(
        ch.isspace() or ch in IGNORE_CHARS or CJK_PATTERN.match(ch) for ch in word
    )


def extract_cn_chars(word: str) -> List[str]:
    return [ch for ch in word if CJK_PATTERN.match(ch)]


def add_suffix_before_extensions(filename: str, suffix: str) -> str:
    if not suffix:
        return filename
    i = filename.find(".")
    return (filename + suffix) if i == -1 else (filename[:i] + suffix + filename[i:])


def is_blacklisted(filename: str) -> bool:
    return any(fnmatch.fnmatch(filename, pat) for pat in BLACKLIST_PATTERNS)


# ---------- 辅码 CSV 加载 ----------
def load_aux_map(csv_path: str) -> Dict[str, str]:
    if not os.path.isfile(csv_path):
        print(f"错误：辅码表不存在：{csv_path}")
        raise SystemExit(1)
    aux_map: Dict[str, str] = {}
    with open(csv_path, "r", encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            print("错误：辅码 CSV 为空或无表头")
            raise SystemExit(1)
        han_col, aux_col = reader.fieldnames[0], reader.fieldnames[1]
        for row in reader:
            han = (row.get(han_col) or "").strip()
            cell = row.get(aux_col) or ""
            if not han:
                continue
            # 提取连续字母块，逗号连接（兼容 "abc,de" / "abc de" 等写法）
            blocks = re.findall(r"[a-zA-Z]+", cell)
            aux_code = ",".join(b.lower() for b in blocks)
            if aux_code:
                aux_map[han] = aux_code
    print(f"✓ 已加载辅码表: {len(aux_map)} 条")
    return aux_map


# ---------- 处理单个词库文件 ----------
def process_dict_file(in_file: str, out_file: str, aux_map: Dict[str, str], sep: str = SEP):
    missing_chars = set()  # 本文件中不在 aux_code.csv 且上游也无辅码的字

    with open(in_file, "r", encoding="utf-8-sig") as fin, open(
        out_file, "w", encoding="utf-8", newline="\n"
    ) as fout:
        in_header = True
        for raw in fin:
            line = raw.rstrip("\n").rstrip("\r")

            if in_header:
                fout.write(line + "\n")
                if line.strip() == "...":
                    in_header = False
                continue

            if not line or line.lstrip().startswith("#"):
                fout.write(line + "\n")
                continue

            parts = line.split("\t")
            if len(parts) == 1:
                fout.write(line + "\n")
                continue

            han = parts[0]
            col2 = parts[1] if len(parts) > 1 else ""
            col3 = parts[2] if len(parts) > 2 else ""
            col4 = parts[3] if len(parts) > 3 else ""

            # 兼容「拼音\t汉字」列序错位的容错：若第二列是纯数字（词频）则交换
            if re.fullmatch(r"\d+", col2 or ""):
                col3, col2 = col2, ""

            if line.strip() in PASSTHROUGH_SET:
                fout.write(line + "\n")
                continue

            # 保留原始段用于辅码回退；按 TONE_MODE 决定声调转换方向
            raw_segs = col2.split(" ") if col2 else []
            if TONE_MODE == "mark":
                pinyins = [digit_to_mark(clean_aux_from_seg(py)) for py in raw_segs]
            else:
                pinyins = [convert_tone(clean_aux_from_seg(py)) for py in raw_segs]
            cn_chars = extract_cn_chars(han)

            if not is_valid_han_word(han) or len(cn_chars) != len(pinyins):
                # 只打日志，不把警告写进词库
                print(f"⚠ 拼音数与字数不匹配，保留原行: {os.path.basename(in_file)} => {line}")
                fout.write(line + "\n")
                continue

            new_cols = []
            for i, py in enumerate(pinyins):
                ch = cn_chars[i]
                aux = aux_map.get(ch, "")
                if not aux:
                    # 本地码表查不到 → 回退沿用上游已有的辅码
                    upstream_aux = split_seg(raw_segs[i])[1] if i < len(raw_segs) else ""
                    if upstream_aux:
                        aux = upstream_aux
                    else:
                        missing_chars.add(ch)
                new_cols.append(f"{py}{sep}{aux}" if aux else py)
            fout.write(
                "\t".join([han, " ".join(new_cols)] + ([col3] if col3 else []) + ([col4] if col4 else [])) + "\n"
            )

    if missing_chars:
        print(
            f"⚠ {os.path.basename(in_file)}: 以下字不在 aux_code.csv 中且上游无辅码，"
            f"已输出为无辅码（建议补录）：{''.join(sorted(missing_chars))}"
        )


# ---------- 文件级检测 ----------
def has_userdb_marker(filepath: str) -> bool:
    try:
        with open(filepath, "r", encoding="utf-8-sig", errors="ignore") as f:
            for _ in range(30):  # 只看文件头附近
                line = f.readline()
                if not line:
                    break
                if any(m in line for m in USERDB_MARKERS):
                    return True
    except OSError:
        pass
    return False


# ---------- 批量处理（递归，保留子目录结构） ----------
def collect_dict_files(input_dir: str) -> List[str]:
    valid = []
    for root, _, files in os.walk(input_dir):
        for name in files:
            if name.endswith((".yaml", ".yml", ".txt")):
                valid.append(os.path.join(root, name))
    return sorted(valid)


def process_all_schemes(input_dir: str, out_root: str, aux_map: Dict[str, str]):
    out_dir = os.path.join(out_root, "dicts-pro")
    os.makedirs(out_dir, exist_ok=True)

    files = collect_dict_files(input_dir)
    if not files:
        print(f"⚠ 输入目录中没有可处理的文件: {input_dir}")
        return

    print(f"共找到 {len(files)} 个文件待处理（TONE_MODE={TONE_MODE}）")
    for in_file in files:
        name = os.path.basename(in_file)
        rel_dir = os.path.relpath(os.path.dirname(in_file), input_dir)
        dest_dir = os.path.join(out_dir, rel_dir)
        os.makedirs(dest_dir, exist_ok=True)

        if is_blacklisted(name):
            shutil.copy2(in_file, os.path.join(dest_dir, name))
            print(f"⇢ 黑名单，原样复制: {name}")
            continue

        if has_userdb_marker(in_file):
            shutil.copy2(in_file, os.path.join(dest_dir, name))
            print(f"⚠ userdb 格式，原样复制: {name}")
            continue

        # 已含 .pro. 的文件不重复加后缀，避免 xxx.pro.pro.dict.yaml
        out_name = name if ".pro." in name else add_suffix_before_extensions(name, OUTPUT_SUFFIX)
        process_dict_file(in_file, os.path.join(dest_dir, out_name), aux_map)
        print(f"✓ 已处理: {name} → {os.path.relpath(os.path.join(dest_dir, out_name), out_root)}")


# ---------- 入口 ----------
def main():
    aux_map = load_aux_map(CSV_PATH)
    process_all_schemes(INPUT_DIR, OUT_ROOT, aux_map)
    print(f"\n✓ 全部完成（TONE_MODE={TONE_MODE}），输出目录:", os.path.join(OUT_ROOT, "dicts-pro"))


if __name__ == "__main__":
    main()
