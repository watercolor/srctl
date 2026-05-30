#!/usr/bin/env python3
"""
srctl - Shadowrocket CLI Controller for macOS
Usage: srctl <command> [args]
"""

import argparse
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time

GROUP_CONTAINER = os.path.expanduser(
    "~/Library/Group Containers/group.com.liguangming.Shadowrocket"
)
SERVER_MGR = os.path.join(GROUP_CONTAINER, "ServerManager")
PREFS_PLIST = os.path.join(
    GROUP_CONTAINER, "Library", "Preferences", "group.com.liguangming.Shadowrocket.plist"
)
PREFS_KEY_UUID = "group.com.liguangming.SelectedServerUUID"
PREFS_KEY_NAME = "group.com.liguangming.SelectedServerName"

_SERVER_CLASSES = {"DLWServer", "Subscribe"}


def _read_prefs():
    if not os.path.exists(PREFS_PLIST):
        return {}
    with open(PREFS_PLIST, "rb") as f:
        return plistlib.load(f)


def _write_prefs_bulk(updates):
    prefs = _read_prefs()
    prefs.update(updates)
    with open(PREFS_PLIST, "wb") as f:
        plistlib.dump(prefs, f, fmt=plistlib.FMT_BINARY)
    _flush_prefs_cache()


def _flush_prefs_cache():
    try:
        subprocess.run(
            ["killall", "cfprefsd"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def get_active_server_uuid():
    prefs = _read_prefs()
    return prefs.get(PREFS_KEY_UUID)


def _is_shadowrocket_running():
    try:
        result = subprocess.run(
            ["pgrep", "-x", "Shadowrocket"],
            capture_output=True, text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _is_vpn_connected():
    try:
        result = subprocess.run(
            ["scutil", "--nc", "list"],
            capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            if "com.liguangming.Shadowrocket" in line and "(Connected)" in line:
                return True
        return False
    except Exception:
        return False


def _quit_shadowrocket(timeout=5.0):
    subprocess.run(
        ["osascript", "-e", 'quit app "Shadowrocket"'],
        capture_output=True,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_shadowrocket_running():
            return True
        time.sleep(0.2)
    return not _is_shadowrocket_running()


def _launch_shadowrocket(timeout=10.0):
    result = subprocess.run(
        ["open", "-a", "Shadowrocket"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"Warning: 'open -a Shadowrocket' returned {result.returncode}: {result.stderr.strip()}", file=sys.stderr)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_shadowrocket_running():
            return True
        time.sleep(0.2)
    print(f"Warning: Shadowrocket did not appear within {timeout}s", file=sys.stderr)
    return _is_shadowrocket_running()


class NSKeyedUnarchiver:
    def __init__(self, plist_data):
        self.objects = plist_data["$objects"]
        self.top = plist_data["$top"]
        self._resolved = {}
        self._class_cache = {}
        self._resolve_class_cache()

    def _resolve_class_cache(self):
        for i, obj in enumerate(self.objects):
            if isinstance(obj, dict) and "$classname" in obj and "$classes" in obj:
                self._class_cache[i] = obj

    def _get_class_by_uid(self, uid):
        if uid in self._class_cache:
            return self._class_cache[uid]
        obj = self.objects[uid] if uid < len(self.objects) else None
        if isinstance(obj, dict) and "$classname" in obj:
            self._class_cache[uid] = obj
            return obj
        return None

    def _resolve_ref(self, uid, depth=0):
        if depth > 50:
            return f"<max depth {uid}>"
        if uid in self._resolved:
            return self._resolved[uid]
        if uid >= len(self.objects):
            return None
        raw = self.objects[uid]
        result = self._decode_obj(raw, uid, depth)
        self._resolved[uid] = result
        return result

    def _decode_obj(self, obj, uid, depth):
        if isinstance(obj, str):
            return None if obj == "$null" else obj
        if isinstance(obj, (int, float, bool)):
            return obj
        if isinstance(obj, list):
            return [self._resolve_ref(ref["CF$UID"], depth + 1)
                    if isinstance(ref, dict) and "CF$UID" in ref else ref
                    for ref in obj]
        if isinstance(obj, dict):
            if "CF$UID" in obj:
                return self._resolve_ref(obj["CF$UID"], depth + 1)
            if "$class" in obj:
                class_uid = obj["$class"].get("CF$UID") if isinstance(obj["$class"], dict) else None
                cls_info = self._get_class_by_uid(class_uid) if class_uid is not None else None
                classname = cls_info.get("$classname") if cls_info else None

                if classname == "NSDate":
                    ns_time = obj.get("NS.time")
                    return f"<NSDate: {ns_time}>" if ns_time is not None else None

                if classname in ("NSString", "NSMutableString"):
                    ns_str = obj.get("NS.string")
                    return ns_str if ns_str is not None else None

                if classname in ("NSArray", "NSMutableArray"):
                    ns_objs = obj.get("NS.objects", [])
                    return [self._resolve_ref(ref["CF$UID"], depth + 1)
                            if isinstance(ref, dict) and "CF$UID" in ref else ref
                            for ref in ns_objs]

                if classname in ("NSDictionary", "NSMutableDictionary"):
                    ns_keys = obj.get("NS.keys", [])
                    ns_vals = obj.get("NS.objects", [])
                    keys = [self._resolve_ref(k["CF$UID"], depth + 1) for k in ns_keys]
                    vals = [self._resolve_ref(v["CF$UID"], depth + 1) for v in ns_vals]
                    return dict(zip(keys, vals))

                if classname == "DLWServer":
                    return self._decode_server(obj, depth)
                if classname == "Subscribe":
                    return self._decode_subscribe(obj, depth)

                result = {}
                for k, v in obj.items():
                    if k.startswith("$"):
                        continue
                    if isinstance(v, dict) and "CF$UID" in v:
                        result[k] = self._resolve_ref(v["CF$UID"], depth + 1)
                    else:
                        result[k] = v
                if classname:
                    result["_class"] = classname
                return result

            return {k: v for k, v in obj.items() if not k.startswith("$")}
        return obj

    def _decode_server(self, obj, depth):
        server = {}
        for k, v in obj.items():
            if k.startswith("$"):
                continue
            if isinstance(v, dict) and "CF$UID" in v:
                server[k] = self._resolve_ref(v["CF$UID"], depth + 1)
            else:
                server[k] = v
        server["_class"] = "DLWServer"
        return server

    def _decode_subscribe(self, obj, depth):
        sub = {}
        for k, v in obj.items():
            if k.startswith("$"):
                continue
            if isinstance(v, dict) and "CF$UID" in v:
                sub[k] = self._resolve_ref(v["CF$UID"], depth + 1)
            else:
                sub[k] = v
        sub["_class"] = "Subscribe"
        return sub

    def decode_all(self):
        root_ref = self.top.get("root", {})
        if not root_ref:
            return {}
        top = self._resolve_ref(root_ref["CF$UID"])
        result = {}
        if isinstance(top, dict):
            for key, obj in top.items():
                if isinstance(obj, dict) and obj.get("_class") in _SERVER_CLASSES:
                    label = obj.get("title") or obj.get("host") or str(key)
                    result[label] = {k: v for k, v in obj.items()}
            return result
        if isinstance(top, list):
            for i, obj in enumerate(top):
                if isinstance(obj, dict) and obj.get("_class") in _SERVER_CLASSES:
                    label = obj.get("title") or obj.get("host") or f"server-{i}"
                    result[label] = {k: v for k, v in obj.items()}
        return result


def load_servers():
    if not os.path.exists(SERVER_MGR):
        return {}
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        subprocess.run(
            ["plutil", "-convert", "xml1", "-o", tmp_path, SERVER_MGR],
            check=True, capture_output=True,
        )
        with open(tmp_path, "rb") as f:
            plist_data = plistlib.load(f)
        ua = NSKeyedUnarchiver(plist_data)
        return ua.decode_all()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _find_server(servers, identifier):
    if not servers:
        return None
    for name, srv in servers.items():
        if srv.get("uuid") == identifier:
            return name, srv
    for name, srv in servers.items():
        if identifier.lower() in name.lower():
            return name, srv
    try:
        idx = int(identifier)
        items = list(servers.items())
        if 0 <= idx < len(items):
            return items[idx]
    except ValueError:
        pass
    return None


def _format_server_row(i, srv, active_uuid):
    marker = "*" if srv.get("uuid") == active_uuid else " "
    host = str(srv.get("host", "") or "")
    port = str(srv.get("port", "") or "")
    stype = str(srv.get("type", "") or "")
    title = str(srv.get("title", "") or "")[:38]
    return f"{i:<5} [{marker}] {title:<40} {host:<25} {port:<8} {stype:<8}"


def cmd_on(*args):
    subprocess.run(["open", "rocket://connect"], capture_output=True)
    print("VPN connect sent")


def cmd_off(*args):
    subprocess.run(["open", "rocket://disconnect"], capture_output=True)
    print("VPN disconnect sent")


def cmd_toggle(*args):
    subprocess.run(["open", "rocket://toggle"], capture_output=True)
    print("VPN toggle sent")


def cmd_list(args):
    servers = load_servers()
    if not servers:
        print("No servers found.")
        return
    active_uuid = get_active_server_uuid()
    print(f"{'#':<5} {'':<2} {'Title':<40} {'Host':<25} {'Port':<8} {'Type':<8}")
    print("-" * 95)
    for i, (name, srv) in enumerate(servers.items()):
        print(_format_server_row(i, srv, active_uuid))
    print(f"\nTotal: {len(servers)} servers. [*] = active")


def cmd_switch(args):
    servers = load_servers()
    if not servers:
        print("No servers found.", file=sys.stderr)
        return
    found = _find_server(servers, args.server)
    if not found:
        print(f"No server matching '{args.server}' found.", file=sys.stderr)
        return
    name, srv = found
    uuid = srv.get("uuid")
    title = srv.get("title", name)
    if not uuid:
        print("Server has no UUID.", file=sys.stderr)
        return

    old_uuid = get_active_server_uuid()
    if old_uuid == uuid and not _is_shadowrocket_running():
        # Same selection and no app to refresh; nothing to do.
        print(f"Already selected: {title}")
        return

    was_running = _is_shadowrocket_running()
    was_connected = was_running and _is_vpn_connected()

    if was_running:
        print("Quitting Shadowrocket...")
        if not _quit_shadowrocket():
            print("Failed to quit Shadowrocket within timeout.", file=sys.stderr)
            sys.exit(1)

    _write_prefs_bulk({
        PREFS_KEY_UUID: uuid,
        PREFS_KEY_NAME: title,
    })

    host = srv.get("host", "")
    port = srv.get("port", "")
    print(f"Switched to: {title} ({host}:{port})")

    if was_running:
        print("Relaunching Shadowrocket...")
        if not _launch_shadowrocket():
            print("Failed to relaunch Shadowrocket.", file=sys.stderr)
            sys.exit(1)
        if was_connected:
            # Give the app a moment to register its URL handler.
            time.sleep(1.0)
            subprocess.run(["open", "rocket://connect"], capture_output=True)
            print("VPN reconnect sent")


def cmd_config(args):
    servers = load_servers()
    found = _find_server(servers, args.server)
    if not found:
        print(f"No server matching '{args.server}' found.", file=sys.stderr)
        return
    name, srv = found
    active_uuid = get_active_server_uuid()
    is_active = srv.get("uuid") == active_uuid
    print(f"Name:     {name}")
    print(f"Status:   {'ACTIVE' if is_active else 'inactive'}")
    for key in sorted(k for k in srv if not k.startswith("_")):
        val = srv[key]
        if val is not None:
            print(f"  {key:<15}: {val}")


def cmd_set(args):
    servers = load_servers()
    found = _find_server(servers, args.server)
    if not found:
        print(f"No server matching '{args.server}' found.", file=sys.stderr)
        return
    name, srv = found
    print(f"Server: {name}")
    print(f"Setting '{args.key}' = '{args.value}'")
    print("Note: Direct modification of ServerManager archive is not supported.")
    print("Shadowrocket will overwrite manual changes on next save.")


def cmd_export(args):
    servers = load_servers()
    if not servers:
        print("{}")
        return
    clean = {}
    for name, srv in servers.items():
        clean[name] = {k: v for k, v in srv.items()
                       if not k.startswith("_") and not isinstance(v, dict)}
    print(json.dumps(clean, indent=2, ensure_ascii=False, default=str))


def cmd_active(args):
    servers = load_servers()
    uuid = get_active_server_uuid()
    if uuid:
        found = _find_server(servers, uuid)
        if found:
            name, srv = found
            print(f"{name}  ({srv.get('host', '')}:{srv.get('port', '')})  [{srv.get('type', '')}]")
        else:
            print(f"UUID: {uuid} (not in server list)")
    else:
        print("No active server")


def main():
    parser = argparse.ArgumentParser(
        description="Shadowrocket CLI Controller",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  srctl on              Start VPN
  srctl off             Stop VPN
  srctl toggle          Toggle VPN
  srctl list            List all servers
  srctl switch "德国"    Switch to server
  srctl config "香港"    Show server config
  srctl active          Show current server
  srctl export          Export all servers as JSON
        """
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("on", help="Connect VPN")
    sub.add_parser("off", help="Disconnect VPN")
    sub.add_parser("toggle", help="Toggle VPN")
    sub.add_parser("list", help="List all servers")
    sub.add_parser("active", help="Show active server")
    sp = sub.add_parser("switch", help="Switch active server")
    sp.add_argument("server", help="Server index, UUID, or name substring")
    sp = sub.add_parser("config", help="Show server configuration")
    sp.add_argument("server", help="Server index, UUID, or name substring")
    sp = sub.add_parser("set", help="Modify server property")
    sp.add_argument("server", help="Server index, UUID, or name substring")
    sp.add_argument("key", help="Property name")
    sp.add_argument("value", help="New value")
    sub.add_parser("export", help="Export all servers as JSON")

    args = parser.parse_args()

    commands = {
        "on": cmd_on, "off": cmd_off, "toggle": cmd_toggle,
        "list": cmd_list, "switch": cmd_switch, "config": cmd_config,
        "set": cmd_set, "export": cmd_export, "active": cmd_active,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
