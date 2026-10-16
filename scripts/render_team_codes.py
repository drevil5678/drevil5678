#!/usr/bin/env python3
"""Reproduce printable team cards and the CSV shipped in the Windows package."""
import argparse
import csv
import io
import re
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.initial_roster import INITIAL_CODES, INITIAL_ROSTER


def read_roster():
    source = (ROOT / "config" / "team-codes.csv").read_bytes()
    reader = csv.DictReader(io.StringIO(source.decode("utf-8-sig")))
    fields = ["隊長姓名", "隊員姓名", "隊名", "點棋碼"]
    if reader.fieldnames != fields:
        raise ValueError("點棋碼 CSV 欄位與預期不同")
    rows = list(reader)
    expected = [(*team, code) for team, code in zip(INITIAL_ROSTER, INITIAL_CODES)]
    actual = [tuple(row.get(field, "") for field in fields) for row in rows]
    if len(rows) != 18 or actual != expected:
        raise ValueError("點棋碼 CSV 必須與程式中的 18 隊名稱、姓名、順序與固定碼完全一致")
    codes = [row["點棋碼"] for row in rows]
    if len(set(codes)) != 18 or any(not re.fullmatch(r"[A-HJ-NP-Z2-9]{4}(?:-[A-HJ-NP-Z2-9]{4}){2}", code) for code in codes):
        raise ValueError("點棋碼不可重複，且必須使用 XXXX-XXXX-XXXX 格式")
    return source, rows


def render_html(rows):
    pages = []
    for offset in range(0, len(rows), 9):
        cards = []
        for number, row in enumerate(rows[offset:offset + 9], offset + 1):
            captain, member, name, code = (escape(row[field], quote=True) for field in
                                          ("隊長姓名", "隊員姓名", "隊名", "點棋碼"))
            cards.append(f'''    <article class="card" aria-label="第 {number:02d} 隊 {name}">
      <header><span>楊海分站賽 · 隊伍點棋碼</span><span>第 {number:02d} 隊</span></header>
      <h2>{name}</h2>
      <p class="players"><span>隊長：{captain}</span><span>隊友：{member}</span></p>
      <p class="code">{code}</p>
      <p class="hint">進入賽場網頁，輸入此碼查看及操作本隊棋局。</p>
    </article>''')
        pages.append('  <section class="page" aria-label="點棋碼列印頁">\n' + "\n".join(cards) + "\n  </section>")
    return ('''<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>楊海分站賽 · 18 隊點棋碼列印卡</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; background: #ecefea; color: #17251d; font-family: "Microsoft JhengHei", "Noto Sans TC", sans-serif; }
    .toolbar { max-width: 1100px; margin: 20px auto; padding: 0 16px; }
    .toolbar h1 { margin: 0 0 8px; font-size: 24px; }
    .toolbar p { margin: 8px 0; line-height: 1.6; }
    button { padding: 10px 24px; font: inherit; border: 0; border-radius: 6px; background: #215d3e; color: white; cursor: pointer; }
    .page { width: 277mm; height: 190mm; margin: 18px auto; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); grid-template-rows: repeat(3, minmax(0, 1fr)); gap: 5mm; }
    .card { display: flex; flex-direction: column; justify-content: center; min-width: 0; padding: 4mm; border: 1px dashed #69796d; background: white; break-inside: avoid; page-break-inside: avoid; }
    .card header { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; color: #3f5145; }
    .card h2 { margin: 3mm 0 2mm; font-size: 19px; line-height: 1.35; overflow-wrap: anywhere; }
    .players { display: flex; flex-wrap: wrap; gap: 3px 16px; margin: 0; font-size: 13px; line-height: 1.5; }
    .code { margin: 3mm 0 1mm; font: bold 22px/1.3 Consolas, "Courier New", monospace; white-space: nowrap; letter-spacing: .4px; }
    .hint { margin: 0; font-size: 10px; line-height: 1.5; }
    @page { size: A4 landscape; margin: 10mm; }
    @media print {
      body { background: white; color: black; }
      .toolbar { display: none; }
      .page { margin: 0; break-after: page; page-break-after: always; }
      .page:last-child { break-after: auto; page-break-after: auto; }
      .card { border-color: #666; }
    }
  </style>
</head>
<body>
  <div class="toolbar">
    <h1>18 隊點棋碼列印卡</h1>
    <p>使用 A4 橫向、100% 比例列印，共 2 頁、每頁 9 張。沿虛線裁切後，每隊發一張。</p>
    <button type="button" onclick="window.print()">列印點棋碼</button>
  </div>
''' + "\n".join(pages) + "\n</body>\n</html>\n").encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the committed files without changing them")
    args = parser.parse_args()
    source, rows = read_roster()
    outputs = {"TEAM-CODES.csv": source, "TEAM-CODES.html": render_html(rows)}
    for name, content in outputs.items():
        path = ROOT / name
        if args.check:
            if not path.exists() or path.read_bytes() != content:
                raise SystemExit(f"{name} 與原始名單不同，請執行 python scripts/render_team_codes.py 更新")
        else:
            path.write_bytes(content)
    print("18 隊姓名、隊名、順序及點棋碼完全一致；TEAM-CODES.csv 與列印卡" + ("驗證通過。" if args.check else "已產生。"))


if __name__ == "__main__":
    main()
