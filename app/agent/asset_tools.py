"""asset_generate: one generated hero object for the episode (local FLUX on the owner's GPU, cut out to a
transparent PNG in assets/). Gated like the voiceover (GPU work): a draft's start dialog OKs it once."""
import json

from ..assets import AssetError


def tools(studio):
    from .studio import Tool
    assets, pid, store = studio.assets, studio.pid, studio.store

    async def generate(args):
        art = None
        if args.get("art"):
            try:
                art = json.loads(store.file_path(pid, str(args["art"])).read_text(encoding="utf-8-sig"))
            except Exception:                                # noqa: BLE001 - the look is optional
                art = None
        try:
            res = await assets.agenerate(pid, str(args.get("name") or ""), str(args.get("prompt") or ""), art,
                                         variant=int(args.get("variant") or 0), kind=str(args.get("kind") or "object"))
        except (AssetError, ValueError) as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({k: res[k] for k in ("name", "file", "w", "h", "reused", "seconds") if k in res}
                          | {"next": "Read the PNG to check it; use it in a shot with "
                                   + ("S.scene(name) as the shot's background." if args.get("kind") == "scene"
                                      else "S.image(name, x, y, h).")})

    return [Tool("asset_generate", "Generate ONE isolated object as a transparent PNG for the shots (local FLUX on the "
                 "owner's graphics card, ~10 s; the episode's look is added from art). Objects only - buildings, vault "
                 "doors, gold bars, coins, banknotes, maps, machines, a generic person figure; never a real person, a "
                 "logo or text. Saved as assets/<name>.png and listed for S.image(name, x, y, h). Same name + prompt "
                 "returns the saved file; variant=1 or 2 makes another take of a bad result. kind=\"scene\" makes a "
                 "full-frame background plate instead (a vault interior, a central bank hall, a trading floor, a port at "
                 "night - a place, no people), used with S.scene(name).",
                 {"type": "object", "properties": {
                     "name": {"type": "string", "description": "short-kebab-name, e.g. vault-door"},
                     "prompt": {"type": "string", "description": "the object, its material and angle"},
                     "art": {"type": "string", "description": "the episode's art.json (for one look across assets)"},
                     "variant": {"type": "integer", "description": "0 (default) or 1-2 for another take"},
                     "kind": {"type": "string", "enum": ["object", "scene"],
                              "description": "object (default, cut out) or scene (a full-frame background place)"}},
                  "required": ["name", "prompt"]}, generate)]
