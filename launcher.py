# -*- coding: utf-8 -*-
"""
自分専用 Windows ランチャー
- タスクバー（システムトレイ）に常駐
- クリックで登録済みコマンドの一覧を表示、選択して実行
- 「＋ 新規登録」から専用ダイアログで登録（メニュー名／複数行コマンド／PowerShell or cmd／引数最大3件）
- 引数欄に説明を入力して「挿入」を押すと、コマンド欄のカーソル位置に {ARG1}〜{ARG3} が挿入される
- 実行時、コマンドにプレースホルダーが含まれていれば、その場で値の入力を求めてから実行する
- 実行オプション: 作業フォルダ指定／「---」区切りでの別ウィンドウ並列起動／ウィンドウなしバックグラウンド実行

必要ライブラリ: pystray, pillow (pip install pystray pillow)
Windows専用（cmd.exe / powershell.exe / CREATE_NEW_CONSOLE を利用）
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import uuid

import tkinter as tk
from tkinter import messagebox

import pystray
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

APP_NAME = "自分専用ランチャー"
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_PATH = os.path.join(BASE_DIR, "commands.json")

PLACEHOLDERS = ["{ARG1}", "{ARG2}", "{ARG3}"]

# ---------------------------------------------------------------------------
# 設定ファイルの読み書き
# ---------------------------------------------------------------------------


def load_commands():
    if not os.path.exists(CONFIG_PATH):
        return []
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_commands(commands):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(commands, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# コマンド実行
# ---------------------------------------------------------------------------


def run_in_shell(command_text, shell, cwd=None, background=False):
    """一時スクリプトを作成して実行する。

    cwd: 作業フォルダ（空なら BASE_DIR）。
    background=False: 新規コンソールを開き、終了後も結果を残す（従来動作）。
    background=True: ウィンドウを作らず裏で実行する（サーバー常駐向け）。
    """
    workdir = cwd.strip() if isinstance(cwd, str) and cwd.strip() else BASE_DIR
    try:
        if shell == "powershell":
            fd, path = tempfile.mkstemp(suffix=".ps1")
            os.close(fd)
            # BOM付きUTF-8にしておくとPowerShellが日本語を正しく解釈しやすい
            with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
                f.write(command_text + "\r\n")
                if not background:
                    f.write("Write-Host ''\r\nWrite-Host '--- 実行終了 ---'\r\n")
            if background:
                args = [
                    "powershell.exe",
                    "-NoLogo",
                    "-WindowStyle",
                    "Hidden",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    path,
                ]
                flags = subprocess.CREATE_NO_WINDOW
            else:
                args = [
                    "powershell.exe",
                    "-NoLogo",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-NoExit",
                    "-File",
                    path,
                ]
                flags = subprocess.CREATE_NEW_CONSOLE
        else:
            fd, path = tempfile.mkstemp(suffix=".bat")
            os.close(fd)
            with open(path, "w", encoding="utf-8", newline="\r\n") as f:
                f.write("@echo off\r\n")
                f.write("chcp 65001 >nul\r\n")
                f.write(command_text + "\r\n")
                if not background:
                    f.write("echo.\r\necho --- 実行終了 ---\r\npause >nul\r\n")
            if background:
                args = ["cmd.exe", "/c", path]
                flags = subprocess.CREATE_NO_WINDOW
            else:
                args = ["cmd.exe", "/k", path]
                flags = subprocess.CREATE_NEW_CONSOLE

        subprocess.Popen(
            args,
            creationflags=flags,
            cwd=workdir,
        )
    except Exception as e:
        messagebox.showerror("実行エラー", str(e))


def split_blocks(command_text):
    """`---` のみの行を区切りとしてコマンドを分割する。区切りがなければ全体を1件として返す。"""
    parts = re.split(r"^[ \t]*---[ \t]*$", command_text, flags=re.MULTILINE)
    blocks = [p.strip() for p in parts]
    blocks = [b for b in blocks if b]
    return blocks if blocks else [command_text]


def used_placeholders(command_text):
    return [p for p in PLACEHOLDERS if p in command_text]


def execute_command(cmd_data):
    """tray側スレッドから呼ばれるので、必ずrootのメインスレッドで処理する。"""

    def _run():
        placeholders = used_placeholders(cmd_data["command"])
        values = {}
        if placeholders:
            values = ask_arguments(cmd_data, placeholders)
            if values is None:
                return  # キャンセルされた
        final_command = cmd_data["command"]
        for ph in placeholders:
            final_command = final_command.replace(ph, values.get(ph, ""))
        workdir = cmd_data.get("cwd", "") or BASE_DIR
        background = bool(cmd_data.get("background", False))
        if cmd_data.get("parallel", False):
            blocks = split_blocks(final_command)
            if len(blocks) > 1:
                for block in blocks:
                    run_in_shell(block, cmd_data["shell"], cwd=workdir, background=background)
                return
        run_in_shell(final_command, cmd_data["shell"], cwd=workdir, background=background)

    root.after(0, _run)


# ---------------------------------------------------------------------------
# 実行時: 引数入力ダイアログ
# ---------------------------------------------------------------------------


def ask_arguments(cmd_data, placeholders):
    result = {}
    cancelled = {"flag": True}

    dialog = tk.Toplevel(root)
    dialog.title(f"{cmd_data['name']} - 引数入力")
    dialog.resizable(False, False)
    dialog.grab_set()

    entries = {}
    arg_labels = cmd_data.get("args", ["", "", ""])
    for ph in placeholders:
        idx = PLACEHOLDERS.index(ph)
        label_text = arg_labels[idx] if idx < len(arg_labels) and arg_labels[idx] else f"引数{idx + 1}"
        row = tk.Frame(dialog)
        row.pack(fill="x", padx=12, pady=6)
        tk.Label(row, text=label_text, width=18, anchor="w").pack(side="left")
        entry = tk.Entry(row, width=35)
        entry.pack(side="left", padx=(4, 0))
        entries[ph] = entry

    if entries:
        list(entries.values())[0].focus_set()

    def on_ok(event=None):
        for ph, e in entries.items():
            result[ph] = e.get()
        cancelled["flag"] = False
        dialog.destroy()

    def on_cancel(event=None):
        dialog.destroy()

    btn_frame = tk.Frame(dialog)
    btn_frame.pack(pady=10)
    tk.Button(btn_frame, text="実行", width=10, command=on_ok).pack(side="left", padx=5)
    tk.Button(btn_frame, text="キャンセル", width=10, command=on_cancel).pack(side="left", padx=5)

    dialog.bind("<Return>", on_ok)
    dialog.bind("<Escape>", on_cancel)

    dialog.update_idletasks()
    dialog.geometry(f"+{root.winfo_screenwidth() // 2 - 150}+{root.winfo_screenheight() // 2 - 100}")

    dialog.wait_window()

    if cancelled["flag"]:
        return None
    return result


# ---------------------------------------------------------------------------
# 登録・編集ダイアログ
# ---------------------------------------------------------------------------


def open_register_dialog(edit_data=None):
    dialog = tk.Toplevel(root)
    dialog.title("コマンド編集" if edit_data else "新規コマンド登録")
    dialog.geometry("560x660")
    dialog.grab_set()

    tk.Label(dialog, text="メニュー名:").pack(anchor="w", padx=12, pady=(12, 0))
    name_entry = tk.Entry(dialog, width=60)
    name_entry.pack(padx=12, fill="x")

    tk.Label(dialog, text="実行コマンド（複数行可。引数を使う場所に {ARG1} 等を挿入）:").pack(
        anchor="w", padx=12, pady=(10, 0)
    )
    text_frame = tk.Frame(dialog)
    text_frame.pack(padx=12, fill="both", expand=True)
    command_text = tk.Text(text_frame, width=60, height=10, wrap="none")
    command_text.pack(side="left", fill="both", expand=True)
    scrollbar = tk.Scrollbar(text_frame, command=command_text.yview)
    scrollbar.pack(side="right", fill="y")
    command_text.configure(yscrollcommand=scrollbar.set)

    shell_var = tk.StringVar(value="powershell")
    shell_frame = tk.Frame(dialog)
    shell_frame.pack(anchor="w", padx=12, pady=(8, 0))
    tk.Label(shell_frame, text="実行シェル:").pack(side="left")
    tk.Radiobutton(shell_frame, text="PowerShell", variable=shell_var, value="powershell").pack(side="left")
    tk.Radiobutton(shell_frame, text="cmd", variable=shell_var, value="cmd").pack(side="left")

    args_frame = tk.LabelFrame(dialog, text="引数指定（最大3件・任意／説明を入力して「挿入」でコマンドに追加）")
    args_frame.pack(padx=12, pady=10, fill="x")

    arg_entries = []
    for i in range(3):
        row = tk.Frame(args_frame)
        row.pack(fill="x", pady=3, padx=6)
        tk.Label(row, text=f"引数{i + 1} 説明:", width=10, anchor="w").pack(side="left")
        entry = tk.Entry(row, width=28)
        entry.pack(side="left", padx=5)

        def make_insert(idx=i):
            def _insert():
                command_text.insert(tk.INSERT, PLACEHOLDERS[idx])
                command_text.focus_set()

            return _insert

        tk.Button(row, text="コマンドに挿入", command=make_insert()).pack(side="left")
        arg_entries.append(entry)

    opts_frame = tk.LabelFrame(dialog, text="実行オプション")
    opts_frame.pack(padx=12, pady=(0, 10), fill="x")

    cwd_row = tk.Frame(opts_frame)
    cwd_row.pack(fill="x", padx=6, pady=(6, 2))
    tk.Label(cwd_row, text="作業フォルダ:", width=10, anchor="w").pack(side="left")
    cwd_entry = tk.Entry(cwd_row, width=40)
    cwd_entry.pack(side="left", padx=5, fill="x", expand=True)
    tk.Label(opts_frame, text="※空欄ならランチャーと同じフォルダ。npm run 等は package.json のあるフォルダを指定。",
             anchor="w", fg="gray").pack(anchor="w", padx=6)

    parallel_var = tk.BooleanVar(value=False)
    background_var = tk.BooleanVar(value=False)
    tk.Checkbutton(opts_frame, text="「---」のみの行で分割し、別ウィンドウで並列起動する",
                   variable=parallel_var).pack(anchor="w", padx=6, pady=(4, 0))
    tk.Checkbutton(opts_frame, text="ウィンドウを表示せずバックグラウンドで実行する",
                   variable=background_var).pack(anchor="w", padx=6)
    tk.Label(opts_frame, text="例: 1ブロック目に npm run obsidian-bridge ／ 2ブロック目に n8n start を書き、間に --- の行を挟む",
             anchor="w", justify="left", fg="gray").pack(anchor="w", padx=6, pady=(0, 6))

    if edit_data:
        name_entry.insert(0, edit_data["name"])
        command_text.insert("1.0", edit_data["command"])
        shell_var.set(edit_data["shell"])
        existing_args = edit_data.get("args", ["", "", ""])
        for i in range(3):
            if i < len(existing_args) and existing_args[i]:
                arg_entries[i].insert(0, existing_args[i])
        if edit_data.get("cwd"):
            cwd_entry.insert(0, edit_data["cwd"])
        parallel_var.set(bool(edit_data.get("parallel", False)))
        background_var.set(bool(edit_data.get("background", False)))

    def on_save():
        name = name_entry.get().strip()
        cmd = command_text.get("1.0", "end-1c")
        if not name or not cmd.strip():
            messagebox.showwarning("入力エラー", "メニュー名と実行コマンドは必須です。", parent=dialog)
            return

        args = [e.get().strip() for e in arg_entries]
        cwd_value = cwd_entry.get().strip()
        parallel_value = bool(parallel_var.get())
        background_value = bool(background_var.get())
        commands = load_commands()

        if edit_data:
            for c in commands:
                if c["id"] == edit_data["id"]:
                    c.update({"name": name, "command": cmd, "shell": shell_var.get(), "args": args,
                              "cwd": cwd_value, "parallel": parallel_value, "background": background_value})
                    break
        else:
            commands.append(
                {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "command": cmd,
                    "shell": shell_var.get(),
                    "args": args,
                    "cwd": cwd_value,
                    "parallel": parallel_value,
                    "background": background_value,
                }
            )

        save_commands(commands)
        rebuild_menu()
        dialog.destroy()

    btn_frame = tk.Frame(dialog)
    btn_frame.pack(pady=10)
    tk.Button(btn_frame, text="保存", width=12, command=on_save).pack(side="left", padx=5)
    tk.Button(btn_frame, text="キャンセル", width=12, command=dialog.destroy).pack(side="left", padx=5)


# ---------------------------------------------------------------------------
# 管理（編集・削除）ダイアログ
# ---------------------------------------------------------------------------


def open_manage_dialog():
    dialog = tk.Toplevel(root)
    dialog.title("登録コマンドの管理")
    dialog.geometry("420x380")
    dialog.grab_set()

    tk.Label(dialog, text="登録済みコマンド:").pack(anchor="w", padx=12, pady=(10, 0))

    listbox = tk.Listbox(dialog, width=50)
    listbox.pack(padx=12, pady=10, fill="both", expand=True)

    commands = load_commands()
    for c in commands:
        listbox.insert("end", f"{c['name']}  [{c['shell']}]")

    def refresh_list(select=None):
        listbox.delete(0, "end")
        for c in commands:
            listbox.insert("end", f"{c['name']}  [{c['shell']}]")
        if select is not None:
            listbox.selection_set(select)
            listbox.see(select)

    def on_move(delta):
        sel = listbox.curselection()
        if not sel:
            messagebox.showinfo("選択なし", "移動するコマンドを選択してください。", parent=dialog)
            return
        idx = sel[0]
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(commands):
            return  # 先頭・末尾では何もしない
        commands[idx], commands[new_idx] = commands[new_idx], commands[idx]
        save_commands(commands)
        rebuild_menu()
        refresh_list(select=new_idx)

    def on_edit():
        sel = listbox.curselection()
        if not sel:
            messagebox.showinfo("選択なし", "編集するコマンドを選択してください。", parent=dialog)
            return
        edit_data = commands[sel[0]]
        dialog.destroy()
        open_register_dialog(edit_data=edit_data)

    def on_delete():
        sel = listbox.curselection()
        if not sel:
            messagebox.showinfo("選択なし", "削除するコマンドを選択してください。", parent=dialog)
            return
        target = commands[sel[0]]
        if messagebox.askyesno("削除確認", f"「{target['name']}」を削除しますか？", parent=dialog):
            remaining = [c for c in commands if c["id"] != target["id"]]
            save_commands(remaining)
            rebuild_menu()
            dialog.destroy()

    btn_frame = tk.Frame(dialog)
    btn_frame.pack(pady=10)
    tk.Button(btn_frame, text="編集", width=8, command=on_edit).pack(side="left", padx=3)
    tk.Button(btn_frame, text="▲ 上へ", width=8, command=lambda: on_move(-1)).pack(side="left", padx=3)
    tk.Button(btn_frame, text="▼ 下へ", width=8, command=lambda: on_move(1)).pack(side="left", padx=3)
    tk.Button(btn_frame, text="削除", width=8, command=on_delete).pack(side="left", padx=5)
    tk.Button(btn_frame, text="閉じる", width=8, command=dialog.destroy).pack(side="left", padx=5)


# ---------------------------------------------------------------------------
# トレイメニュー構築
# ---------------------------------------------------------------------------


def make_run_callback(cmd_data):
    def _cb(icon, item):
        execute_command(cmd_data)

    return _cb


def build_menu():
    commands = load_commands()
    items = []

    if commands:
        for c in commands:
            items.append(pystray.MenuItem(c["name"], make_run_callback(c)))
        items.append(pystray.Menu.SEPARATOR)
    else:
        items.append(pystray.MenuItem("(登録済みコマンドなし)", None, enabled=False))
        items.append(pystray.Menu.SEPARATOR)

    items.append(pystray.MenuItem("＋ 新規登録", lambda icon, item: root.after(0, open_register_dialog)))
    items.append(pystray.MenuItem("編集・削除", lambda icon, item: root.after(0, open_manage_dialog)))
    items.append(pystray.Menu.SEPARATOR)
    items.append(pystray.MenuItem("終了", on_quit))

    return pystray.Menu(*items)


def rebuild_menu():
    tray_icon.menu = build_menu()
    tray_icon.update_menu()


def on_quit(icon, item):
    icon.stop()
    root.after(0, root.quit)


def create_image():
    """トレイ用アイコンを描画で生成する。"""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, size - 4, size - 4], radius=12, fill="#1e1e2e", outline="#00d0ff", width=3)
    d.polygon([(24, 18), (24, 46), (46, 32)], fill="#00d0ff")  # 再生ボタン風の三角
    return img


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

root = tk.Tk()
root.withdraw()  # メインウィンドウは表示しない（ダイアログ表示用の裏方）

tray_icon = pystray.Icon("personal_launcher", create_image(), APP_NAME, menu=build_menu())


def start_tray():
    tray_icon.run()


if __name__ == "__main__":
    threading.Thread(target=start_tray, daemon=True).start()
    root.mainloop()
