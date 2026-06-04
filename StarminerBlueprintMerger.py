#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import struct
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ARRAY_TAG = b"\x17\x00\x00\x00SavedBluePrintStations\x00\x0e\x00\x00\x00ArrayProperty\x00"
NONE_TAG = b"\x05\x00\x00\x00None\x00"
CURRENT_PATH_TAG = b"CurrentPathPointIndex"

DEFAULT_SAVE_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "ILLSpace" / "Saved" / "SaveGames"
DEFAULT_BLUEPRINT_PATH = DEFAULT_SAVE_DIR / "Blueprints.sav"


class MergeError(Exception):
    pass


def read_fstring(data: bytes | bytearray, offset: int) -> tuple[str, int]:
    if offset + 4 > len(data):
        raise MergeError("Unexpected end of file while reading string length.")
    length = struct.unpack_from("<i", data, offset)[0]
    offset += 4

    if length == 0:
        return "", offset

    if length > 0:
        end = offset + length
        if end > len(data):
            raise MergeError("Unexpected end of file while reading string.")
        return data[offset:end - 1].decode("utf-8", errors="replace"), end

    chars = -length
    end = offset + chars * 2
    if end > len(data):
        raise MergeError("Unexpected end of file while reading UTF-16 string.")
    return data[offset:end - 2].decode("utf-16le", errors="replace"), end


def parse_str_property_value_after(data: bytes, marker_pos: int) -> str | None:
    try:
        off = marker_pos
        _, off = read_fstring(data, off)
        prop_type, off = read_fstring(data, off)
        if prop_type != "StrProperty":
            return None
        off += 8

        for extra in range(0, 33):
            cand = off + extra
            if cand + 4 > len(data):
                break
            strlen = struct.unpack_from("<i", data, cand)[0]
            if 1 <= strlen <= 200 and cand + 4 + strlen <= len(data):
                raw = data[cand + 4:cand + 4 + strlen - 1]
                if all((32 <= b <= 126) or b in (9, 10, 13) for b in raw):
                    s = raw.decode("utf-8", errors="replace").strip()
                    if s and "\x00" not in s:
                        return s
    except Exception:
        return None
    return None


def station_names_from_entry(entry: bytes) -> list[str]:
    names: list[str] = []
    pos = 0
    marker = b"\x0c\x00\x00\x00StationName\x00"

    while True:
        idx = entry.find(marker, pos)
        if idx < 0:
            break
        value = parse_str_property_value_after(entry, idx)
        if value and value not in names:
            names.append(value)
        pos = idx + 1

    if not names:
        for idx in [m.start() for m in re.finditer(b"StationName", entry)]:
            window = entry[idx:idx + 220]
            for raw in re.findall(rb"[ -~]{4,80}\x00", window):
                s = raw[:-1].decode("utf-8", errors="replace").strip()
                if s not in {"StationName", "StrProperty", "BuildPointStructId", "ColonistId"} and s not in names:
                    names.append(s)
                    break

    return names


@dataclass
class BlueprintEntry:
    file_index: int
    entry_index: int
    start: int
    end: int
    names: list[str]

    @property
    def label(self) -> str:
        if self.names:
            return " / ".join(self.names[:2])
        return f"Entry {self.entry_index}"


@dataclass
class SaveInfo:
    path: Path
    data: bytes
    is_empty: bool
    array_size_offset: int | None = None
    array_size: int = 0
    count_offset: int | None = None
    count: int = 0
    struct_size_offset: int | None = None
    struct_size: int = 0
    payload_start: int | None = None
    entries: list[BlueprintEntry] | None = None

    @property
    def internal_count(self) -> int:
        return len(self.entries or [])

    @property
    def visible_estimate(self) -> int:
        return max(0, self.internal_count - 1)


def analyse_save(path: str | Path, file_index: int = 0) -> SaveInfo:
    path = Path(path)
    data = path.read_bytes()

    if b"RTSBlueprintsData" not in data:
        raise MergeError(f"{path.name} does not look like a Starminer blueprint save.")

    tag_pos = data.find(ARRAY_TAG)
    if tag_pos < 0:
        return SaveInfo(path=path, data=data, is_empty=True, entries=[])

    off = tag_pos
    name, off = read_fstring(data, off)
    prop_type, off = read_fstring(data, off)
    if name != "SavedBluePrintStations" or prop_type != "ArrayProperty":
        raise MergeError(f"{path.name}: unable to parse blueprint array header.")

    array_size_offset = off
    array_size = struct.unpack_from("<Q", data, off)[0]
    off += 8

    inner_type, off = read_fstring(data, off)
    if inner_type != "StructProperty":
        raise MergeError(f"{path.name}: unexpected array inner type {inner_type!r}.")

    if data[off] == 0:
        off += 1

    count_offset = off
    count = struct.unpack_from("<I", data, off)[0]
    off += 4

    nested_name, off2 = read_fstring(data, off)
    nested_type, off2 = read_fstring(data, off2)
    if nested_name != "SavedBluePrintStations" or nested_type != "StructProperty":
        raise MergeError(f"{path.name}: unable to parse nested blueprint struct.")

    struct_size_offset = off2
    struct_size = struct.unpack_from("<Q", data, off2)[0]
    off2 += 8

    struct_name, off2 = read_fstring(data, off2)
    if struct_name != "StationBuild":
        raise MergeError(f"{path.name}: expected StationBuild, found {struct_name!r}.")

    payload_start = off2 + 16
    if payload_start < len(data) and data[payload_start] == 0:
        payload_start += 1

    ends: list[int] = []
    scan = payload_start
    while True:
        pos = data.find(CURRENT_PATH_TAG, scan)
        if pos < 0:
            break
        none_pos = data.find(NONE_TAG, pos)
        if none_pos < 0:
            break
        ends.append(none_pos + len(NONE_TAG))
        scan = none_pos + len(NONE_TAG)

    if not ends:
        raise MergeError(f"{path.name}: no blueprint boundaries found.")

    starts = [payload_start] + ends[:-1]
    entries: list[BlueprintEntry] = []

    for idx, (start, end) in enumerate(zip(starts, ends)):
        chunk = data[start:end]
        entries.append(BlueprintEntry(file_index, idx, start, end, station_names_from_entry(chunk)))

    return SaveInfo(
        path=path,
        data=data,
        is_empty=False,
        array_size_offset=array_size_offset,
        array_size=array_size,
        count_offset=count_offset,
        count=count,
        struct_size_offset=struct_size_offset,
        struct_size=struct_size,
        payload_start=payload_start,
        entries=entries,
    )


def merge_selected(infos: list[SaveInfo], base_file_index: int, selected_keys: set[tuple[int, int]], output_path: str | Path) -> dict:
    if not infos:
        raise MergeError("No files loaded.")

    base = infos[base_file_index]
    if base.is_empty:
        raise MergeError("Base file is empty. Choose a populated base file.")

    chunks: list[bytes] = []
    report: list[str] = []

    for info in infos:
        if info.is_empty or not info.entries:
            continue
        for entry in info.entries:
            key = (entry.file_index, entry.entry_index)
            if key not in selected_keys:
                continue
            if info is base:
                continue
            chunks.append(info.data[entry.start:entry.end])
            report.append(f"{info.path.name}: {entry.label}")

    if not chunks:
        raise MergeError("No non-base blueprint entries selected to append.")

    output = bytearray(base.data)
    insert_at = base.entries[-1].end
    payload = b"".join(chunks)
    delta = len(payload)

    output = output[:insert_at] + payload + output[insert_at:]

    new_count = base.count + len(chunks)
    struct.pack_into("<I", output, base.count_offset, new_count)
    struct.pack_into("<Q", output, base.array_size_offset, base.array_size + delta)
    struct.pack_into("<Q", output, base.struct_size_offset, base.struct_size + delta)

    output_path = Path(output_path)
    output_path.write_bytes(output)

    return {
        "output": str(output_path),
        "base": base.path.name,
        "base_internal_entries": base.internal_count,
        "appended_entries": len(chunks),
        "new_internal_count": new_count,
        "visible_estimate": max(0, new_count - 1),
        "appended_report": report,
    }


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Starminer Blueprint Merger - AppData Base")
        self.geometry("1040x690")
        self.minsize(900, 560)

        self.files: list[str] = []
        self.infos: list[SaveInfo] = []
        self.item_to_key: dict[str, tuple[int, int] | None] = {}
        self.base_index = tk.IntVar(value=0)
        self._build_ui()
        self.load_appdata_base_on_startup()

    def _build_ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root, text="Starminer Blueprint Merger", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            root,
            text=f"Default base path: {DEFAULT_BLUEPRINT_PATH}",
            wraplength=980,
        ).pack(anchor="w", pady=(2, 8))

        buttons = ttk.Frame(root)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Reload AppData Blueprints.sav as base", command=self.reload_appdata_base).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Add source .sav files", command=self.add_files).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Clear", command=self.clear).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Select all non-base visible entries", command=self.select_recommended).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Merge selected to new file...", command=self.merge).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Merge selected directly into AppData Blueprints.sav", command=self.merge_to_appdata).pack(side=tk.RIGHT, padx=6)

        base_row = ttk.Frame(root)
        base_row.pack(fill=tk.X, pady=(10, 4))
        ttk.Label(base_row, text="Base file:").pack(side=tk.LEFT)
        self.base_combo = ttk.Combobox(base_row, state="readonly", width=88)
        self.base_combo.pack(side=tk.LEFT, padx=8)
        self.base_combo.bind("<<ComboboxSelected>>", self.on_base_change)

        columns = ("file", "entry", "names", "note")
        self.tree = ttk.Treeview(root, columns=columns, show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="Tick")
        self.tree.heading("file", text="File")
        self.tree.heading("entry", text="Entry")
        self.tree.heading("names", text="Detected name(s)")
        self.tree.heading("note", text="Note")
        self.tree.column("#0", width=80)
        self.tree.column("file", width=300)
        self.tree.column("entry", width=80)
        self.tree.column("names", width=340)
        self.tree.column("note", width=200)
        self.tree.pack(fill=tk.BOTH, expand=True, pady=8)

        self.tree.bind("<Double-1>", self.toggle_item)
        self.tree.bind("<space>", self.toggle_selected)

        self.log = tk.Text(root, height=9, wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=False)
        self._log("Tip: double-click an entry to tick/untick it. Entry 0 is usually an internal/baseline entry.\n")

    def _log(self, msg: str):
        self.log.insert(tk.END, msg)
        self.log.see(tk.END)

    def load_appdata_base_on_startup(self):
        if DEFAULT_BLUEPRINT_PATH.exists():
            self.files = [str(DEFAULT_BLUEPRINT_PATH)]
            self.reload_analysis()
            self._log(f"Loaded AppData base save: {DEFAULT_BLUEPRINT_PATH}\n")
        else:
            self._log(f"AppData Blueprints.sav not found at: {DEFAULT_BLUEPRINT_PATH}\n")
            self._log("Use 'Add source .sav files' or place Blueprints.sav in the Starminer SaveGames folder.\n")

    def reload_appdata_base(self):
        if not DEFAULT_BLUEPRINT_PATH.exists():
            messagebox.showerror("Not found", f"Could not find:\n{DEFAULT_BLUEPRINT_PATH}")
            return

        appdata_path = str(DEFAULT_BLUEPRINT_PATH)
        self.files = [f for f in self.files if Path(f).resolve() != DEFAULT_BLUEPRINT_PATH.resolve()]
        self.files.insert(0, appdata_path)
        self.reload_analysis()
        self.base_combo.current(0)
        self.base_index.set(0)
        self.update_base_notes()
        self.select_recommended()

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Choose source Starminer blueprint .sav files",
            filetypes=[("Save files", "*.sav"), ("All files", "*.*")],
        )
        if not paths:
            return
        for p in paths:
            if p not in self.files:
                self.files.append(p)
        self.reload_analysis()

    def reload_analysis(self):
        self.infos = []
        self.tree.delete(*self.tree.get_children())
        self.item_to_key.clear()
        combo_values = []

        for idx, path in enumerate(self.files):
            try:
                info = analyse_save(path, idx)
                self.infos.append(info)
                prefix = "APPDATA BASE - " if Path(path).resolve() == DEFAULT_BLUEPRINT_PATH.resolve() else ""
                combo_values.append(f"{idx + 1}. {prefix}{Path(path).name}")

                if info.is_empty:
                    self.tree.insert("", "end", text="", values=(Path(path).name, "-", "-", "empty/no array"), open=True)
                    self._log(f"{Path(path).name}: empty/no blueprint array\n")
                    continue

                parent = self.tree.insert(
                    "",
                    "end",
                    text="",
                    values=(Path(path).name, "-", f"{info.visible_estimate} visible estimate", f"{info.internal_count} internal entries"),
                    open=True,
                )

                for e in info.entries:
                    note = "internal/baseline" if e.entry_index == 0 else "visible blueprint"
                    iid = self.tree.insert(parent, "end", text="[ ]", values=(Path(path).name, e.entry_index, e.label, note))
                    self.item_to_key[iid] = (idx, e.entry_index)

                self._log(f"{Path(path).name}: internal={info.internal_count}, visible estimate={info.visible_estimate}\n")
            except Exception as exc:
                self._log(f"{Path(path).name}: ERROR: {exc}\n")

        self.base_combo["values"] = combo_values
        if combo_values:
            if self.base_combo.current() < 0:
                self.base_combo.current(0)
            self.base_index.set(self.base_combo.current() if self.base_combo.current() >= 0 else 0)
        self.update_base_notes()
        self.select_recommended()

    def clear(self):
        self.files.clear()
        self.infos.clear()
        self.tree.delete(*self.tree.get_children())
        self.base_combo["values"] = []
        self.item_to_key.clear()

    def on_base_change(self, event=None):
        idx = self.base_combo.current()
        if idx >= 0:
            self.base_index.set(idx)
        self.update_base_notes()
        self.select_recommended()

    def update_base_notes(self):
        base_idx = self.base_index.get()
        for iid, key in self.item_to_key.items():
            if key is None:
                continue
            file_idx, entry_idx = key
            vals = list(self.tree.item(iid, "values"))
            if file_idx == base_idx:
                vals[3] = "already in base"
                self.tree.item(iid, text="[base]", values=vals)
            else:
                vals[3] = "internal/baseline" if entry_idx == 0 else "visible blueprint"
                self.tree.item(iid, text="[ ]", values=vals)

    def toggle_one(self, iid):
        if iid not in self.item_to_key:
            return
        file_idx, _entry_idx = self.item_to_key[iid]
        if file_idx == self.base_index.get():
            return
        self.tree.item(iid, text="[ ]" if self.tree.item(iid, "text") == "[x]" else "[x]")

    def toggle_item(self, event=None):
        iid = self.tree.identify_row(event.y) if event else None
        if iid:
            self.toggle_one(iid)

    def toggle_selected(self, event=None):
        for iid in self.tree.selection():
            self.toggle_one(iid)
        return "break"

    def select_recommended(self):
        base_idx = self.base_index.get()
        for iid, key in self.item_to_key.items():
            if key is None:
                continue
            file_idx, entry_idx = key
            if file_idx != base_idx and entry_idx != 0:
                self.tree.item(iid, text="[x]")
            elif file_idx == base_idx:
                self.tree.item(iid, text="[base]")
            else:
                self.tree.item(iid, text="[ ]")

    def selected_keys(self) -> set[tuple[int, int]]:
        selected = set()
        for iid, key in self.item_to_key.items():
            if key and self.tree.item(iid, "text") == "[x]":
                selected.add(key)
        return selected

    def merge_to_path(self, out: str):
        result = merge_selected(self.infos, self.base_index.get(), self.selected_keys(), out)
        self._log("\nMERGE COMPLETE\n")
        for k, v in result.items():
            if k != "appended_report":
                self._log(f"{k}: {v}\n")
        self._log("Appended:\n")
        for line in result["appended_report"]:
            self._log(f"  - {line}\n")
        return result

    def merge(self):
        if len(self.infos) < 2:
            messagebox.showerror("Need files", "Load the AppData base and add at least one source .sav file first.")
            return

        out = filedialog.asksaveasfilename(
            title="Save merged blueprint file",
            defaultextension=".sav",
            initialfile="Blueprints_Merged_Selected.sav",
            filetypes=[("Save files", "*.sav"), ("All files", "*.*")],
        )
        if not out:
            return

        try:
            result = self.merge_to_path(out)
            messagebox.showinfo("Merge complete", f"Saved:\n{result['output']}")
        except Exception as exc:
            self._log(f"\nMERGE FAILED: {exc}\n")
            messagebox.showerror("Merge failed", str(exc))

    def merge_to_appdata(self):
        if len(self.infos) < 2:
            messagebox.showerror("Need files", "Load the AppData base and add at least one source .sav file first.")
            return

        if not DEFAULT_SAVE_DIR.exists():
            messagebox.showerror("Folder not found", f"Save folder not found:\n{DEFAULT_SAVE_DIR}")
            return

        if not messagebox.askyesno(
            "Confirm direct write",
            "This will back up your current AppData Blueprints.sav and then overwrite it with the merged save.\n\nContinue?",
        ):
            return

        try:
            backup = DEFAULT_SAVE_DIR / "Blueprints.backup_before_merge.sav"
            if DEFAULT_BLUEPRINT_PATH.exists():
                backup.write_bytes(DEFAULT_BLUEPRINT_PATH.read_bytes())
            result = self.merge_to_path(DEFAULT_BLUEPRINT_PATH)
            messagebox.showinfo(
                "Merge complete",
                f"Merged directly to:\n{DEFAULT_BLUEPRINT_PATH}\n\nBackup created:\n{backup}",
            )
            self.reload_appdata_base()
        except Exception as exc:
            self._log(f"\nMERGE FAILED: {exc}\n")
            messagebox.showerror("Merge failed", str(exc))


def main():
    parser = argparse.ArgumentParser(description="Selectable Starminer blueprint merger.")
    parser.add_argument("files", nargs="*", help="Input .sav files")
    parser.add_argument("-o", "--output", help="Output .sav path")
    parser.add_argument("--base", type=int, default=0, help="Base file index for CLI mode, zero-based")
    parser.add_argument("--all-visible", action="store_true", help="CLI: append all non-base visible entries")
    args = parser.parse_args()

    if args.files:
        if not args.output:
            raise SystemExit("CLI mode requires -o/--output")
        infos = [analyse_save(p, i) for i, p in enumerate(args.files)]
        keys = set()
        if args.all_visible:
            for i, info in enumerate(infos):
                if info.is_empty:
                    continue
                for e in info.entries:
                    if i != args.base and e.entry_index != 0:
                        keys.add((e.file_index, e.entry_index))
        else:
            raise SystemExit("CLI currently supports --all-visible only.")
        result = merge_selected(infos, args.base, keys, args.output)
        print(result)
        return

    App().mainloop()


if __name__ == "__main__":
    main()
