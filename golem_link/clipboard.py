"""Short text relay between paired nodes.

A text copied on one machine can be sent to another and placed on its desktop
clipboard. Everything is explicit: the sender picks the target and presses
send; the receiver stores the item, attempts an automatic copy and otherwise
leaves it in the inbox with a manual Copy button. Only text is relayed, never
files or images.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from .clipboard_session import ClipboardUnavailable, copy_text, read_text
from .config import ConfigManager
from .db import Database
from .events import EventBus

log = logging.getLogger(__name__)

MAX_ITEMS_IN_VIEW = 20


class ClipboardBridge:
    def __init__(self, cfg: ConfigManager, db: Database, bus: EventBus) -> None:
        self.cfg = cfg
        self.db = db
        self.bus = bus

    # -- views -------------------------------------------------------------
    @staticmethod
    def _view(row: dict, *, full: bool = True) -> dict:
        item = {
            "id": row["id"],
            "ts": row["ts"],
            "from_node": row["from_node"],
            "from_name": row["from_name"],
            "chars": row["chars"],
            "copied": bool(row.get("copied_at")),
            "copied_at": row.get("copied_at"),
            "copy_method": row.get("copy_method"),
            "copy_error": row.get("copy_error"),
            "preview": row["text"][:160],
        }
        if full:
            item["text"] = row["text"]
        return item

    def recent(self, limit: int = MAX_ITEMS_IN_VIEW) -> list[dict]:
        return [self._view(row) for row in self.db.list_clipboard_items(limit)]

    def status(self) -> dict:
        cfg = self.cfg.get().clipboard
        rows = self.db.list_clipboard_items(1)
        last = rows[0] if rows else None
        return {
            "enabled": cfg.enabled,
            "auto_copy": cfg.auto_copy,
            "max_chars": cfg.max_chars,
            "keep_items": cfg.keep_items,
            "last_item": self._view(last, full=False) if last else None,
        }

    # -- outgoing ----------------------------------------------------------
    async def send(self, peers, node_id: str, text: str) -> dict:
        cfg = self.cfg.get().clipboard
        if not cfg.enabled:
            raise ValueError("text relay is disabled on this node")
        if not text or not text.strip():
            raise ValueError("the text is empty")
        if len(text) > cfg.max_chars:
            raise ValueError(f"the text is longer than {cfg.max_chars} characters")
        peer = peers.peers.get(node_id)
        if peer is None:
            raise ValueError("unknown peer")
        if peer.status != "online":
            raise ValueError(f"{peer.name} is offline")
        if not peer.remote_allows_control:
            raise ValueError(f"{peer.name} does not grant control to this node")
        try:
            answer = await peers.client.post_json(
                peer.target(),
                "/peer/clipboard",
                {"text": text, "from_name": self.cfg.get().node_name},
                timeout=30,
            )
        except Exception as exc:
            detail = str(exc)
            if "HTTP 404" in detail:
                raise ValueError(
                    f"{peer.name} runs an older GOLEM LINK without text relay"
                ) from exc
            if "HTTP 403" in detail:
                raise ValueError(f"{peer.name} does not accept text from this node") from exc
            raise ValueError(f"could not reach {peer.name}: {detail}") from exc
        return {
            "ok": True,
            "peer": peer.name,
            "peer_id": peer.node_id,
            "chars": len(text),
            "copied": bool(answer.get("copied")),
            "copy_method": answer.get("copy_method"),
            "copy_error": answer.get("copy_error"),
        }

    async def send_all(self, peers, text: str) -> dict:
        cfg = self.cfg.get().clipboard
        if not cfg.enabled:
            raise ValueError("text relay is disabled on this node")
        if not text or not text.strip():
            raise ValueError("the text is empty")
        if len(text) > cfg.max_chars:
            raise ValueError(f"the text is longer than {cfg.max_chars} characters")
        targets = [
            peer
            for peer in peers.peers.values()
            if peer.status == "online" and peer.remote_allows_control
        ]
        if not targets:
            raise ValueError("no paired device is online and grants control")
        results = []
        for peer in targets:
            try:
                answer = await self.send(peers, peer.node_id, text)
                results.append(
                    {
                        "peer_id": peer.node_id,
                        "peer": peer.name,
                        "ok": True,
                        "copied": answer["copied"],
                        "copy_method": answer["copy_method"],
                        "error": None,
                    }
                )
            except ValueError as exc:
                # One unreachable device must not block the rest of the relay.
                results.append(
                    {
                        "peer_id": peer.node_id,
                        "peer": peer.name,
                        "ok": False,
                        "copied": False,
                        "copy_method": None,
                        "error": str(exc),
                    }
                )
        return {
            "ok": True,
            "chars": len(text),
            "sent": sum(1 for row in results if row["ok"]),
            "copied": sum(1 for row in results if row["copied"]),
            "results": results,
        }

    # -- incoming ----------------------------------------------------------
    def receive(self, from_node: str, from_name: str, text: str) -> dict:
        cfg = self.cfg.get().clipboard
        if not cfg.enabled:
            raise ValueError("text relay is disabled on this node")
        if not text or not text.strip():
            raise ValueError("the text is empty")
        if len(text) > cfg.max_chars:
            raise ValueError(f"the text is longer than {cfg.max_chars} characters")
        item = {
            "id": "c-" + uuid.uuid4().hex[:12],
            "ts": time.time(),
            "from_node": from_node,
            "from_name": from_name or from_node,
            "text": text,
            "chars": len(text),
            "copied_at": None,
            "copy_method": None,
            "copy_error": None,
        }
        self.db.insert_clipboard_item(item)
        self.db.prune_clipboard_items(cfg.keep_items)
        if cfg.auto_copy:
            self._try_copy(item["id"], text)
        row = self.db.get_clipboard_item(item["id"]) or item
        view = self._view(row, full=False)
        self.bus.publish("clipboard", view)
        return view

    def _try_copy(self, item_id: str, text: str) -> dict:
        try:
            method = copy_text(text, data_dir=self.cfg.data_dir)
        except ClipboardUnavailable as exc:
            self.db.mark_clipboard_copied(item_id, None, str(exc))
            log.info("clipboard item %s stored but not copied: %s", item_id, exc)
            return {"copied": False, "method": None, "error": str(exc)}
        except Exception as exc:  # pragma: no cover - unexpected platform failure
            log.warning("clipboard copy failed", exc_info=True)
            self.db.mark_clipboard_copied(item_id, None, str(exc))
            return {"copied": False, "method": None, "error": str(exc)}
        self.db.mark_clipboard_copied(item_id, method)
        log.info("clipboard item %s copied via %s", item_id, method)
        return {"copied": True, "method": method, "error": None}

    def copy_item(self, item_id: str, text: str | None = None) -> dict:
        row = self.db.get_clipboard_item(item_id)
        if row is None:
            raise ValueError("unknown clipboard item")
        result = self._try_copy(item_id, text if text is not None else row["text"])
        updated = self.db.get_clipboard_item(item_id) or row
        view = self._view(updated, full=False)
        self.bus.publish("clipboard", view)
        return {"id": item_id, **result}

    def copy_local(self, text: str) -> dict:
        cfg = self.cfg.get().clipboard
        if not text or not text.strip():
            raise ValueError("the text is empty")
        if len(text) > cfg.max_chars:
            raise ValueError(f"the text is longer than {cfg.max_chars} characters")
        try:
            method = copy_text(text, data_dir=self.cfg.data_dir)
        except ClipboardUnavailable as exc:
            return {"copied": False, "method": None, "error": str(exc)}
        except Exception as exc:  # pragma: no cover - unexpected platform failure
            log.warning("local clipboard copy failed", exc_info=True)
            return {"copied": False, "method": None, "error": str(exc)}
        return {"copied": True, "method": method, "error": None}

    def read_local(self) -> dict:
        cfg = self.cfg.get().clipboard
        if not cfg.enabled:
            raise ValueError("text relay is disabled on this node")
        try:
            text = read_text(data_dir=self.cfg.data_dir)
        except ClipboardUnavailable as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:  # pragma: no cover - unexpected platform failure
            log.warning("local clipboard read failed", exc_info=True)
            raise ValueError(str(exc)) from exc
        if len(text) > cfg.max_chars:
            raise ValueError(
                f"this machine's clipboard is longer than {cfg.max_chars} characters"
            )
        return {"text": text, "chars": len(text)}

    async def read_local_async(self) -> dict:
        return await asyncio.to_thread(self.read_local)

    def delete_item(self, item_id: str) -> dict:
        if not self.db.delete_clipboard_item(item_id):
            raise ValueError("unknown clipboard item")
        self.bus.publish("clipboard-deleted", {"id": item_id})
        return {"deleted": 1, "id": item_id}

    def clear(self) -> dict:
        removed = self.db.clear_clipboard_items()
        self.bus.publish("clipboard-deleted", {"id": None, "cleared": removed})
        return {"cleared": removed}

    async def receive_async(self, from_node: str, from_name: str, text: str) -> dict:
        return await asyncio.to_thread(self.receive, from_node, from_name, text)

    async def copy_item_async(self, item_id: str, text: str | None = None) -> dict:
        return await asyncio.to_thread(self.copy_item, item_id, text)

    async def copy_local_async(self, text: str) -> dict:
        return await asyncio.to_thread(self.copy_local, text)
