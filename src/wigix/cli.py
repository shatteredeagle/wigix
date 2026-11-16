import argparse
import logging

from .engine import Dashboard, load_config


def main():
    ap = argparse.ArgumentParser(prog="wigix")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the dashboard on the panel")
    run.add_argument("-c", "--config", required=True)
    pv = sub.add_parser("preview", help="render page 1 to a PNG, no hardware needed")
    pv.add_argument("-c", "--config", required=True)
    pv.add_argument("-o", "--out", default="preview.png")
    ed = sub.add_parser("editor", help="open the web editor (the running daemon hot-reloads saved changes)")
    ed.add_argument("-c", "--config", required=True)
    ed.add_argument("--host", default="127.0.0.1")
    ed.add_argument("--port", type=int, default=8765)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if a.cmd == "editor":
        from .editor import serve
        serve(a.config, a.host, a.port)
        return
    try:
        dash = Dashboard(load_config(a.config), a.config)
    except (ValueError, KeyError) as e:
        raise SystemExit(f"config error: {e}")
    if a.cmd == "run":
        try:
            dash.run()
        except KeyboardInterrupt:
            pass
    else:
        dash.preview(a.out)
        print("wrote", a.out)
