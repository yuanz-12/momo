#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多功能词库处理工具
────────────────────────────────────────────────────────
功能：
0. 刷辅助码且不会修改词库的拼音部分
1. 支持多个单字辅助码表
2. 生成带辅助码的词库（每个辅助码表对应一个输出目录）
3. 生成纯净拼音词库（去除辅助码）
4. 生成交换格式词库（拼音在前，汉字在后，txt格式）
"""

from __future__ import annotations
import os, re, shutil
from pathlib import Path
from typing import Dict, List, Tuple
from tqdm import tqdm

# ──────────────── 配 置 区 ─────────────────
# ========= GitHub Actions 环境配置 =========
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # 自动获取脚本所在目录

INPUT_PATH = os.path.join(BASE_DIR, "input_dicts")      # 下载解压后的输入目录
AUX_TABLES_DIR = os.path.join(BASE_DIR, "辅助码表")    # 你仓库里的依赖文件夹
OUTPUT_ROOT = os.path.join(BASE_DIR, "output_dicts")   # 输出目录（自动生成）

# ───────────────────────────────────────

# 全局配置
AUX_SEP_REGEX = r'[;\[]'
yaml_heads = ('---', 'name:', 'version:', 'sort:', '...')
SKIP_FILES = {'compatible.dict.yaml', 'corrections.dict.yaml', 'people.dict.yaml', 'encnnum.dict.yaml'}

def is_dir_like(p: str) -> bool:
    """判断路径是否像目录"""
    return (p.endswith(('/', '\\')) or 
            os.path.isdir(p) or 
            not os.path.splitext(p)[1])

def create_output_dirs():
    """创建输出目录结构"""
    Path(OUTPUT_ROOT).mkdir(parents=True, exist_ok=True)
    
    # 纯净词库目录
    pure_dir = os.path.join(OUTPUT_ROOT, "纯净词库")
    Path(pure_dir).mkdir(exist_ok=True)
    
    # 交换格式词库目录
    swapped_dir = os.path.join(OUTPUT_ROOT, "交换格式词库")
    Path(swapped_dir).mkdir(exist_ok=True)
    
    # 带辅助码词库目录
    with_aux_dir = os.path.join(OUTPUT_ROOT, "带辅助码词库")
    Path(with_aux_dir).mkdir(exist_ok=True)
    
    return pure_dir, swapped_dir, with_aux_dir

def load_aux_tables() -> Dict[str, Dict[str, str]]:
    """加载所有辅助码表"""
    aux_tables = {}
    print(f"扫描辅助码表目录: {AUX_TABLES_DIR}")
    
    for filename in os.listdir(AUX_TABLES_DIR):
        if not filename.endswith(('.yaml', '.txt')):
            continue
            
        table_name = os.path.splitext(filename)[0]
        filepath = os.path.join(AUX_TABLES_DIR, filename)
        
        if not os.path.isfile(filepath):
            continue
            
        aux_map = {}
        with open(filepath, encoding='utf-8') as f:
            for line in f:
                if not line.strip() or line.startswith('#') or line.startswith('---'):
                    continue
                    
                parts = line.rstrip('\n').split('\t')
                if len(parts) < 2 or len(parts[0]) != 1:
                    continue
                    
                char, seg = parts[:2]
                if ';' in seg:
                    aux_code = seg.split(';', 1)[1]
                else:
                    aux_code = seg
                    
                aux_map[char] = aux_code
        
        aux_tables[table_name] = aux_map
        print(f"✓ 加载辅助码表 [{table_name}]: {len(aux_map)} 条")
    
    if not aux_tables:
        print("⚠ 警告: 未找到任何辅助码表文件")
        
    return aux_tables

def clean_aux_from_seg(seg: str) -> str:
    """从单个拼音段中移除辅助码"""
    parts = re.split(AUX_SEP_REGEX, seg, 1)
    return parts[0]  # 只返回拼音部分

def is_userdb_head(line: str) -> bool:
    """检测是否是Rime用户词典头"""
    return '#@/db_type\tuserdb' in line or '# Rime user dictionary' in line

def process_line_for_pure(line: str, userdb: bool) -> Tuple[str, bool]:
    """处理行以生成纯净词库"""
    # 透传 YAML/注释
    if line.startswith(yaml_heads) or line.startswith('#'):
        return line, userdb
        
    if not line.strip():
        return line, userdb
        
    cols = line.split('\t')
    word = cols[1] if userdb and len(cols) > 1 else cols[0]
    
    # 移除辅助码
    if userdb and len(cols) > 0:
        segs = cols[0].split()
        cleaned_segs = [clean_aux_from_seg(seg) for seg in segs]
        cols[0] = ' '.join(cleaned_segs)
    elif len(cols) > 1:
        segs = cols[1].split()
        cleaned_segs = [clean_aux_from_seg(seg) for seg in segs]
        cols[1] = ' '.join(cleaned_segs)
    
    return '\t'.join(cols), userdb

def process_line_for_aux(line: str, aux_map: Dict[str, str], userdb: bool) -> Tuple[str, bool]:
    """处理行以添加辅助码"""
    # 透传 YAML/注释
    if line.startswith(yaml_heads) or line.startswith('#'):
        return line, userdb
        
    if not line.strip():
        return line, userdb
        
    cols = line.split('\t')
    word = cols[1] if userdb and len(cols) > 1 else cols[0]
    
    # 添加辅助码
    if userdb and len(cols) > 0:
        segs = cols[0].split()
        for i, ch in enumerate(word):
            if i < len(segs) and ch in aux_map:
                # 保留原拼音，添加辅助码
                py_part = clean_aux_from_seg(segs[i])
                segs[i] = f"{py_part};{aux_map[ch]}"
        cols[0] = ' '.join(segs)
    elif len(cols) > 1:
        segs = cols[1].split()
        for i, ch in enumerate(word):
            if i < len(segs) and ch in aux_map:
                # 保留原拼音，添加辅助码
                py_part = clean_aux_from_seg(segs[i])
                segs[i] = f"{py_part};{aux_map[ch]}"
        cols[1] = ' '.join(segs)
    
    return '\t'.join(cols), userdb

def process_line_for_swapped(line: str, userdb: bool) -> Tuple[str, bool]:
    """处理行以生成交换格式"""
    # 跳过注释和YAML头
    if line.startswith(yaml_heads) or line.startswith('#'):
        return "", userdb
        
    if not line.strip():
        return "", userdb
        
    cols = line.split('\t')
    if len(cols) < 2:
        return "", userdb
        
    # 提取拼音和汉字
    if userdb:
        # UserDB格式: 拼音\t汉字
        py_str = cols[0]
        word = cols[1]
    else:
        # 普通格式: 汉字\t拼音
        word = cols[0]
        py_str = cols[1]
    
    # 清理拼音中的辅助码
    py_segs = py_str.split()
    cleaned_py = ' '.join([clean_aux_from_seg(seg) for seg in py_segs])
    
    # 生成交换格式: 拼音\t汉字
    return f"{cleaned_py}\t{word}", userdb

def process_file(src: str, dest: str, process_func, aux_map=None):
    """处理单个文件"""
    userdb = False
    with open(src, encoding='utf-8') as s, open(dest, 'w', encoding='utf-8') as d:
        for raw in s:
            line = raw.rstrip('\n')
            
            # 更新userdb状态
            if is_userdb_head(line):
                userdb = True
                
            # 处理行
            if aux_map:
                processed_line, userdb = process_func(line, aux_map, userdb)
            else:
                processed_line, userdb = process_func(line, userdb)
                
            if processed_line:
                d.write(processed_line + '\n')

def process_directory(input_path: str, output_dir: str, process_func, aux_map=None):
    """处理目录中的所有文件"""
    tasks = []
    for root, _, files in os.walk(input_path):
        for fn in files:
            if not fn.endswith(('.txt', '.yaml')) or fn in SKIP_FILES:
                continue
                
            rel_path = os.path.relpath(root, input_path)
            dest_dir = os.path.join(output_dir, rel_path)
            Path(dest_dir).mkdir(parents=True, exist_ok=True)
            
            src_file = os.path.join(root, fn)
            dest_file = os.path.join(dest_dir, fn)
            
            tasks.append((src_file, dest_file))
    
    if not tasks:
        print(f"⚠ 警告: 在目录 {input_path} 中未找到可处理的文件")
        return
        
    bar = tqdm(tasks, desc="处理文件", unit="file", ncols=90)
    for src, dest in bar:
        bar.set_postfix(file=os.path.basename(src))
        process_file(src, dest, process_func, aux_map)
        tqdm.write(f"✓ 完成 {os.path.basename(src)} → {os.path.relpath(dest, output_dir)}")

def generate_pure_pinyin(input_path: str, pure_dir: str):
    """生成纯净拼音词库"""
    print("\n" + "="*50)
    print("步骤1: 生成纯净拼音词库")
    print("="*50)
    
    if os.path.isfile(input_path):
        # 处理单文件
        dest = os.path.join(pure_dir, os.path.basename(input_path))
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        process_file(input_path, dest, process_line_for_pure)
        print(f"✓ 完成 {os.path.basename(input_path)} → {dest}")
    else:
        # 处理目录
        process_directory(input_path, pure_dir, process_line_for_pure)

def generate_aux_pinyin(pure_dir: str, with_aux_dir: str, aux_tables: Dict[str, Dict[str, str]]):
    """生成带辅助码的词库"""
    if not aux_tables:
        print("⚠ 跳过辅助码添加: 没有可用的辅助码表")
        return
        
    print("\n" + "="*50)
    print("步骤2: 生成带辅助码的词库")
    print("="*50)
    
    for table_name, aux_map in aux_tables.items():
        print(f"\n▷ 处理辅助码表: {table_name}")
        table_output_dir = os.path.join(with_aux_dir, table_name)
        Path(table_output_dir).mkdir(parents=True, exist_ok=True)
        process_directory(pure_dir, table_output_dir, process_line_for_aux, aux_map)

def generate_swapped_pinyin(pure_dir: str, swapped_dir: str):
    """生成交换格式词库"""
    print("\n" + "="*50)
    print("步骤3: 生成交换格式词库")
    print("="*50)
    
    # 转换所有文件为交换格式
    tasks = []
    for root, _, files in os.walk(pure_dir):
        for fn in files:
            if not fn.endswith(('.txt', '.yaml')) or fn in SKIP_FILES:
                continue
                
            # 转换为.txt格式
            new_fn = os.path.splitext(fn)[0] + ".txt"
            rel_path = os.path.relpath(root, pure_dir)
            dest_dir = os.path.join(swapped_dir, rel_path)
            Path(dest_dir).mkdir(parents=True, exist_ok=True)
            
            src_file = os.path.join(root, fn)
            dest_file = os.path.join(dest_dir, new_fn)
            
            tasks.append((src_file, dest_file))
    
    if not tasks:
        print("⚠ 警告: 没有文件可转换为交换格式")
        return
        
    bar = tqdm(tasks, desc="生成交换格式", unit="file", ncols=90)
    for src, dest in bar:
        bar.set_postfix(file=os.path.basename(src))
        process_file(src, dest, process_line_for_swapped)
        tqdm.write(f"✓ 完成 {os.path.basename(src)} → {os.path.relpath(dest, swapped_dir)}")

# ---------- 主入口 ----------
if __name__ == "__main__":
    # 创建输出目录
    pure_dir, swapped_dir, with_aux_dir = create_output_dirs()
    
    # 加载所有辅助码表
    aux_tables = load_aux_tables()
    
    # 执行三步处理
    generate_pure_pinyin(INPUT_PATH, pure_dir)
    generate_aux_pinyin(pure_dir, with_aux_dir, aux_tables)
    generate_swapped_pinyin(pure_dir, swapped_dir)
    
    print("\n" + "="*50)
    print("✓ 所有处理完成！")
    print(f"• 纯净词库: {pure_dir}")
    print(f"• 交换格式词库: {swapped_dir}")
    
    if aux_tables:
        print(f"• 带辅助码词库: {with_aux_dir}")
        for table_name in aux_tables.keys():
            print(f"  - {table_name}: {os.path.join(with_aux_dir, table_name)}")
    
    print("="*50)
