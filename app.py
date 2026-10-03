"""Euroleague Free Throws — Windows GUI.

Pick one or more seasons, view each team's free throws made, free throws made by
their opponents, and the difference, for Regular Season, Playoffs, Final Four
and a combined Total — all in one scrollable view. With several seasons ticked
the stats are summed across them (averages are per game over all of them).
Data is cached locally per season so re-selecting is instant/offline; Refresh
forces a re-read of every selected season from the API.
"""

import threading
from tkinter import ttk

import customtkinter as ctk

import euroleague_api as api
import storage

# Seasons offered in the dropdown (modern Euroleague era).
FIRST_YEAR = 2000
LAST_YEAR = 2026  # 2026-27

# One row per team. Columns: (key, title, width, anchor, kind)
#   kind: text | int | intdiff (signed) | float | floatdiff (signed)
COLUMNS = [
    ("team", "Team", 120, "w", "text"),
    ("games", "Games", 70, "center", "int"),
    ("avg_att", "Avg FT Att", 110, "center", "float"),
    ("avg_opp", "Avg FT Opp", 110, "center", "float"),
    ("avg_diff", "Avg Diff", 100, "center", "floatdiff"),
    ("rs_att", "RS FT Att", 105, "center", "int"),
    ("rs_opp", "RS FT Opp", 105, "center", "int"),
    ("rs_diff", "RS Diff", 95, "center", "intdiff"),
    ("po_att", "PO FT Att", 105, "center", "int"),
    ("po_opp", "PO FT Opp", 105, "center", "int"),
    ("po_diff", "PO Diff", 95, "center", "intdiff"),
    ("f4_att", "F4 FT Att", 105, "center", "int"),
    ("f4_opp", "F4 FT Opp", 105, "center", "int"),
    ("f4_diff", "F4 Diff", 95, "center", "intdiff"),
]

# Only the team name sorts as text; everything else is numeric.
TEXT_COLUMNS = {"team"}

# Selectable point-margin thresholds for the "close games" filter.
MARGIN_CHOICES = [3, 5, 8, 10, 12, 15, 20, 25, 30]

# Font sizes.
ROW_HEIGHT = 30
BODY_FONT = ("Segoe UI", 13)
HEAD_FONT = ("Segoe UI", 13, "bold")
CONTROL_SIZE = 15

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class SortableTable(ttk.Treeview):
    """A Treeview whose columns sort (numeric-aware) when headers are clicked.

    Height auto-fits the row count so the *outer* frame scrolls, not the table.
    """

    def __init__(self, master):
        col_ids = [c[0] for c in COLUMNS]
        super().__init__(master, columns=col_ids, show="headings", selectmode="none")
        self._sort_state = {}
        for key, title, width, anchor, _kind in COLUMNS:
            self.heading(key, text=title, command=lambda k=key: self._sort_by(k))
            self.column(key, width=width, anchor=anchor, stretch=False)

    @staticmethod
    def _fmt(kind, v):
        if kind == "text":
            return v
        if kind == "int":
            return str(v)
        if kind == "intdiff":
            return f"+{v}" if v > 0 else str(v)
        if kind == "float":
            return f"{v:.1f}"
        if kind == "floatdiff":
            return f"+{v:.1f}" if v > 0 else f"{v:.1f}"
        return str(v)

    def set_rows(self, rows):
        self.delete(*self.get_children())
        for r in rows:
            values = tuple(
                self._fmt(kind, r.get(key, "" if kind == "text" else 0))
                for key, _t, _w, _a, kind in COLUMNS
            )
            self.insert("", "end", values=values)
        self.configure(height=max(1, len(rows)))
        self._sort_state.clear()

    def _sort_by(self, key):
        descending = self._sort_state.get(key, False)
        idx = [c[0] for c in COLUMNS].index(key)
        numeric = key not in TEXT_COLUMNS

        def conv(v):
            if not numeric:
                return v.lower()
            return float(str(v).replace("+", ""))

        items = [(conv(self.set(i, key)), i) for i in self.get_children("")]
        items.sort(reverse=descending)
        for pos, (_, item) in enumerate(items):
            self.move(item, "", pos)

        for k, title, _w, _a, _kind in COLUMNS:
            self.heading(k, text=title)
        arrow = " ▼" if descending else " ▲"
        self.heading(key, text=COLUMNS[idx][1] + arrow)
        self._sort_state[key] = not descending


class MultiSelectMenu(ctk.CTkButton):
    """A dropdown button whose list is a checkbox per option.

    Ticking boxes fires nothing; `command(selected)` runs once when the list
    closes (Done / click outside) with a changed, non-empty selection.
    Escape closes it without applying.
    """

    def __init__(self, master, values, command, font, list_height=360, **kwargs):
        super().__init__(master, command=self._toggle, font=font, **kwargs)
        self._values = list(values)
        self._selected = []
        self._on_change = command
        self._font = font
        self._list_height = list_height
        self._popup = None
        self._vars = {}
        root = self.winfo_toplevel()
        root.bind_all("<Button-1>", self._on_any_click, add="+")
        root.bind("<Configure>", self._on_root_configure, add="+")

    def get(self):
        return list(self._selected)

    def set(self, selected):
        self._selected = [v for v in self._values if v in selected]
        n = len(self._selected)
        text = self._selected[0] if n == 1 else f"{n} seasons"
        self.configure(text=f"{text}  ▾")

    def close(self, apply=True):
        pop, self._popup = self._popup, None
        if pop is None:
            return
        chosen = [v for v in self._values if self._vars[v].get()]
        pop.destroy()
        if apply and chosen and chosen != self._selected:
            self.set(chosen)
            self._on_change(chosen)

    def _toggle(self):
        if self._popup is not None:
            self.close()
        else:
            self._open()

    def _open(self):
        pop = ctk.CTkToplevel(self)
        pop.withdraw()
        pop.overrideredirect(True)
        pop.transient(self.winfo_toplevel())
        self._popup = pop

        box = ctk.CTkFrame(pop, corner_radius=0, border_width=1)
        box.pack(fill="both", expand=True)

        top = ctk.CTkFrame(box, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(8, 0))
        small = dict(width=90, height=30, font=self._font)
        ctk.CTkButton(
            top, text="Select all", command=lambda: self._set_all(True), **small
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            top, text="Clear", command=lambda: self._set_all(False), **small
        ).pack(side="left")

        lst = ctk.CTkScrollableFrame(box, width=160, height=self._list_height)
        lst.pack(fill="both", expand=True, padx=8, pady=8)
        self._vars = {}
        for v in self._values:
            var = ctk.BooleanVar(value=v in self._selected)
            self._vars[v] = var
            ctk.CTkCheckBox(lst, text=v, variable=var, font=self._font).pack(
                anchor="w", padx=6, pady=4
            )

        ctk.CTkButton(box, text="Done", height=34, font=self._font,
                      command=self.close).pack(fill="x", padx=8, pady=(0, 8))

        pop.bind("<Escape>", lambda _e: self.close(apply=False))
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 2
        pop.geometry(f"+{x}+{y}")
        pop.deiconify()
        pop.lift()
        pop.focus_force()

    def _set_all(self, value):
        for var in self._vars.values():
            var.set(value)

    def _on_any_click(self, event):
        """Close (and apply) when clicking anywhere outside the list/button."""
        if self._popup is None:
            return
        w = str(event.widget)
        for owner in (str(self._popup), str(self)):
            if w == owner or w.startswith(owner + "."):
                return
        self.close()

    def _on_root_configure(self, event):
        # The main window moved/resized: the list would be left floating.
        if self._popup is not None and event.widget is self.winfo_toplevel():
            self.close()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Euroleague Free Throws")
        self.geometry("2560x1600")
        self.minsize(900, 600)

        self._busy = False
        self._raw = None
        self._build_widgets()
        # Auto-load the most recent season on startup.
        latest = [api.season_label(LAST_YEAR)]
        self.season_menu.set(latest)
        self._on_seasons_change(latest)

    # ----- layout -------------------------------------------------------
    def _build_widgets(self):
        ctrl_font = ctk.CTkFont(size=CONTROL_SIZE)

        bar = ctk.CTkFrame(self)
        bar.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(bar, text="Seasons:", font=ctrl_font).pack(
            side="left", padx=(16, 8), pady=14
        )

        years = list(range(LAST_YEAR, FIRST_YEAR - 1, -1))
        self.season_menu = MultiSelectMenu(
            bar,
            values=[api.season_label(y) for y in years],
            command=self._on_seasons_change,
            font=ctrl_font,
            width=160,
            height=40,
        )
        self.season_menu.pack(side="left", padx=8, pady=14)

        self.refresh_btn = ctk.CTkButton(
            bar, text="Refresh", width=140, height=40,
            font=ctrl_font, command=self._on_refresh,
        )
        self.refresh_btn.pack(side="left", padx=8, pady=14)

        self.close_only = ctk.BooleanVar(value=False)
        self.close_chk = ctk.CTkCheckBox(
            bar,
            text="Close games only (≤",
            variable=self.close_only,
            command=self._refresh_view,
            font=ctrl_font,
        )
        self.close_chk.pack(side="left", padx=(12, 4), pady=14)

        self.margin_var = ctk.StringVar(value=str(api.CLOSE_MARGIN))
        self.margin_menu = ctk.CTkOptionMenu(
            bar,
            values=[str(m) for m in MARGIN_CHOICES],
            variable=self.margin_var,
            command=lambda _v: self._refresh_view(),
            width=72,
            height=40,
            font=ctrl_font,
            dropdown_font=ctrl_font,
        )
        self.margin_menu.pack(side="left", padx=0, pady=14)
        ctk.CTkLabel(bar, text="pts)", font=ctrl_font).pack(
            side="left", padx=(4, 8), pady=14
        )

        self.status = ctk.CTkLabel(bar, text="", anchor="w", font=ctrl_font)
        self.status.pack(side="left", fill="x", expand=True, padx=16)

        self.progress = ctk.CTkProgressBar(self, height=16)
        self.progress.set(0)

        self._build_sections()

    def _build_sections(self):
        self._style_treeview()
        self.scroll = ctk.CTkScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True, padx=16, pady=(8, 16))
        self.table = SortableTable(self.scroll)
        self.table.pack(fill="x", expand=False, padx=8, pady=8)

    def _style_treeview(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background="#2b2b2b",
            fieldbackground="#2b2b2b",
            foreground="#dce4ee",
            rowheight=ROW_HEIGHT,
            borderwidth=0,
            font=BODY_FONT,
        )
        style.configure(
            "Treeview.Heading",
            background="#1f6aa5",
            foreground="white",
            relief="flat",
            font=HEAD_FONT,
            padding=(6, 8),
        )
        style.map("Treeview.Heading", background=[("active", "#144870")])

    # ----- actions ------------------------------------------------------
    def _on_seasons_change(self, labels):
        if self._busy:
            return
        years = sorted(api.label_to_year(label) for label in labels)
        cached, missing, saved = {}, [], None
        for year in years:
            data, fetched_at = storage.load(year)
            if data is not None and self._is_raw(data):
                cached[year] = data
                saved = fetched_at
            else:
                # Not cached, or cached in an older incompatible format -> fetch.
                missing.append(year)
        if not missing:
            self._show(cached)
            when = f" (saved {saved})" if len(years) == 1 else ""
            self._set_status(
                f"Loaded {self._describe(years)} from local cache{when}."
            )
        else:
            self._start_fetch(missing, cached)

    def _on_refresh(self):
        if self._busy:
            return
        years = sorted(api.label_to_year(label) for label in self.season_menu.get())
        self._start_fetch(years, {}, force=True)

    # ----- fetching (background thread) --------------------------------
    def _start_fetch(self, to_fetch, have, force=False):
        """Fetch the seasons in `to_fetch` one after another, then show them
        together with the already-loaded seasons in `have` ({year: results})."""
        self._set_busy(True)
        self.progress.pack(fill="x", padx=16, pady=(0, 8))
        self.progress.set(0)
        verb = "Refreshing" if force else "Fetching"
        self._set_status(f"{verb} {self._describe(to_fetch)}...")
        have = dict(have)
        n = len(to_fetch)

        def worker():
            errors, fetched_at = [], None
            for i, year in enumerate(to_fetch):
                label = api.season_label(year)

                def progress(done, total, message, i=i, label=label):
                    step = done / total if total else 0
                    prefix = f"{label} ({i + 1}/{n}): " if n > 1 else ""
                    self._progress_cb(i + step, n, prefix + message)

                try:
                    data = api.fetch_season(year, progress=progress)
                    fetched_at = storage.save(year, data)
                    have[year] = data
                except api.EuroleagueError as exc:
                    errors.append(str(exc))
                except Exception as exc:  # network/unexpected
                    errors.append(f"{label}: unexpected error: {exc}")
            self.after(0, lambda: self._fetch_done(to_fetch, have, errors, fetched_at))

        threading.Thread(target=worker, daemon=True).start()

    def _progress_cb(self, done, total, message):
        frac = done / total if total else 0
        self.after(0, lambda: self._update_progress(frac, message))

    def _update_progress(self, frac, message):
        self.progress.set(frac)
        self._set_status(message)

    def _fetch_done(self, fetched, have, errors, fetched_at):
        if not have:
            self._fetch_failed(" | ".join(errors))
            return
        self._show(have)
        self.progress.pack_forget()
        self._set_busy(False)
        ok = [y for y in fetched if y in have]
        if len(have) == 1 and ok:
            msg = f"{self._describe(ok)} updated from API (saved {fetched_at})."
        else:
            msg = f"Showing {self._describe(sorted(have))}"
            msg += f" — {len(ok)} fetched from API." if ok else "."
        if errors:
            msg += "  Failed: " + " | ".join(errors)
        self._set_status(msg)

    def _fetch_failed(self, message):
        self.progress.pack_forget()
        self._set_busy(False)
        self._set_status(message)

    # ----- helpers ------------------------------------------------------
    @staticmethod
    def _is_raw(data):
        """True if cached data is the current raw per-game format."""
        return isinstance(data, list) and (not data or "margin" in data[0])

    @staticmethod
    def _describe(years):
        """[2024] -> '2024-25'; up to 3 seasons listed; more -> 'N seasons (a – b)'."""
        labels = [api.season_label(y) for y in sorted(years)]
        if len(labels) <= 3:
            return ", ".join(labels)
        return f"{len(labels)} seasons ({labels[0]} – {labels[-1]})"

    def _show(self, by_year):
        """Combine {year: raw results} (oldest first) and render them."""
        self._set_data([r for year in sorted(by_year) for r in by_year[year]])

    def _set_data(self, results):
        """Store raw per-game results and render with the current filter."""
        self._raw = results
        self._refresh_view()

    def _refresh_view(self, *_):
        """Aggregate the raw results for the selected margin and show them."""
        if self._raw is None:
            return
        on = self.close_only.get()
        self.margin_menu.configure(state="normal" if on else "disabled")
        max_margin = int(self.margin_var.get()) if on else None
        self.table.set_rows(api.aggregate(self._raw, max_margin))

    def _set_status(self, text):
        self.status.configure(text=text)

    def _set_busy(self, busy):
        self._busy = busy
        if busy:
            self.season_menu.close(apply=False)
        state = "disabled" if busy else "normal"
        self.refresh_btn.configure(state=state)
        self.season_menu.configure(state=state)


if __name__ == "__main__":
    App().mainloop()
