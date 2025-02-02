"""
gui.py — X AutoReply Bot Desktop Panel  (light theme)
Launch: python main.py
"""
from __future__ import annotations

import asyncio
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Optional

# ── Async bridge ───────────────────────────────────────────────────────────────
_loop = asyncio.new_event_loop()

def _run_asyncio_loop_forever(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()

threading.Thread(
    target=_run_asyncio_loop_forever,
    args=(_loop,),
    daemon=True,
    name="GUIAsyncLoop",
).start()

# ── Live log buffer (thread-safe ring buffer) ──────────────────────────────────
import collections
_live_log_buffer: collections.deque = collections.deque(maxlen=500)
_live_log_callbacks: list = []

class _LiveLogSink:
    """Loguru sink — receives Message objects with .record attached."""
    def write(self, message):
        record = message.record
        level = record["level"].name
        t     = record["time"].strftime("%H:%M:%S")
        msg   = record["message"]
        name  = record["name"]
        color = {
            "SUCCESS": "#22c55e",
            "INFO":    "#60a5fa",
            "WARNING": "#f59e0b",
            "ERROR":   "#ef4444",
            "DEBUG":   "#94a3b8",
        }.get(level, "#e2e8f0")
        entry = (t, level, name, msg, color)
        _live_log_buffer.append(entry)
        for cb in list(_live_log_callbacks):
            try: cb(entry)
            except Exception: pass

    def __call__(self, message):
        self.write(message)

_live_log_sink = _LiveLogSink()

# Register our sink into loguru
from config import logger as _logger
_logger.add(_live_log_sink, format="{message}", level="DEBUG", enqueue=True)

def run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _loop)

def run_sync(coro):
    return run_async(coro).result(timeout=15)

# ── Palette (light) ────────────────────────────────────────────────────────────
C = {
    "bg":       "#F0F2F5",
    "surface":  "#FFFFFF",
    "border":   "#DDE1E7",
    "accent":   "#2563EB",
    "accent2":  "#EFF6FF",
    "green":    "#16A34A",
    "green2":   "#F0FDF4",
    "red":      "#DC2626",
    "red2":     "#FEF2F2",
    "yellow":   "#D97706",
    "yellow2":  "#FFFBEB",
    "text":     "#111827",
    "muted":    "#6B7280",
    "border2":  "#93C5FD",
    "tab_sel":  "#EFF6FF",
    "row_alt":  "#F9FAFB",
}

# ── Base widgets ───────────────────────────────────────────────────────────────

def _entry(parent, show="", width=0, **kw):
    """Proper Entry widget with working paste."""
    e = tk.Entry(parent,
                 font=("Segoe UI", 10),
                 fg=C["text"], bg=C["surface"],
                 insertbackground=C["text"],
                 relief="solid", bd=1,
                 highlightthickness=2,
                 highlightcolor=C["accent"],
                 highlightbackground=C["border"],
                 show=show, **kw)
    if width: e.config(width=width)
    return e

def _label(parent, text, size=10, bold=False, color=None, bg=None, **kw):
    return tk.Label(parent, text=text,
                    font=("Segoe UI", size, "bold" if bold else "normal"),
                    fg=color or C["text"],
                    bg=bg or C["surface"], **kw)

def _btn(parent, text, command=None, style="primary", width=None):
    styles = {
        "primary": (C["accent"],  "#FFFFFF"),
        "success": (C["green"],   "#FFFFFF"),
        "danger":  (C["red"],     "#FFFFFF"),
        "warning": (C["yellow"],  "#FFFFFF"),
        "ghost":   (C["border"],  C["text"]),
    }
    bg, fg = styles.get(style, styles["primary"])
    kw = dict(text=text, command=command,
              font=("Segoe UI", 9, "bold"),
              fg=fg, bg=bg,
              activebackground=bg, activeforeground=fg,
              relief="flat", bd=0, cursor="hand2",
              padx=14, pady=6)
    if width: kw["width"] = width
    b = tk.Button(parent, **kw)
    b.bind("<Enter>", lambda e: b.config(bg=_dim(bg, 20)))
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    return b

def _dim(h, amt=20):
    r,g,b = int(h[1:3],16), int(h[3:5],16), int(h[5:7],16)
    return f"#{max(0,r-amt):02x}{max(0,g-amt):02x}{max(0,b-amt):02x}"

def _sep(parent, orient="h"):
    f = tk.Frame(parent,
                 bg=C["border"],
                 height=1 if orient=="h" else 0,
                 width=0 if orient=="h" else 1)
    return f


# ── Main App ───────────────────────────────────────────────────────────────────

class XBotApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("X AutoReply Bot")
        self.geometry("1060x700")
        self.minsize(900, 600)
        self.configure(bg=C["bg"])
        try: run_sync(self._init_db())
        except Exception: pass
        self._tg_running = False
        self._apply_styles()
        self._build_ui()
        self._refresh_all()
        self.after(12000, self._auto_refresh)
        # Auto-start Telegram bot on launch
        self.after(1500, self._toggle_tg_bot)

    async def _init_db(self):
        from db import init_db
        await init_db()
        _logger.info("[GUI] БД инициализирована")

    def _apply_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Treeview",
                    background=C["surface"], foreground=C["text"],
                    fieldbackground=C["surface"], rowheight=30,
                    font=("Segoe UI", 9), borderwidth=0,
                    relief="flat")
        s.configure("Treeview.Heading",
                    background=C["bg"], foreground=C["muted"],
                    font=("Segoe UI", 9, "bold"), relief="flat",
                    borderwidth=0)
        s.map("Treeview",
              background=[("selected", C["accent2"])],
              foreground=[("selected", C["accent"])])
        s.configure("Vertical.TScrollbar",
                    background=C["border"], troughcolor=C["bg"],
                    arrowcolor=C["muted"], relief="flat")
        s.configure("TCombobox",
                    fieldbackground=C["surface"], background=C["surface"],
                    foreground=C["text"], selectbackground=C["accent2"])

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Top bar
        top = tk.Frame(self, bg=C["surface"], height=56)
        top.pack(fill="x")
        top.pack_propagate(False)
        _sep(top).pack(side="bottom", fill="x")

        tk.Label(top, text="⚡", font=("Segoe UI", 18),
                 fg=C["accent"], bg=C["surface"]).pack(side="left", padx=(16,4), pady=8)
        tk.Label(top, text="X AutoReply Bot",
                 font=("Segoe UI", 13, "bold"),
                 fg=C["text"], bg=C["surface"]).pack(side="left", pady=8)

        self._tg_btn = _btn(top, "▶  Start Telegram Bot",
                            command=self._toggle_tg_bot, style="success")
        self._tg_btn.pack(side="right", padx=16, pady=10)

        self._tg_dot = tk.Label(top, text="●  Telegram OFF",
                                font=("Segoe UI", 9),
                                fg=C["red"], bg=C["surface"])
        self._tg_dot.pack(side="right", padx=4)

        # Nav
        nav = tk.Frame(self, bg=C["surface"])
        nav.pack(fill="x")
        _sep(nav).pack(side="bottom", fill="x")

        self._pages: dict[str, tk.Frame] = {}
        self._tabs:  dict[str, tk.Label] = {}
        # Main area = top pages + bottom live log (resizable paned)
        self._paned = tk.PanedWindow(self, orient="vertical",
                                      bg=C["bg"], sashwidth=5,
                                      sashrelief="flat")
        self._paned.pack(fill="both", expand=True)

        self._container = tk.Frame(self._paned, bg=C["bg"])
        self._paned.add(self._container, minsize=300)

        # ── Live Console panel ──────────────────────────────────────────────
        self._console_frame = tk.Frame(self._paned, bg=C["surface"],
                                        highlightthickness=1,
                                        highlightbackground=C["border"])
        self._paned.add(self._console_frame, minsize=80)

        console_hdr = tk.Frame(self._console_frame, bg=C["accent"], height=28)
        console_hdr.pack(fill="x")
        console_hdr.pack_propagate(False)
        tk.Label(console_hdr, text="  📡  Live Console",
                 font=("Segoe UI", 9, "bold"),
                 fg="white", bg=C["accent"]).pack(side="left")
        _btn(console_hdr, "🗑 Clear",
             command=self._clear_console, style="ghost").pack(side="right", padx=4)

        # Level filter checkboxes
        self._console_filter = tk.StringVar(value="all")
        fbar = tk.Frame(self._console_frame, bg=C["surface"])
        fbar.pack(fill="x", padx=4, pady=2)
        for val, lbl, col in [
            ("all",     "ALL",     C["text"]),
            ("INFO",    "INFO",    "#60a5fa"),
            ("SUCCESS", "OK",      "#22c55e"),
            ("WARNING", "WARN",    "#f59e0b"),
            ("ERROR",   "ERROR",   "#ef4444"),
        ]:
            tk.Radiobutton(fbar, text=lbl, variable=self._console_filter,
                           value=val, command=self._redraw_console,
                           font=("Consolas", 8), fg=col, bg=C["surface"],
                           selectcolor=C["surface"],
                           activebackground=C["surface"],
                           cursor="hand2").pack(side="left", padx=4)

        self._console_auto = tk.BooleanVar(value=True)
        tk.Checkbutton(fbar, text="Auto-scroll",
                       variable=self._console_auto,
                       font=("Consolas", 8), fg=C["muted"], bg=C["surface"],
                       selectcolor=C["surface"],
                       activebackground=C["surface"]).pack(side="right", padx=8)

        self._console_txt = tk.Text(
            self._console_frame,
            font=("Consolas", 9),
            bg="#0f172a", fg="#e2e8f0",
            relief="flat", bd=0,
            wrap="word",
            state="disabled",
        )
        self._console_txt.pack(fill="both", expand=True, padx=0, pady=0)
        # Tag colours
        for lvl, col in [
            ("SUCCESS", "#22c55e"), ("INFO", "#60a5fa"),
            ("WARNING", "#f59e0b"), ("ERROR", "#ef4444"),
            ("DEBUG",   "#94a3b8"), ("TIME", "#64748b"),
            ("NAME",    "#818cf8"),
        ]:
            self._console_txt.tag_config(lvl, foreground=col)

        # Register live log callback
        _live_log_callbacks.append(self._on_log_entry)

        for name, builder in [
            ("Accounts", self._build_accounts),
            ("Settings",  self._build_settings),
            ("API Keys",  self._build_apikeys),
            ("Proxies",   self._build_proxies),
            ("Users",     self._build_users),
            ("Logs",      self._build_logs),
            ("Stats",     self._build_stats),
        ]:
            pg = tk.Frame(self._container, bg=C["bg"])
            self._pages[name] = pg
            builder(pg)
            t = tk.Label(nav, text=name,
                         font=("Segoe UI", 10), fg=C["muted"],
                         bg=C["surface"], padx=20, pady=12, cursor="hand2")
            t.bind("<Button-1>", lambda e, n=name: self._show_tab(n))
            t.pack(side="left")
            self._tabs[name] = t

        self._show_tab("Accounts")

    def _on_log_entry(self, entry):
        """Called from any thread when a new log line arrives."""
        self.after(0, self._append_console_entry, entry)

    def _append_console_entry(self, entry):
        t, level, name, msg, color = entry
        filt = self._console_filter.get()
        if filt != "all" and level != filt:
            return
        txt = self._console_txt
        txt.config(state="normal")
        txt.insert("end", t,   "TIME")
        txt.insert("end", f" {level:<8}", level)
        short_name = name.split(".")[-1][:12]
        txt.insert("end", f" [{short_name}] ", "NAME")
        txt.insert("end", msg + "\n")
        if self._console_auto.get():
            txt.see("end")
        # Trim to last 500 lines
        lines = int(txt.index("end-1c").split(".")[0])
        if lines > 500:
            txt.delete("1.0", f"{lines-500}.0")
        txt.config(state="disabled")

    def _redraw_console(self):
        """Redraw console with current filter from buffer."""
        txt = self._console_txt
        txt.config(state="normal")
        txt.delete("1.0", "end")
        filt = self._console_filter.get()
        for entry in _live_log_buffer:
            t, level, name, msg, color = entry
            if filt != "all" and level != filt:
                continue
            txt.insert("end", t,   "TIME")
            txt.insert("end", f" {level:<8}", level)
            short_name = name.split(".")[-1][:12]
            txt.insert("end", f" [{short_name}] ", "NAME")
            txt.insert("end", msg + "\n")
        txt.see("end")
        txt.config(state="disabled")

    def _clear_console(self):
        _live_log_buffer.clear()
        self._console_txt.config(state="normal")
        self._console_txt.delete("1.0", "end")
        self._console_txt.config(state="disabled")

    def _show_tab(self, name):
        for pg in self._pages.values(): pg.pack_forget()
        self._pages[name].pack(fill="both", expand=True)
        for n, t in self._tabs.items():
            if n == name:
                t.config(fg=C["accent"], bg=C["tab_sel"],
                         font=("Segoe UI", 10, "bold"))
            else:
                t.config(fg=C["muted"], bg=C["surface"],
                         font=("Segoe UI", 10))

    # ── Card helper ────────────────────────────────────────────────────────────

    def _card(self, parent, row=0, col=0, rowspan=1, colspan=1,
              padx=16, pady=8, sticky="nsew"):
        f = tk.Frame(parent, bg=C["surface"], bd=0,
                     highlightthickness=1,
                     highlightbackground=C["border"])
        f.grid(row=row, column=col, rowspan=rowspan, columnspan=colspan,
               padx=padx, pady=pady, sticky=sticky)
        return f

    def _toolbar(self, parent, title, row=0):
        bar = tk.Frame(parent, bg=C["bg"])
        bar.grid(row=row, column=0, sticky="ew", padx=16, pady=(14,4))
        _label(bar, title, size=13, bold=True, bg=C["bg"]).pack(side="left")
        return bar

    def _tree(self, parent, cols_spec):
        """cols_spec: list of (id, label, width)"""
        f = tk.Frame(parent, bg=C["surface"])
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(0, weight=1)
        cols = [c[0] for c in cols_spec]
        t = ttk.Treeview(f, columns=cols, show="headings")
        for cid, lbl, w in cols_spec:
            t.heading(cid, text=lbl)
            t.column(cid, width=w, anchor="center" if w < 250 else "w", minwidth=40)
        sb = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        return t

    # ── Accounts ───────────────────────────────────────────────────────────────

    def _build_accounts(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        bar = self._toolbar(pg, "Accounts")
        _btn(bar, "＋ Add Account", command=self._add_account_dialog,
             style="success").pack(side="right", padx=4)
        _btn(bar, "↻ Refresh", command=self._refresh_accounts,
             style="ghost").pack(side="right", padx=4)

        # Scrollable account cards container
        outer = self._card(pg, row=1)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, bg=C["surface"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self._acc_list_frame = tk.Frame(canvas, bg=C["surface"])
        self._acc_canvas_window = canvas.create_window((0, 0), window=self._acc_list_frame, anchor="nw")

        def _on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_configure(e):
            canvas.itemconfig(self._acc_canvas_window, width=e.width)
        self._acc_list_frame.bind("<Configure>", _on_frame_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))

        self._acc_row_widgets = {}  # acc_id -> {frame, toggle_btn, status_lbl, today_lbl}
        self._selected_acc_id = None

        # Column headers
        hdr = tk.Frame(self._acc_list_frame, bg=C["bg"])
        hdr.pack(fill="x", padx=8, pady=(8, 4))
        for txt, w in [("ID", 40), ("Username", 200), ("Today", 70), ("Status", 110)]:
            tk.Label(hdr, text=txt, font=("Segoe UI", 8, "bold"),
                     fg=C["muted"], bg=C["bg"], width=w//7, anchor="w").pack(side="left", padx=4)

    def _sel_acc_id(self):
        if not self._selected_acc_id:
            messagebox.showwarning("Select", "Select an account first", parent=self)
            return None
        return self._selected_acc_id

    def _refresh_accounts(self):
        async def _load():
            from db import get_accounts, get_daily_count
            try:
                from main import worker_manager
                running = set(worker_manager.running_accounts())
            except Exception: running = set()
            accs = await get_accounts(active_only=False)
            rows = []
            for a in accs:
                cnt = await get_daily_count(a["id"])
                rows.append({
                    "id": a["id"], "username": a["username"],
                    "today": cnt, "running": a["id"] in running,
                    "active": a["active"],
                })
            return rows

        def _done(fut):
            try:
                rows = fut.result()
            except Exception as e:
                print(f"[GUI] accounts: {e}")
                return

            # Remove rows for deleted accounts
            existing_ids = {r["id"] for r in rows}
            for aid in list(self._acc_row_widgets.keys()):
                if aid not in existing_ids:
                    self._acc_row_widgets[aid]["frame"].destroy()
                    del self._acc_row_widgets[aid]

            for i, row in enumerate(rows):
                acc_id   = row["id"]
                username = row["username"]
                today    = row["today"]
                running  = row["running"]

                if acc_id in self._acc_row_widgets:
                    # Just update dynamic labels + button
                    w = self._acc_row_widgets[acc_id]
                    w["today_lbl"].config(text=str(today))
                    w["status_lbl"].config(
                        text="🟢 Running" if running else "⚪ Stopped",
                        fg=C["green"] if running else C["muted"])
                    w["toggle_btn"].config(
                        text="⏹  Stop" if running else "▶  Start",
                        bg=C["yellow"] if running else C["green"])
                else:
                    # Build new row card
                    bg = C["surface"] if i % 2 == 0 else C["row_alt"]
                    frame = tk.Frame(self._acc_list_frame, bg=bg,
                                     highlightthickness=1,
                                     highlightbackground=C["border"])
                    frame.pack(fill="x", padx=8, pady=2)

                    # Click anywhere on frame to select
                    def _make_select(aid=acc_id, f=frame):
                        def _sel(e=None):
                            self._selected_acc_id = aid
                            for w2 in self._acc_row_widgets.values():
                                w2["frame"].config(highlightbackground=C["border"])
                            f.config(highlightbackground=C["accent"])
                            # auto-fill settings tab
                            if hasattr(self, "_sett_id"):
                                self._sett_id.delete(0, "end")
                                self._sett_id.insert(0, str(aid))
                        return _sel
                    select_fn = _make_select()
                    frame.bind("<Button-1>", select_fn)

                    inner = tk.Frame(frame, bg=bg)
                    inner.pack(fill="x", padx=12, pady=8)
                    inner.bind("<Button-1>", select_fn)

                    # ID
                    id_lbl = tk.Label(inner, text=str(acc_id),
                                      font=("Segoe UI", 9), fg=C["muted"],
                                      bg=bg, width=4, anchor="w")
                    id_lbl.pack(side="left", padx=(0, 8))
                    id_lbl.bind("<Button-1>", select_fn)

                    # Username
                    u_lbl = tk.Label(inner, text=f"@{username}",
                                     font=("Segoe UI", 10, "bold"),
                                     fg=C["text"], bg=bg, width=22, anchor="w")
                    u_lbl.pack(side="left", padx=(0, 12))
                    u_lbl.bind("<Button-1>", select_fn)

                    # Today count
                    today_lbl = tk.Label(inner, text=str(today),
                                         font=("Segoe UI", 9),
                                         fg=C["muted"], bg=bg, width=8, anchor="w")
                    today_lbl.pack(side="left", padx=(0, 8))
                    today_lbl.bind("<Button-1>", select_fn)

                    # Status
                    status_lbl = tk.Label(inner,
                                          text="🟢 Running" if running else "⚪ Stopped",
                                          font=("Segoe UI", 9),
                                          fg=C["green"] if running else C["muted"],
                                          bg=bg, width=12, anchor="w")
                    status_lbl.pack(side="left", padx=(0, 16))
                    status_lbl.bind("<Button-1>", select_fn)

                    # ── Toggle Start/Stop button ─────────────────────────────
                    def _make_toggle(aid=acc_id):
                        def _toggle():
                            self._selected_acc_id = aid
                            w = self._acc_row_widgets.get(aid)
                            if not w: return
                            is_running = w["status_lbl"].cget("text").startswith("🟢")
                            if is_running:
                                self._stop_worker()
                            else:
                                self._start_worker()
                        return _toggle

                    toggle_btn = _btn(inner,
                                      "⏹  Stop" if running else "▶  Start",
                                      command=_make_toggle(),
                                      style="warning" if running else "success")
                    toggle_btn.pack(side="left", padx=4)

                    # Test / Delete buttons
                    _btn(inner, "🔑 Test",   command=lambda aid=acc_id: self._test_session_id(aid),
                         style="primary").pack(side="left", padx=2)
                    _btn(inner, "⚙",         command=lambda aid=acc_id: self._open_settings_for(aid),
                         style="ghost").pack(side="left", padx=2)
                    _btn(inner, "🗑",         command=lambda aid=acc_id: self._delete_account_id(aid),
                         style="danger").pack(side="left", padx=2)

                    self._acc_row_widgets[acc_id] = {
                        "frame": frame, "toggle_btn": toggle_btn,
                        "status_lbl": status_lbl, "today_lbl": today_lbl,
                    }

        run_async(_load()).add_done_callback(lambda f: self.after(0, _done, f))

    def _add_account_dialog(self):
        win = tk.Toplevel(self)
        win.title("Add X Account")
        win.geometry("480x340")
        win.configure(bg=C["bg"])
        win.resizable(False, False)
        win.grab_set()
        win.focus_set()

        # Header
        hdr = tk.Frame(win, bg=C["accent"], height=50)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="  Add X Account",
                 font=("Segoe UI", 12, "bold"),
                 fg="white", bg=C["accent"]).pack(side="left", padx=16)

        body = tk.Frame(win, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=24, pady=16)

        _label(body, "Get cookies from:", size=9, color=C["muted"], bg=C["bg"]).pack(anchor="w")
        _label(body, "Chrome → F12 → Application → Cookies → x.com",
               size=9, bold=True, color=C["accent"], bg=C["bg"]).pack(anchor="w", pady=(0,12))

        def field(lbl, show=""):
            _label(body, lbl, size=9, color=C["muted"], bg=C["bg"]).pack(anchor="w")
            e = _entry(body, show=show)
            e.pack(fill="x", pady=(2,10), ipady=5)
            return e

        e_auth = field("auth_token", show="•")
        e_ct0  = field("ct0",        show="•")

        # Status
        sv = tk.StringVar()
