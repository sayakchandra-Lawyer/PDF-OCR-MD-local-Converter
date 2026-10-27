"""`add-folder`, `remove-folder`, `config` and `models`."""

from __future__ import annotations

import sys

from pdf2md.config import CONFIG_PATH, Config, expand, save_config


def cmd_add_folder(args, cfg: Config) -> int:
    path = expand(args.path)
    if not path.is_dir():
        print(f"not a folder: {path}", file=sys.stderr)
        return 1
    target = cfg.watch.projects if args.single else cfg.watch.project_parents
    if str(path) not in target:
        target.append(str(path))
    save_config(cfg)
    kind = "project folder" if args.single else "folder of project folders"
    print(f"Added {kind}: {path}\nRestart the watcher to pick it up: pdf2md autostart restart")
    return 0


def cmd_remove_folder(args, cfg: Config) -> int:
    path = str(expand(args.path))
    cfg.watch.projects = [p for p in cfg.watch.projects if str(expand(p)) != path]
    cfg.watch.project_parents = [p for p in cfg.watch.project_parents if str(expand(p)) != path]
    save_config(cfg)
    print(f"Removed {path} (if it was configured).")
    return 0


def cmd_models(args, cfg: Config) -> int:
    from pdf2md.engine.ollama import OllamaClient, OllamaError

    client = OllamaClient(cfg.llm.ollama_url)
    if not client.available():
        print(f"Ollama is not running at {cfg.llm.ollama_url}. Install it from https://ollama.com/download and start it.")
        return 1
    if args.action == "list":
        print("\n".join(client.models()) or "(no models)")
        return 0
    models = {cfg.llm.vision_model, cfg.llm.page_model}
    for model in sorted(models):
        if client.has_model(model):
            print(f"{model}: already installed")
            continue
        print(f"pulling {model} (one-time download) ...")
        try:
            client.pull(model, on_progress=lambda s: print(f"  {s}", end="\r", flush=True))
            print(f"\n{model}: ready")
        except OllamaError as e:
            print(f"\nfailed to pull {model}: {e}")
            return 1
    return 0


def cmd_config(args, cfg: Config) -> int:
    if not CONFIG_PATH.exists():
        save_config(cfg)
    print(f"# {CONFIG_PATH}\n")
    print(CONFIG_PATH.read_text(encoding="utf-8"))
    return 0
