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
        tk.Label(body, textvariable=sv, font=("Segoe UI", 9),
                 fg=C["red"], bg=C["bg"]).pack(anchor="w")

        btn_frame = tk.Frame(body, bg=C["bg"])
        btn_frame.pack(fill="x", pady=(8,0))

        def do_add():
            auth = e_auth.get().strip()
            ct0  = e_ct0.get().strip()
            if not auth or not ct0:
                sv.set("⚠  Fill both fields"); return
            sv.set("⏳  Verifying session...")
            win.update()

            async def _add():
                from config import BotDefaults, encrypt
                from db import add_account, set_setting
                from twitter import TwitterClient
                ae, ce = encrypt(auth), encrypt(ct0)
                async with TwitterClient(0, ae, ce) as c:
                    uname = await c.verify_session()
                if not uname:
                    _logger.warning("[GUI] Добавление аккаунта: сессия недействительна")
                    return None
                aid = await add_account(uname, ae, ce)
                for k, v in [
                    ("search_mode", BotDefaults.search_mode),
                    ("min_likes", BotDefaults.min_likes),
                    ("min_retweets", BotDefaults.min_retweets),
                    ("max_age_min", BotDefaults.max_post_age_minutes),
                    ("comment_sort", BotDefaults.comment_sort),
                    ("reply_mode", "hybrid"),
                    ("auto_publish", BotDefaults.auto_publish),
                    ("min_delay", BotDefaults.min_delay_seconds),
                    ("max_delay", BotDefaults.max_delay_seconds),
                    ("daily_limit", BotDefaults.daily_comment_limit),
                    ("system_prompt", BotDefaults.system_prompt),
                    ("auto_start", False),
                ]: await set_setting(aid, k, v)
                _logger.success(f"[GUI] ✅ Аккаунт @{uname} добавлен (id={aid})")
                return uname

            def _done(fut):
                try:
                    uname = fut.result()
                    if uname:
                        win.destroy()
                        self._refresh_accounts()
                        messagebox.showinfo("Success", f"✅  @{uname} added!", parent=self)
                    else:
                        sv.set("❌  Session invalid — check cookies")
                except Exception as ex:
                    _logger.error(f"[GUI] Ошибка добавления аккаунта: {ex}")
                    sv.set(f"❌  {ex}")
            run_async(_add()).add_done_callback(lambda f: self.after(0, _done, f))

        _btn(btn_frame, "Add Account", command=do_add, style="success").pack(side="left")
        _btn(btn_frame, "Cancel", command=win.destroy, style="ghost").pack(side="left", padx=8)

    def _start_worker(self):
        acc_id = self._sel_acc_id()
        if not acc_id: return
        _logger.info(f"[GUI] Запуск воркера acc_id={acc_id}")
        async def _go():
            from main import worker_manager
            return await worker_manager.start(acc_id)
        def _done(fut):
            try:
                ok = fut.result()
                self._refresh_accounts()
                if ok:
                    _logger.success(f"[GUI] ✅ Воркер acc_id={acc_id} запущен")
                else:
                    _logger.info(f"[GUI] Воркер acc_id={acc_id} уже запущен")
                    messagebox.showinfo("Info", f"Worker {acc_id} already running", parent=self)
            except Exception as e:
                _logger.error(f"[GUI] Ошибка запуска воркера acc_id={acc_id}: {e}")
                messagebox.showerror("Error", str(e), parent=self)
        run_async(_go()).add_done_callback(lambda f: self.after(0, _done, f))

    def _stop_worker(self):
        acc_id = self._sel_acc_id()
        if not acc_id: return
        _logger.info(f"[GUI] Остановка воркера acc_id={acc_id}")
        async def _go():
            from main import worker_manager
            return await worker_manager.stop(acc_id)
        def _done(fut):
            try:
                fut.result()
                self._refresh_accounts()
                _logger.info(f"[GUI] Воркер acc_id={acc_id} остановлен")
            except Exception as e:
                _logger.error(f"[GUI] Ошибка остановки воркера acc_id={acc_id}: {e}")
                messagebox.showerror("Error", str(e), parent=self)
        run_async(_go()).add_done_callback(lambda f: self.after(0, _done, f))

    def _test_session(self):
        acc_id = self._sel_acc_id()
        if not acc_id: return
        _logger.info(f"[GUI] Проверка сессии acc_id={acc_id}")
        async def _go():
            from db import get_account
            from twitter import TwitterClient
            acc = await get_account(acc_id)
            if not acc: return None
            async with TwitterClient(acc_id, acc["auth_token"], acc["ct0"]) as c:
                return await c.verify_session()
        def _done(fut):
            try:
                uname = fut.result()
                if uname:
                    _logger.success(f"[GUI] ✅ Сессия acc_id={acc_id} валидна — @{uname}")
                    messagebox.showinfo("Valid", f"✅  Session OK — @{uname}", parent=self)
                else:
                    _logger.warning(f"[GUI] ❌ Сессия acc_id={acc_id} недействительна")
                    messagebox.showerror("Invalid", "❌  Session invalid — re-add account", parent=self)
            except Exception as e:
                _logger.error(f"[GUI] Ошибка проверки сессии acc_id={acc_id}: {e}")
                messagebox.showerror("Error", str(e), parent=self)
        run_async(_go()).add_done_callback(lambda f: self.after(0, _done, f))

    def _reset_daily(self):
        acc_id = self._sel_acc_id()
        if not acc_id: return
        if not messagebox.askyesno("Confirm", f"Reset daily counter for account {acc_id}?", parent=self): return
        async def _go():
            from datetime import datetime, timezone
            from db import execute
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            await execute("DELETE FROM daily_stats WHERE account_id=? AND date=?", (acc_id, today))
        def _done(fut):
            try:
                fut.result()
                self._refresh_accounts()
                _logger.info(f"[GUI] Сброс дневного счётчика acc_id={acc_id}")
            except Exception as e:
                _logger.error(f"[GUI] Ошибка сброса счётчика acc_id={acc_id}: {e}")
                messagebox.showerror("Error", str(e), parent=self)
        run_async(_go()).add_done_callback(lambda f: self.after(0, _done, f))

    def _delete_account(self):
        acc_id = self._sel_acc_id()
        if not acc_id: return
        if not messagebox.askyesno("Confirm", f"Delete account {acc_id}?\nThis cannot be undone.", parent=self): return
        async def _go():
            from db import delete_account
            await delete_account(acc_id)
        def _done(fut):
            try:
                fut.result()
                self._refresh_accounts()
                _logger.warning(f"[GUI] 🗑 Аккаунт acc_id={acc_id} удалён")
            except Exception as e:
                _logger.error(f"[GUI] Ошибка удаления аккаунта acc_id={acc_id}: {e}")
                messagebox.showerror("Error", str(e), parent=self)
        run_async(_go()).add_done_callback(lambda f: self.after(0, _done, f))

    def _on_acc_select(self, event=None):
        pass  # selection now handled per-row in _refresh_accounts

    def _test_session_id(self, acc_id):
        self._selected_acc_id = acc_id
        self._test_session()

    def _open_settings_for(self, acc_id):
        self._selected_acc_id = acc_id
        if hasattr(self, "_sett_id"):
            self._sett_id.delete(0, "end")
            self._sett_id.insert(0, str(acc_id))
            self._load_settings()
        # Switch to Settings tab
        try:
            self._show_tab("Settings")
        except Exception:
            pass

    def _delete_account_id(self, acc_id):
        self._selected_acc_id = acc_id
        self._delete_account()

        # ── Settings ───────────────────────────────────────────────────────────────

    def _build_settings(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        bar = self._toolbar(pg, "Account Settings")
        _btn(bar, "💾  Save", command=self._save_settings, style="success").pack(side="right", padx=4)
        _btn(bar, "📂  Load", command=self._load_settings, style="primary").pack(side="right", padx=4)
        _btn(bar, "🧪  Test Run", command=self._test_run, style="warning").pack(side="right", padx=4)
        _label(bar, "Account ID:", size=9, color=C["muted"], bg=C["bg"]).pack(side="right", padx=(12,4))
        self._sett_id = _entry(bar, width=5)
        self._sett_id.pack(side="right", padx=(0,4), ipady=3)
        self._sett_loaded_lbl = tk.Label(bar, text="← click account to load",
                                          font=("Segoe UI", 8, "italic"),
                                          fg=C["muted"], bg=C["bg"])
        self._sett_loaded_lbl.pack(side="left", padx=(8,0))

        card = self._card(pg, row=1)
        card.columnconfigure(1, weight=1)
        card.columnconfigure(3, weight=1)

        self._sv: dict[str, tk.Variable] = {}

        def field(r, c, lbl, key, wtype="entry", opts=None):
            tk.Label(card, text=lbl, font=("Segoe UI", 9),
                     fg=C["muted"], bg=C["surface"],
                     anchor="e").grid(row=r, column=c*2, sticky="e",
                                      padx=(16,6), pady=6)
            if wtype == "combo":
                v = tk.StringVar()
                w = ttk.Combobox(card, textvariable=v, values=opts,
                                 state="readonly", width=18,
                                 font=("Segoe UI", 9))
                w.grid(row=r, column=c*2+1, sticky="w", padx=(0,20), pady=6)
            elif wtype == "check":
                v = tk.BooleanVar()
                w = tk.Checkbutton(card, variable=v, bg=C["surface"],
                                   fg=C["text"], selectcolor=C["surface"],
                                   activebackground=C["surface"],
                                   cursor="hand2")
                w.grid(row=r, column=c*2+1, sticky="w", padx=(0,20), pady=6)
            else:
                v = tk.StringVar()
                w = _entry(card, width=18)
                w.config(textvariable=v)
                w.grid(row=r, column=c*2+1, sticky="ew", padx=(0,20), pady=6)
            self._sv[key] = v

        field(0,0,"Search Mode",  "search_mode", "combo", ["keywords","list","recommendations"])
        field(1,0,"Min Likes",    "min_likes")
        field(2,0,"Min Retweets", "min_retweets")
        field(3,0,"Max Age (min)","max_age_min")
        field(4,0,"Comment Sort", "comment_sort", "combo", ["likes","views"])
        field(5,0,"Reply Mode",   "reply_mode",   "combo", ["hybrid","post_only"])
        field(6,0,"AI Provider",  "ai_provider",  "combo", ["openai","gemini","perplexity","groq"])
        field(0,1,"Auto Publish", "auto_publish", "check")
        field(1,1,"Auto Start",   "auto_start",   "check")
        field(2,1,"Delay (min) ±5m","min_delay_min")
        field(3,1,"Daily Limit",  "daily_limit")

        def textarea(r, lbl, hint=""):
            tk.Label(card, text=lbl, font=("Segoe UI",9),
                     fg=C["muted"], bg=C["surface"],
                     anchor="ne").grid(row=r, column=0, sticky="ne",
                                       padx=(16,6), pady=6)
            if hint:
                tk.Label(card, text=hint, font=("Segoe UI",8),
                         fg=C["muted"], bg=C["surface"]).grid(
                    row=r+1, column=1, columnspan=3, sticky="w", padx=(0,16))
            t = tk.Text(card, height=4, font=("Segoe UI",9),
                        fg=C["text"], bg=C["bg"],
                        insertbackground=C["text"],
                        relief="solid", bd=1,
                        highlightthickness=1,
                        highlightcolor=C["accent"],
                        highlightbackground=C["border"])
            t.grid(row=r, column=1, columnspan=3,
                   sticky="ew", padx=(0,16), pady=6)
            return t

        self._prompt_txt = textarea(6, "System Prompt")
        self._kw_txt     = textarea(7, "Keywords",  "one per line")
        self._list_txt   = textarea(8, "X Lists",   "one URL per line")

    def _test_run(self):
        """Run one full search+generate cycle without posting. Shows result in a popup."""
        try:
            acc_id = int(self._sett_id.get())
        except ValueError:
            messagebox.showwarning("Input", "Load an account first (enter ID and click Load)", parent=self)
            return

        # Progress window
        win = tk.Toplevel(self)
        win.title("🧪 Test Run")
        win.geometry("620x500")
        win.configure(bg=C["bg"])
        win.resizable(True, True)
        win.grab_set()

        hdr = tk.Frame(win, bg=C["accent"], height=44)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=f"  🧪 Test Run — Account {acc_id}",
                 font=("Segoe UI", 11, "bold"),
                 fg="white", bg=C["accent"]).pack(side="left", padx=16)

        body = tk.Frame(win, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=16, pady=12)

        status_var = tk.StringVar(value="⏳ Starting test...")
        tk.Label(body, textvariable=status_var,
                 font=("Segoe UI", 9, "bold"),
                 fg=C["accent"], bg=C["bg"]).pack(anchor="w", pady=(0, 8))

        txt = tk.Text(body, font=("Segoe UI", 9),
                      fg=C["text"], bg=C["surface"],
                      relief="solid", bd=1, wrap="word",
                      state="disabled")
        txt.pack(fill="both", expand=True)

        def append(line, tag=None):
            try:
                if not win.winfo_exists():
                    return
                txt.config(state="normal")
                txt.insert("end", line + "\n", tag or "")
                txt.see("end")
                txt.config(state="disabled")
                win.update()
            except Exception:
                pass

        txt.tag_config("ok",    foreground=C["green"])
        txt.tag_config("err",   foreground=C["red"])
        txt.tag_config("info",  foreground=C["accent"])
        txt.tag_config("reply", foreground=C["yellow"], font=("Segoe UI", 9, "bold"))

        _btn(body, "Close", command=win.destroy, style="ghost").pack(pady=(8, 0))

        async def _run():
            import random
            from db import get_account, get_all_settings, get_keywords, get_x_lists
            from twitter import TwitterClient
            from ai import generate_reply
            from proxy import proxy_manager
            from config import BotDefaults

            acc = await get_account(acc_id)
            if not acc:
                return None, "Account not found"

            settings  = await get_all_settings(acc_id)
            mode      = settings.get("search_mode",  BotDefaults.search_mode)
            min_likes = int(settings.get("min_likes",    BotDefaults.min_likes) or BotDefaults.min_likes)
            min_rt    = int(settings.get("min_retweets", BotDefaults.min_retweets) or BotDefaults.min_retweets)
            max_age   = int(settings.get("max_age_min",  BotDefaults.max_post_age_minutes) or BotDefaults.max_post_age_minutes)
            sort_by   = settings.get("comment_sort", BotDefaults.comment_sort)
            ai_provider   = settings.get("ai_provider", None)
            system_prompt = settings.get("system_prompt", BotDefaults.system_prompt)

            proxy = await proxy_manager.get_proxy_for_account(acc.get("proxy_id"))

            async with TwitterClient(
                account_id=acc_id,
                auth_token_enc=acc["auth_token"],
                ct0_enc=acc["ct0"],
                proxy=proxy,
            ) as client:
                username = await client.verify_session()
                if not username:
                    return None, "Session invalid"

                lines = [("✅ Session OK — @" + username, "ok")]
                lines.append((f"⚙️  mode={mode}  min_likes={min_likes}  max_age={max_age}min  AI={ai_provider}", ""))

                # ── Fetch tweets (with fallback to recommendations) ──
                tweets = []
                if mode == "keywords":
                    keywords = await get_keywords(acc_id)
                    if keywords:
                        lines.append((f"🔍 Searching keywords: {keywords[:3]}", ""))
                        for kw in keywords[:3]:
                            res = await client.search_tweets(kw, min_likes=min_likes,
                                                              min_retweets=min_rt, max_age_minutes=max_age, limit=10)
                            tweets.extend(res)
                    if not tweets:
                        lines.append(("⚠️ Keyword search returned 0 — trying recommendations...", "err"))
                        tweets = await client.get_recommended_tweets(min_likes=0, limit=20)

                elif mode == "list":
                    urls = await get_x_lists(acc_id)
                    if urls:
                        for url in urls[:3]:
                            res = await client.get_list_tweets(url, min_likes=min_likes,
                                                                min_retweets=min_rt, max_age_minutes=max_age, limit=10)
                            tweets.extend(res)
                    if not tweets:
                        lines.append(("⚠️ List returned 0 — trying recommendations...", "err"))
                        tweets = await client.get_recommended_tweets(min_likes=0, limit=20)

                else:  # recommendations
                    # Try with user's min_likes first, then fall back to 0 if empty
                    tweets = await client.get_recommended_tweets(min_likes=min_likes, limit=20)
                    if not tweets and min_likes > 0:
                        lines.append((f"⚠️ No tweets with min_likes={min_likes} — retrying with min_likes=0...", "err"))
                        tweets = await client.get_recommended_tweets(min_likes=0, limit=20)

                if not tweets:
                    lines.append(("❌ No tweets found via any method. Check your session or network.", "err"))
                    return lines, None

                lines.append((f"✅ Found {len(tweets)} tweets (mode: {mode})", "ok"))

                # ── Pick best tweet (most likes with a comment) ──
                tweets.sort(key=lambda t: t.likes, reverse=True)
                chosen_tweet   = None
                chosen_comment = None
                for t in tweets[:10]:
                    c = await client.get_top_comment(t, sort_by=sort_by)
                    if c:
                        chosen_tweet   = t
                        chosen_comment = c
                        break

                if not chosen_tweet:
                    lines.append(("⚠️ Found tweets but none had comments. Bot will try again next cycle.", "err"))
                    return lines, None

                post_url = f"https://x.com/{chosen_tweet.author_username}/status/{chosen_tweet.id}"
                lines.append((f"\n📌 POST by @{chosen_tweet.author_username} (❤ {chosen_tweet.likes} | 🔁 {chosen_tweet.retweets})", "info"))
                lines.append((chosen_tweet.text[:300], ""))
                lines.append((f"🔗 {post_url}", ""))
                lines.append((f"\n💬 TOP COMMENT by @{chosen_comment.author_username} (❤ {chosen_comment.likes})", "info"))
                lines.append((chosen_comment.text[:200], ""))

                # ── Generate AI reply ──
                lines.append(("\n🤖 Generating AI reply...", ""))
                try:
                    reply_text, prov = await generate_reply(
                        post_text=chosen_tweet.text,
                        comment_text=chosen_comment.text,
                        provider=ai_provider,
                        system_prompt=system_prompt,
                    )
                    lines.append((f"\n✅ REPLY [{prov}]:", "ok"))
                    lines.append((reply_text, "reply"))
                except Exception as e:
                    lines.append((f"\n❌ AI error: {e}", "err"))

                lines.append(("\n⚠️  Nothing was posted — this is a dry run.", ""))
                return lines, None

        def _done(fut):
            try:
                if not win.winfo_exists():
                    return
                result, err = fut.result()
                if err:
                    try: status_var.set(f"❌ {err}")
                    except Exception: pass
                    append(f"Error: {err}", "err")
                else:
                    try: status_var.set("✅ Test completed successfully")
                    except Exception: pass
                    for line, tag in result:
                        append(line, tag)
            except Exception as e:
                try:
                    if win.winfo_exists():
                        status_var.set(f"❌ Exception")
                        append(str(e), "err")
                except Exception:
                    pass

        status_var.set("⏳ Running test (this may take 10–30 seconds)...")
        run_async(_run()).add_done_callback(lambda f: self.after(0, _done, f))

    def _load_settings(self):
        try: acc_id = int(self._sett_id.get())
        except ValueError:
            messagebox.showwarning("Input", "Enter a valid account ID", parent=self); return
        async def _load():
            from db import get_all_settings, get_keywords, get_x_lists
            return await get_all_settings(acc_id), await get_keywords(acc_id), await get_x_lists(acc_id)
        def _done(fut):
            try:
                from config import BotDefaults
                s, kws, lists = fut.result()
                for key, var in self._sv.items():
                    # min_delay_min is a virtual display key (minutes)
                    if key == "min_delay_min":
                        raw = s.get("min_delay", BotDefaults.min_delay_seconds)
                        var.set(str(int(raw) // 60))
                        continue
                    val = s.get(key, "")
                    if isinstance(var, tk.BooleanVar): var.set(bool(val))
                    else: var.set(str(val) if val != "" else "")
                self._prompt_txt.delete("1.0","end")
                self._prompt_txt.insert("end", s.get("system_prompt",""))
                self._kw_txt.delete("1.0","end")
                self._kw_txt.insert("end", "\n".join(kws))
                self._list_txt.delete("1.0","end")
                self._list_txt.insert("end", "\n".join(lists))
                # Switch to Settings tab so user sees the loaded data
                self._show_tab("Settings")
                self._sett_loaded_lbl.config(text=f"✓  Loaded account {acc_id}", fg=C["green"])
            except Exception as e: messagebox.showerror("Error", str(e), parent=self)
        run_async(_load()).add_done_callback(lambda f: self.after(0, _done, f))

    def _save_settings(self):
        try: acc_id = int(self._sett_id.get())
        except ValueError:
            messagebox.showwarning("Input", "Enter a valid account ID", parent=self); return
        async def _save():
            from db import set_keywords, set_setting, set_x_lists
            from config import BotDefaults
            for key, var in self._sv.items():
                val = var.get()
                # Virtual display keys — convert minutes → seconds
                if key == "min_delay_min":
                    try:
                        mins = max(1, int(val))
                    except (ValueError, TypeError):
                        mins = BotDefaults.min_delay_seconds // 60
                    await set_setting(acc_id, "min_delay", mins * 60)
                    continue
                if isinstance(var, tk.BooleanVar):
                    await set_setting(acc_id, key, bool(val))
                elif str(val).strip() and str(val).lstrip("-").isdigit():
                    int_val = int(val)
                    # Hard cap: daily_limit max 300
                    if key == "daily_limit":
                        int_val = min(300, max(1, int_val))
                    await set_setting(acc_id, key, int_val)
                else:
                    await set_setting(acc_id, key, val)
            await set_setting(acc_id, "system_prompt",
                              self._prompt_txt.get("1.0","end").strip())
            kws = [k.strip() for k in self._kw_txt.get("1.0","end").splitlines() if k.strip()]
            await set_keywords(acc_id, kws)
            ls = [l.strip() for l in self._list_txt.get("1.0","end").splitlines() if l.strip()]
            await set_x_lists(acc_id, ls)
        def _done(fut):
            try:
                fut.result()
                _logger.success(f"[GUI] ✅ Настройки сохранены для acc_id={acc_id}")
                messagebox.showinfo("Saved","✅  Settings saved!", parent=self)
            except Exception as e:
                _logger.error(f"[GUI] Ошибка сохранения настроек acc_id={acc_id}: {e}")
                messagebox.showerror("Error", str(e), parent=self)
        run_async(_save()).add_done_callback(lambda f: self.after(0, _done, f))

    # ── API Keys ───────────────────────────────────────────────────────────────

    def _build_apikeys(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        self._toolbar(pg, "API Keys & Configuration")

        # ── Scrollable container so all fields are always reachable ──────────
        outer = tk.Frame(pg, bg=C["bg"])
        outer.grid(row=1, column=0, sticky="nsew", padx=12, pady=8)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, bg=C["surface"], highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        card = tk.Frame(canvas, bg=C["surface"])
        card.columnconfigure(1, weight=1)
        _win = canvas.create_window((0, 0), window=card, anchor="nw")

        def _on_frame(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas(e):
            canvas.itemconfig(_win, width=e.width)
        card.bind("<Configure>", _on_frame)
        canvas.bind("<Configure>", _on_canvas)
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))
        # ─────────────────────────────────────────────────────────────────────

        def api_field(r, lbl, hint, var_name, show=""):
            tk.Label(card, text=lbl, font=("Segoe UI", 9, "bold"),
                     fg=C["text"], bg=C["surface"], anchor="w",
                     width=20).grid(row=r*2,   column=0, sticky="nw",
                                    padx=(16,8), pady=(14,0))
            tk.Label(card, text=hint, font=("Segoe UI", 8),
                     fg=C["muted"], bg=C["surface"],
                     anchor="w").grid(row=r*2+1, column=0, columnspan=2,
                                      sticky="w", padx=(16,8), pady=(0,4))
            e = _entry(card, show=show)
            e.grid(row=r*2, column=1, sticky="ew", padx=(0,16), pady=(14,0), ipady=5)
            setattr(self, var_name, e)

        api_field(0, "OpenAI API Key",
                  "sk-...  Used for GPT-4o-mini replies",
                  "_e_openai", show="•")
        api_field(1, "Gemini API Key",
                  "AIza...  Used for Gemini Flash replies",
                  "_e_gemini", show="•")
        api_field(2, "Perplexity API Key",
                  "pplx-...  perplexity.ai/settings/api  (free $5 on signup)",
                  "_e_perplexity", show="•")
        api_field(3, "Groq API Key",
                  "gsk_...  console.groq.com/keys  (free tier available)",
                  "_e_groq", show="•")
        api_field(4, "Telegram Bot Token",
                  "From @BotFather - required to run the bot",
                  "_e_tg_token", show="•")
        api_field(5, "Telegram Admin IDs",
                  "Your Telegram user ID, e.g. 123456789  (find via @userinfobot)",
                  "_e_tg_admins")

        # Default provider (after 6 fields x 2 rows = rows 0-11)
        tk.Label(card, text="Default AI Provider",
                 font=("Segoe UI", 9, "bold"),
                 fg=C["text"], bg=C["surface"]).grid(
            row=12, column=0, sticky="w", padx=(16,8), pady=(14,4))
        self._e_provider = tk.StringVar(value="groq")
        pf = tk.Frame(card, bg=C["surface"])
        pf.grid(row=12, column=1, sticky="w", pady=(14,4))
        for val, lbl in [("openai","OpenAI"), ("gemini","Gemini"), ("perplexity","Perplexity"), ("groq","Groq (free)")]:
            tk.Radiobutton(pf, text=lbl, variable=self._e_provider, value=val,
                           font=("Segoe UI",9), fg=C["text"], bg=C["surface"],
                           selectcolor=C["surface"], activebackground=C["surface"],
                           cursor="hand2").pack(side="left", padx=(0,14))

        # Buttons
        bf = tk.Frame(card, bg=C["surface"])
        bf.grid(row=13, column=0, columnspan=2, sticky="w", padx=16, pady=(12,16))
        _btn(bf, "💾  Save Keys", command=self._save_apikeys,
             style="success").pack(side="left")
        _btn(bf, "🔄  Reload", command=self._load_apikeys,
             style="ghost").pack(side="left", padx=8)
        _btn(bf, "🔧  Проверить Playwright", command=self._check_playwright,
             style="warning").pack(side="left", padx=8)

        tk.Label(card, text="Keys are saved to %APPDATA%/XBot/.env  (created automatically on first launch)",
                 font=("Segoe UI", 8), fg=C["muted"],
                 bg=C["surface"]).grid(row=14, column=0, columnspan=2,
                                       sticky="w", padx=16, pady=(0,16))

        # Load on build
        self.after(500, self._load_apikeys)
    def _load_apikeys(self):
        from config import _ENV_FILE
        env_path = _ENV_FILE
        if not env_path.exists(): return
        vals = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                vals[k.strip()] = v.strip()
        self._e_openai.delete(0, "end")
        self._e_openai.insert(0, vals.get("OPENAI_API_KEY", ""))
        self._e_gemini.delete(0, "end")
        self._e_gemini.insert(0, vals.get("GEMINI_API_KEY", ""))
        self._e_perplexity.delete(0, "end")
        self._e_perplexity.insert(0, vals.get("PERPLEXITY_API_KEY", ""))
        self._e_groq.delete(0, "end")
        self._e_groq.insert(0, vals.get("GROQ_API_KEY", ""))
        self._e_tg_token.delete(0, "end")
        self._e_tg_token.insert(0, vals.get("TELEGRAM_BOT_TOKEN", ""))
        self._e_tg_admins.delete(0, "end")
        self._e_tg_admins.insert(0, vals.get("TELEGRAM_ADMIN_IDS", ""))
        self._e_provider.set(vals.get("DEFAULT_AI_PROVIDER", "groq"))
    def _save_apikeys(self):
        from config import _ENV_FILE, save_env_value, reload_settings
        from ai import reset_ai_clients
        # Normalize admin IDs: strip brackets/spaces
        admin_raw = self._e_tg_admins.get().strip()
        admin_clean = ",".join(
            x.strip() for x in admin_raw.replace("[","").replace("]","").split(",")
            if x.strip().lstrip("-").isdigit()
        )
        save_env_value("OPENAI_API_KEY",      self._e_openai.get().strip())
        save_env_value("GEMINI_API_KEY",       self._e_gemini.get().strip())
        save_env_value("PERPLEXITY_API_KEY",   self._e_perplexity.get().strip())
        save_env_value("GROQ_API_KEY",         self._e_groq.get().strip())
        save_env_value("TELEGRAM_BOT_TOKEN",   self._e_tg_token.get().strip())
        save_env_value("TELEGRAM_ADMIN_IDS",   admin_clean)
        save_env_value("DEFAULT_AI_PROVIDER",  self._e_provider.get())
        try:
            reload_settings()
            reset_ai_clients()
            _logger.success(f"[GUI] API keys saved (provider={self._e_provider.get()})")
            messagebox.showinfo("Saved",
                                "Keys saved and applied!\n\nAI providers reloaded.",
                                parent=self)
        except Exception as e:
            _logger.warning(f"[GUI] Keys saved, reload failed: {e}")
            messagebox.showinfo("Saved",
                                f"Keys saved.\n\nRestart to apply. ({e})",
                                parent=self)
    # ── Playwright check/fix ───────────────────────────────────────────────────

    def _check_playwright(self):
        """Проверить версию Playwright и при необходимости переустановить."""
        from browser_poster import REQUIRED_PLAYWRIGHT_VERSION

        # Окно с логом прогресса
        win = tk.Toplevel(self)
        win.title("🔧 Проверка Playwright")
        win.geometry("600x380")
        win.configure(bg=C["bg"])
        win.resizable(False, False)

        tk.Label(win, text="Проверка и установка Playwright",
                 font=("Segoe UI", 11, "bold"),
                 fg=C["text"], bg=C["bg"]).pack(pady=(16, 4))
        tk.Label(win,
                 text=f"Требуется версия: playwright=={REQUIRED_PLAYWRIGHT_VERSION}",
                 font=("Segoe UI", 9), fg=C["muted"], bg=C["bg"]).pack()

        txt = tk.Text(win, font=("Consolas", 9), bg="#1e1e2e", fg="#cdd6f4",
                      relief="flat", state="disabled", wrap="word",
                      height=14, padx=8, pady=8)
        txt.pack(fill="both", expand=True, padx=16, pady=8)
        txt.tag_config("ok",   foreground="#a6e3a1")
        txt.tag_config("err",  foreground="#f38ba8")
        txt.tag_config("info", foreground="#89b4fa")

        close_btn = _btn(win, "Подождите...", style="ghost")
        close_btn.pack(pady=(0, 12))
        close_btn.config(state="disabled")

        def append(line: str) -> None:
            tag = "ok" if "✅" in line else ("err" if "❌" in line else "info")
            try:
                txt.config(state="normal")
                txt.insert("end", line + "\n", tag)
                txt.see("end")
                txt.config(state="disabled")
                win.update()
            except Exception:
                pass

        def run_check():
            from browser_poster import check_and_fix_playwright
            append("Запуск проверки...")
            ok, message = check_and_fix_playwright()
            for line in message.splitlines():
                append(line)
            if ok:
                append("\n✅ Playwright готов к работе!")
                _logger.success("[GUI] Playwright — проверка пройдена, всё OK")
            else:
                append("\n❌ Не удалось исправить Playwright.")
                append("Попробуйте перезапустить программу.")
                _logger.error("[GUI] Playwright — проверка провалена")
            self.after(0, lambda: close_btn.config(
                text="Закрыть", state="normal",
                command=win.destroy
            ))

        # Запускаем в отдельном потоке чтобы не фризить GUI
        threading.Thread(target=run_check, daemon=True).start()

    # ── Proxies ────────────────────────────────────────────────────────────────

    def _build_proxies(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        bar = self._toolbar(pg, "Proxies")
        _btn(bar, "＋ Add Proxy", command=self._add_proxy, style="success").pack(side="right", padx=4)
        _btn(bar, "🗑 Delete",    command=self._del_proxy,  style="danger").pack(side="right", padx=4)
        _btn(bar, "↻ Refresh",   command=self._refresh_proxies, style="ghost").pack(side="right", padx=4)

        card = self._card(pg, row=1)
        card.columnconfigure(0, weight=1); card.rowconfigure(0, weight=1)
        self._proxy_tree = self._tree(card, [
            ("id",    "ID",     55),
            ("url",   "URL",    420),
            ("type",  "Type",   80),
            ("fails", "Fails",  70),
            ("active","Active", 80),
        ])

    def _refresh_proxies(self):
        async def _load():
            from db import get_proxies
            return await get_proxies(active_only=False)
        def _done(fut):
            try:
                self._proxy_tree.delete(*self._proxy_tree.get_children())
                for p in fut.result():
                    self._proxy_tree.insert("", "end", values=(
                        p["id"], p["url"], p["ptype"],
                        p["fail_count"], "Yes" if p["active"] else "No"))
            except Exception as e: print(f"[GUI] proxies: {e}")
        run_async(_load()).add_done_callback(lambda f: self.after(0, _done, f))

    def _add_proxy(self):
        win = tk.Toplevel(self)
        win.title("Add Proxy")
        win.geometry("460x200")
        win.configure(bg=C["bg"])
        win.resizable(False, False)
        win.grab_set()

        hdr = tk.Frame(win, bg=C["accent"], height=44)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr, text="  Add Proxy", font=("Segoe UI",11,"bold"),
                 fg="white", bg=C["accent"]).pack(side="left", padx=16)

        body = tk.Frame(win, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=24, pady=12)

        _label(body, "Proxy URL:", size=9, color=C["muted"], bg=C["bg"]).pack(anchor="w")
        e = _entry(body)
        e.pack(fill="x", pady=(4,4), ipady=5)
        e.insert(0, "socks5://user:pass@host:port")
        e.bind("<FocusIn>", lambda ev: e.select_range(0, "end"))
        _label(body, "Supports: http:// and socks5://",
               size=8, color=C["muted"], bg=C["bg"]).pack(anchor="w")

        bf = tk.Frame(body, bg=C["bg"])
        bf.pack(fill="x", pady=(10,0))

        def do_add():
            url = e.get().strip()
            if not url: return
            async def _go():
                from db import add_proxy
                ptype = "socks5" if url.startswith("socks5") else "http"
                return await add_proxy(url, ptype)
            def _done(fut):
                try:
                    pid = fut.result(); win.destroy()
                    _logger.success(f"[GUI] ✅ Прокси добавлен id={pid} url={url}")
                    self._refresh_proxies()
                    messagebox.showinfo("Added", f"✅  Proxy added (id={pid})", parent=self)
                except Exception as ex:
                    _logger.error(f"[GUI] Ошибка добавления прокси: {ex}")
                    messagebox.showerror("Error", str(ex), parent=self)
            run_async(_go()).add_done_callback(lambda f: self.after(0, _done, f))

        _btn(bf, "Add Proxy", command=do_add, style="success").pack(side="left")
        _btn(bf, "Cancel", command=win.destroy, style="ghost").pack(side="left", padx=8)

    def _del_proxy(self):
        sel = self._proxy_tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a proxy first", parent=self); return
        pid = int(self._proxy_tree.item(sel[0])["values"][0])
        if not messagebox.askyesno("Confirm", f"Delete proxy {pid}?", parent=self): return
        async def _go():
            from db import execute
            await execute("DELETE FROM proxies WHERE id=?", (pid,))
        def _done(fut):
            try:
                fut.result()
                self._refresh_proxies()
                _logger.warning(f"[GUI] 🗑 Прокси id={pid} удалён")
            except Exception as e:
                _logger.error(f"[GUI] Ошибка удаления прокси id={pid}: {e}")
                messagebox.showerror("Error", str(e), parent=self)
        run_async(_go()).add_done_callback(lambda f: self.after(0, _done, f))

    # ── Users ──────────────────────────────────────────────────────────────────

    def _build_users(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)

        bar = self._toolbar(pg, "Allowed Telegram Users")
        _btn(bar, "＋ Add User", command=self._add_user,   style="success").pack(side="right", padx=4)
        _btn(bar, "🗑 Remove",   command=self._del_user,    style="danger").pack(side="right", padx=4)
        _btn(bar, "↻ Refresh",  command=self._refresh_users, style="ghost").pack(side="right", padx=4)

        # Info label
        info = tk.Frame(pg, bg=C["bg"])
        info.grid(row=0, column=0, sticky="ew", padx=16, pady=(0,0))

        card = self._card(pg, row=1)
        card.columnconfigure(0, weight=1); card.rowconfigure(0, weight=1)
        self._users_tree = self._tree(card, [
            ("telegram_id", "Telegram ID", 140),
            ("label",       "Name / Label", 280),
            ("added_at",    "Added",        180),
        ])

        info2 = tk.Label(pg, text=(
            "ℹ  Users listed here can access the Telegram bot. "
            "Owner IDs in .env always have access regardless of this list."
        ), font=("Segoe UI", 8), fg=C["muted"], bg=C["bg"], anchor="w")
        info2.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 8))

        self.after(300, self._refresh_users)

    def _refresh_users(self):
        async def _load():
            from db import get_allowed_users
            return await get_allowed_users()
        def _done(fut):
            try:
                self._users_tree.delete(*self._users_tree.get_children())
                for u in fut.result():
                    self._users_tree.insert("", "end", values=(
                        u["telegram_id"],
                        u.get("label") or "—",
                        u["added_at"][:16] if u.get("added_at") else "—",
                    ))
            except Exception as e:
                print(f"[GUI] users refresh: {e}")
        run_async(_load()).add_done_callback(lambda f: self.after(0, _done, f))

    def _add_user(self):
        win = tk.Toplevel(self)
        win.title("Add User")
        win.geometry("420x260")
        win.configure(bg=C["bg"])
        win.resizable(False, False)
        win.grab_set()

        hdr = tk.Frame(win, bg=C["accent"], height=44)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        tk.Label(hdr, text="  Add Allowed User", font=("Segoe UI", 11, "bold"),
                 fg="white", bg=C["accent"]).pack(side="left", padx=16)

        body = tk.Frame(win, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=24, pady=12)

        _label(body, "Telegram ID:", size=9, color=C["muted"], bg=C["bg"]).pack(anchor="w")
        e_id = _entry(body)
        e_id.pack(fill="x", pady=(4, 4), ipady=5)
        _label(body, "Find via @userinfobot", size=8, color=C["muted"], bg=C["bg"]).pack(anchor="w")

        _label(body, "Name / Label (optional):", size=9, color=C["muted"], bg=C["bg"]).pack(anchor="w", pady=(8,0))
        e_label = _entry(body)
        e_label.pack(fill="x", pady=(4, 8), ipady=5)

        bf = tk.Frame(body, bg=C["bg"])
        bf.pack(fill="x", pady=(4, 0))

        def do_add():
            raw = e_id.get().strip()
            lbl = e_label.get().strip()
            try:
                uid = int(raw)
            except ValueError:
                messagebox.showwarning("Input", "Telegram ID must be a number", parent=win)
                return
            async def _go():
                from db import add_allowed_user
                await add_allowed_user(uid, lbl)
            def _done(fut):
                try:
                    fut.result()
                    win.destroy()
                    self._refresh_users()
                    _logger.success(f"[GUI] ✅ Пользователь Telegram id={uid} добавлен (label={lbl!r})")
                    messagebox.showinfo("Added", f"✅  User {uid} added.", parent=self)
                except Exception as ex:
                    _logger.error(f"[GUI] Ошибка добавления пользователя id={uid}: {ex}")
                    messagebox.showerror("Error", str(ex), parent=win)
            run_async(_go()).add_done_callback(lambda f: self.after(0, _done, f))

        _btn(bf, "✅  Add User", command=do_add, style="success").pack(side="left")
        _btn(bf, "Cancel",       command=win.destroy, style="ghost").pack(side="left", padx=8)

        # Enter key submits
        win.bind("<Return>", lambda e: do_add())
        e_id.focus_set()

    def _del_user(self):
        sel = self._users_tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a user first", parent=self); return
        uid = int(self._users_tree.item(sel[0])["values"][0])
        if not messagebox.askyesno("Confirm", f"Remove user {uid}?", parent=self): return
        async def _go():
            from db import remove_allowed_user
            await remove_allowed_user(uid)
        def _done(fut):
            try:
                fut.result()
                self._refresh_users()
                _logger.warning(f"[GUI] 🗑 Пользователь Telegram id={uid} удалён")
            except Exception as e:
                _logger.error(f"[GUI] Ошибка удаления пользователя id={uid}: {e}")
                messagebox.showerror("Error", str(e), parent=self)
        run_async(_go()).add_done_callback(lambda f: self.after(0, _done, f))

    # ── Logs ───────────────────────────────────────────────────────────────────

    def _build_logs(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(1, weight=1)
        pg.rowconfigure(2, weight=0)

        bar = self._toolbar(pg, "Activity Log")
        _btn(bar, "↻ Refresh", command=self._refresh_logs, style="ghost").pack(side="right", padx=4)
        self._log_ar_btn_ref = _btn(
            bar,
            "🔁 Auto-refresh ON",
            command=self._toggle_log_autorefresh,
            style="ghost",
        )
        self._log_ar_btn_ref.pack(side="right", padx=4)
        self._log_autorefresh = True
        self._log_refresh_after_id = None

        self._log_filter = tk.StringVar(value="all")
        ff = tk.Frame(bar, bg=C["bg"])
        ff.pack(side="right", padx=8)
        for val, lbl, col in [
            ("all",     "All",     C["text"]),
            ("posted",  "Posted",  C["green"]),
            ("pending", "Pending", C["yellow"]),
            ("error",   "Error",   C["red"]),
        ]:
            tk.Radiobutton(ff, text=lbl, variable=self._log_filter, value=val,
                           command=self._refresh_logs,
                           font=("Segoe UI",9), fg=col, bg=C["bg"],
                           selectcolor=C["bg"], activebackground=C["bg"],
                           cursor="hand2").pack(side="left", padx=6)

        card = self._card(pg, row=1)
        card.columnconfigure(0, weight=1); card.rowconfigure(0, weight=1)
        self._log_tree = self._tree(card, [
            ("id",      "ID",      45),
            ("acc",     "Acc",     45),
            ("status",  "Status",  75),
            ("ai",      "AI",      65),
            ("time",    "Time",   130),
            ("sleep",   "Sleep",   60),
            ("reply",   "Reply",  260),
            ("post_url","Post URL",200),
        ])
        self._log_tree.tag_configure("posted",  foreground=C["green"])
        self._log_tree.tag_configure("error",   foreground=C["red"])
        self._log_tree.tag_configure("pending", foreground=C["yellow"])
        self._log_tree.bind("<ButtonRelease-1>", self._on_log_select)

        # Detail panel
        detail_card = self._card(pg, row=2)
        detail_card.columnconfigure(0, weight=1)
        detail_card.rowconfigure(1, weight=1)
        tk.Label(detail_card, text="Reply Preview",
                 font=("Segoe UI", 9, "bold"),
                 fg=C["muted"], bg=C["surface"]).grid(
            row=0, column=0, sticky="w", padx=12, pady=(8,2))
        self._log_detail = tk.Text(
            detail_card, font=("Segoe UI", 9),
            fg=C["text"], bg=C["bg"],
            height=4, relief="flat", bd=0,
            wrap="word", state="disabled",
        )
        self._log_detail.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0,8))

        # Auto-refresh every 5s
        self._schedule_log_refresh()

    def _refresh_logs(self):
        filt = self._log_filter.get()
        async def _load():
            from db import fetchall
            sql = ("SELECT id,account_id,status,ai_provider,created_at,"
                   "sleep_seconds,reply_text,post_url FROM posts_log")
            if filt != "all": sql += f" WHERE status='{filt}'"
            sql += " ORDER BY id DESC LIMIT 200"
            return await fetchall(sql)
        def _done(fut):
            try:
                self._log_tree.delete(*self._log_tree.get_children())
                for r in fut.result():
                    st = r["status"]
                    tag = st if st in ("posted","error","pending") else ""
                    sleep_s = r.get("sleep_seconds") or 0
                    sleep_str = f"{int(sleep_s)}s" if sleep_s else "—"
                    self._log_tree.insert("", "end", tags=(tag,), values=(
                        r["id"], r["account_id"], st,
                        r.get("ai_provider") or "",
                        r["created_at"],
                        sleep_str,
                        str(r.get("reply_text") or "")[:60],
                        str(r.get("post_url") or "—")))
            except Exception as e: print(f"[GUI] logs: {e}")
        run_async(_load()).add_done_callback(lambda f: self.after(0, _done, f))

    def _schedule_log_refresh(self):
        self._refresh_logs()
        if getattr(self, "_log_autorefresh", True):
            self._log_refresh_after_id = self.after(5000, self._schedule_log_refresh)
        else:
            self._log_refresh_after_id = None

    def _toggle_log_autorefresh(self):
        self._log_autorefresh = not self._log_autorefresh
        if getattr(self, "_log_ar_btn_ref", None) is not None:
            self._log_ar_btn_ref.config(
                text=("🔁 Auto-refresh ON" if self._log_autorefresh else "⏸ Auto-refresh OFF")
            )
        if not self._log_autorefresh and self._log_refresh_after_id is not None:
            try:
                self.after_cancel(self._log_refresh_after_id)
            except Exception:
                pass
            self._log_refresh_after_id = None
        elif self._log_autorefresh and self._log_refresh_after_id is None:
            # Restart scheduling immediately when re-enabled
            self._schedule_log_refresh()

    def _on_log_select(self, event):
        sel = self._log_tree.selection()
        if not sel:
            return
        vals = self._log_tree.item(sel[0])["values"]
        # vals: id, acc, status, ai, time, sleep, reply (truncated), post_url
        log_id = vals[0]
        async def _load_detail():
            from db import fetchone
            r = await fetchone("SELECT reply_text, post_text, comment_text, post_url FROM posts_log WHERE id=?", (log_id,))
            return r
        def _done(fut):
            try:
                r = fut.result()
                if not r:
                    return
                self._log_detail.config(state="normal")
                self._log_detail.delete("1.0", "end")
                self._log_detail.insert("end", f"REPLY: {r.get('reply_text','') or '—'}\n\n")
                self._log_detail.insert("end", f"POST: {r.get('post_text','')[:200] or '—'}\n")
                url = r.get("post_url","")
                if url:
                    self._log_detail.insert("end", f"URL: {url}")
                self._log_detail.config(state="disabled")
            except Exception as e:
                print(f"[GUI] log detail: {e}")
        run_async(_load_detail()).add_done_callback(lambda f: self.after(0, _done, f))

    # ── Stats ──────────────────────────────────────────────────────────────────

    def _build_stats(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(2, weight=1)

        bar = self._toolbar(pg, "Statistics")
        _btn(bar, "↻ Refresh", command=self._refresh_stats, style="ghost").pack(side="right")

        # Summary cards
        cards_row = tk.Frame(pg, bg=C["bg"])
        cards_row.grid(row=1, column=0, sticky="ew", padx=12, pady=(0,8))
        self._sc: dict[str, tk.Label] = {}
        for key, lbl, accent in [
            ("today",    "Replies Today",   C["accent"]),
            ("posted",   "Total Posted",    C["green"]),
            ("accounts", "Active Accounts", C["yellow"]),
            ("workers",  "Running Workers", C["text"]),
        ]:
            c = tk.Frame(cards_row, bg=C["surface"],
                         highlightthickness=1,
                         highlightbackground=C["border"])
            c.pack(side="left", padx=6, ipadx=20, ipady=12,
                   expand=True, fill="x")
            tk.Label(c, text=lbl, font=("Segoe UI",8),
                     fg=C["muted"], bg=C["surface"]).pack()
            v = tk.Label(c, text="—", font=("Segoe UI",24,"bold"),
                         fg=accent, bg=C["surface"])
            v.pack()
            self._sc[key] = v

        card = self._card(pg, row=2)
        card.columnconfigure(0, weight=1); card.rowconfigure(0, weight=1)
        self._stats_tree = self._tree(card, [
            ("username", "Account", 220),
            ("today",    "Today",   100),
            ("limit",    "Limit",   100),
            ("bar",      "Usage",   360),
        ])

    def _refresh_stats(self):
        async def _load():
            from db import fetchone, get_accounts, get_daily_count, get_setting
            try:
                from main import worker_manager
                running = len(worker_manager.running_accounts())
            except Exception: running = 0
            accs = await get_accounts(active_only=False)
            active = sum(1 for a in accs if a["active"])
            total = 0; rows = []
            for a in accs:
                cnt = await get_daily_count(a["id"])
                total += cnt
                lim = int(await get_setting(a["id"], "daily_limit", 150))
                pct = int(cnt / max(lim, 1) * 100)
                bar = "█" * int(pct/5) + "░" * (20 - int(pct/5))
                rows.append((f"@{a['username']}", cnt, lim, f"{bar}  {pct}%"))
            tp = await fetchone("SELECT COUNT(*) n FROM posts_log WHERE status='posted'")
            return rows, total, (tp["n"] if tp else 0), active, running
        def _done(fut):
            try:
                rows, today, posted, active, workers = fut.result()
                self._sc["today"].config(text=str(today))
                self._sc["posted"].config(text=str(posted))
                self._sc["accounts"].config(text=str(active))
                self._sc["workers"].config(text=str(workers))
                self._stats_tree.delete(*self._stats_tree.get_children())
                for row in rows: self._stats_tree.insert("", "end", values=row)
            except Exception as e: print(f"[GUI] stats: {e}")
        run_async(_load()).add_done_callback(lambda f: self.after(0, _done, f))

    # ── Telegram Bot ───────────────────────────────────────────────────────────

    def _toggle_tg_bot(self):
        if self._tg_running: return
        self._tg_running = True
        self._tg_btn.config(text="🔄  Starting...", bg=C["yellow"])
        self.update()

        def _run():
            import asyncio as _a
            loop = _a.new_event_loop()
            _a.set_event_loop(loop)
            _logger.info("[GUI] Telegram-бот запускается...")
            try:
                from main import main as bot_main
                loop.run_until_complete(bot_main())
            except Exception as e:
                err = str(e)
                if "signal" not in err.lower():
                    _logger.error(f"[GUI] Telegram-бот упал: {err}")
                    print(f"[Bot] {e}")
                self.after(0, lambda: (
                    setattr(self, "_tg_running", False),
                    self._tg_btn.config(text="▶  Start Telegram Bot", bg=C["green"]),
                    self._tg_dot.config(text="● Telegram: Error", fg=C["red"]),
                ))

        t = threading.Thread(target=_run, daemon=True, name="TelegramBot")
        t.start()

        # Check after 4s if thread is still alive (means bot started OK)
        def _check():
            if t.is_alive():
                _logger.success("[GUI] ✅ Telegram-бот запущен и работает")
                self._tg_btn.config(text="✓  Bot Running", bg=_dim(C["green"], 10))
                self._tg_dot.config(text="●  Telegram: ON", fg=C["green"])
            else:
                self._tg_running = False
                _logger.warning("[GUI] Telegram-бот не запустился (поток завершился)")
                self._tg_btn.config(text="▶  Start Telegram Bot", bg=C["green"])
                self._tg_dot.config(text="● Telegram: OFF", fg=C["red"])
        self.after(4000, _check)

    # ── Auto refresh ───────────────────────────────────────────────────────────

    def _refresh_all(self):
        self._refresh_accounts()
        self._refresh_proxies()
        self._refresh_logs()
        self._refresh_stats()

    def _auto_refresh(self):
        self._refresh_all()
        self.after(12000, self._auto_refresh)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    app = XBotApp()
    app.mainloop()

if __name__ == "__main__":
    main()