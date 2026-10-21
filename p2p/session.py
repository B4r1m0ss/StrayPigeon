from __future__ import annotations

import base64
import json
import os
import queue
import secrets
import select
import socket
import sys
import threading
import time

from . import stun
from .candidate import Candidate
from .config import Config
from .log import JsonlLogger
from .net import bind_udp, fmt_addr, parse_addr
from .punch import punch
from .signal.file import FileChannel

try:  # optional dependency: only needed when a PSK is configured
    from .keyexchange import (
        KeyPair,
        derive_session_key,
        open_frame,
        open_signal,
        seal_frame,
        seal_signal,
    )

    HAVE_KX = True
except Exception:  # noqa: BLE001
    HAVE_KX = False


def is_own_message(msg: dict, name: str, instance_id: str) -> bool:
    """Return True if this candidate was produced by this process.

    The visible name can be the same on both sides in shared-mailbox test
    mode (both use the mailbox address). The per-process instance id keeps
    them distinguishable; fall back to the name for older peers that do not
    send an instance id.
    """
    peer_instance = str(msg.get("instance") or "")
    if peer_instance:
        return peer_instance == instance_id
    return str(msg.get("name", "")) == name


class Session:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = JsonlLogger(cfg.log_dir, cfg.name)
        self.instance_id = secrets.token_hex(8)
        self.sock: socket.socket | None = None
        self.chan = None
        self.sid = ""
        self.peer_addr: tuple[str, int] | None = None
        self.my_lease = ""
        self.my_candidates: list[Candidate] = []
        self.peer_candidates: list[Candidate] = []
        self.nat_mapping = "unknown"
        self.seen_sids: set[str] = set()
        self._stop = threading.Event()
        # GUI embedding: no stdin reads, no CHAT prints; outbox for sends.
        self.gui = False
        self.event_sink = None
        self.outbox: queue.Queue[str | None] = queue.Queue()

        try:
            self.psk: bytes | None = cfg.psk_bytes()
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if self.psk is not None and not HAVE_KX:
            raise SystemExit("PSK is set but cryptography is not installed: pip install cryptography")
        self.kp = KeyPair.generate() if self.psk is not None else None
        self.session_key: bytes | None = None
        self.send_seq = 0
        self.recv_seq = 0

    def _emit(self, kind: str, **payload) -> None:
        sink = self.event_sink
        if sink is None:
            return
        try:
            sink(kind, payload)
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        self._stop.set()
        try:
            self.outbox.put_nowait(None)
        except queue.Full:
            pass

    # ---- setup ---------------------------------------------------------
    def _channel(self):
        if self.cfg.signal_mode == "file":
            return FileChannel(self.cfg.shared_dir, self.cfg.name, self.cfg.peer)
        if self.cfg.signal_mode == "imap":
            host = os.environ.get("P2P_IMAP_HOST", "").strip()
            user = os.environ.get("P2P_IMAP_USER", "").strip()
            password = os.environ.get("P2P_IMAP_PASSWORD", "")
            folder = os.environ.get("P2P_IMAP_FOLDER", "p2p-demo").strip()
            if not (host and user and password):
                raise SystemExit("IMAP mode needs P2P_IMAP_HOST / P2P_IMAP_USER / P2P_IMAP_PASSWORD")
            from .signal.imap_store import ImapStoreChannel

            return ImapStoreChannel(host, user, password, folder)
        if self.cfg.signal_mode == "smtp":
            smtp_host = os.environ.get("P2P_SMTP_HOST", "").strip()
            smtp_port_raw = os.environ.get("P2P_SMTP_PORT", "465").strip() or "465"
            my_email = os.environ.get("P2P_MY_EMAIL", "").strip()
            peer_email = os.environ.get("P2P_PEER_EMAIL", "").strip()
            imap_host = os.environ.get("P2P_IMAP_HOST", "").strip()
            user = os.environ.get("P2P_IMAP_USER", "").strip()
            password = os.environ.get("P2P_IMAP_PASSWORD", "")
            folder = os.environ.get("P2P_SMTP_FOLDER", "INBOX").strip() or "INBOX"
            if not (smtp_host and my_email and peer_email and imap_host and user and password):
                raise SystemExit(
                    "SMTP mode needs P2P_SMTP_HOST / P2P_MY_EMAIL / P2P_PEER_EMAIL / "
                    "P2P_IMAP_HOST / P2P_IMAP_USER / P2P_IMAP_PASSWORD"
                )
            try:
                smtp_port = int(smtp_port_raw)
            except ValueError as exc:
                raise SystemExit("P2P_SMTP_PORT must be an integer") from exc
            starttls = os.environ.get("P2P_SMTP_STARTTLS", "0").lower() in (
                "1",
                "true",
                "yes",
            )
            from .signal.smtp_mail import SmtpMailChannel

            return SmtpMailChannel(
                smtp_host,
                smtp_port,
                my_email,
                peer_email,
                imap_host,
                user,
                password,
                folder,
                starttls,
            )
        raise SystemExit(f"signal mode {self.cfg.signal_mode!r} is not implemented yet; use --signal file")

    def _collect_candidates(self) -> None:
        assert self.sock is not None
        now = time.time()
        self.my_lease = secrets.token_hex(8)
        host_ip = self.cfg.advertise_host or "127.0.0.1"
        cands = [
            Candidate("ipv4", host_ip, self.cfg.port, "host", self.my_lease, now, now + 45.0, "unknown")
        ]
        srflx: list[Candidate] = []
        for server in list(self.cfg.stun_servers)[:2]:
            try:
                ip, port = stun.query(self.sock, server, timeout=self.cfg.stun_timeout)
                c = Candidate(
                    "ipv4", ip, port, "srflx", self.my_lease, now, now + 45.0, "unknown"
                )
                cands.append(c)
                srflx.append(c)
                self.log.event("STUN_OK", server=server, mapped=fmt_addr((ip, port)))
            except Exception as exc:  # noqa: BLE001
                self.log.event("STUN_FAIL", server=server, detail=str(exc))

        if len(srflx) >= 2:
            self.nat_mapping = "eim" if srflx[0].port == srflx[1].port else "apdm"
        else:
            self.nat_mapping = "unknown"
        for c in cands:
            c.mapping = self.nat_mapping
        self.my_candidates = cands
        self.log.event("CANDIDATES_READY", count=len(cands), nat=self.nat_mapping)

    def _send_candidate(self, seq: int) -> None:
        msg = {
            "type": "CANDIDATE",
            "name": self.cfg.name,
            "instance": self.instance_id,
            "sid": self.sid,
            "ts": time.time(),
            "candidates": [c.to_dict() for c in self.my_candidates],
            "nat": self.nat_mapping,
            "pub": self.kp.public_b64() if self.kp is not None else None,
        }
        blob = json.dumps(msg).encode("utf-8")
        if self.psk is not None:
            envelope = seal_signal(self.psk, self.sid, seq, "CANDIDATE", blob)
            blob = json.dumps(envelope).encode("utf-8")
            self.log.event("SIGNAL_SEALED", seq=seq, type="CANDIDATE")
        self.chan.send(self.sid, seq, blob)
        self.log.event("CANDIDATE_SENT", count=len(self.my_candidates), nat=self.nat_mapping, seq=seq)

    def _recv_candidate(self, after: int, allow_any_sid: bool):
        deadline = time.time() + self.cfg.signal_timeout
        last = after
        while time.time() < deadline:
            if self._stop.is_set():
                raise TimeoutError("stopped by user")
            for seq, blob in self.chan.recv("" if allow_any_sid else self.sid, last):
                try:
                    if self.psk is not None:
                        envelope = json.loads(blob)
                        if not isinstance(envelope, dict):
                            continue
                        if self.sid and str(envelope.get("sid")) != self.sid:
                            continue
                        plain = open_signal(self.psk, envelope)
                        msg = json.loads(plain)
                    else:
                        msg = json.loads(blob)
                except Exception as exc:  # noqa: BLE001
                    self.log.event("ERROR", code="E_AUTH", detail=type(exc).__name__)
                    continue

                # A shared folder/INBOX contains our own messages too.
                if is_own_message(msg, self.cfg.name, self.instance_id):
                    continue
                if msg.get("type") != "CANDIDATE":
                    continue
                try:
                    msg_ts = float(msg.get("ts", 0.0))
                except (TypeError, ValueError):
                    msg_ts = 0.0
                if abs(time.time() - msg_ts) > self.cfg.replay_window:
                    self.log.event("ERROR", code="E_REPLAY", detail="stale timestamp")
                    continue
                sid = str(msg.get("sid") or "")
                if self.sid and sid != self.sid:
                    continue
                if sid and sid in self.seen_sids:
                    self.log.event("ERROR", code="E_REPLAY", detail="duplicate sid")
                    continue
                if not self.sid:
                    self.sid = sid
                self.seen_sids.add(self.sid)
                return seq, msg
            time.sleep(0.1)
        raise TimeoutError("no peer candidate before timeout")

    def _peer_candidates_from_msg(self, msg: dict) -> list[Candidate]:
        out: list[Candidate] = []
        for raw in msg.get("candidates") or []:
            try:
                c = Candidate.from_dict(raw)
            except (KeyError, TypeError, ValueError):
                continue
            if c.family != "ipv4":
                continue
            if c.expires_at and not c.alive():
                continue
            out.append(c)
        if not out and msg.get("addr"):
            try:
                ip, port = parse_addr(str(msg["addr"]))
                now = time.time()
                out = [Candidate("ipv4", ip, port, "srflx", "", now, now + 45.0, "unknown")]
            except ValueError:
                pass
        out.sort(key=lambda c: 0 if c.kind == "srflx" else 1)
        return out

    # ---- key exchange --------------------------------------------------
    def _derive_session_key(self, peer_msg: dict) -> bool:
        if self.psk is None:
            return True
        peer_pub_b64 = peer_msg.get("pub")
        if not peer_pub_b64 or self.kp is None:
            self.log.event("ERROR", code="E_KEYEXCHANGE", detail="missing peer public key")
            return False
        try:
            peer_pub = base64.b64decode(str(peer_pub_b64), validate=True)
            dh = self.kp.dh(str(peer_pub_b64))
            if self.cfg.role == "initiator":
                initiator_pub, responder_pub = self.kp.public_bytes, peer_pub
            else:
                initiator_pub, responder_pub = peer_pub, self.kp.public_bytes
            self.session_key = derive_session_key(dh, self.sid, initiator_pub, responder_pub)
        except Exception as exc:  # noqa: BLE001
            self.log.event("ERROR", code="E_KEYEXCHANGE", detail=type(exc).__name__)
            return False
        self.log.event("SESSION_KEY_READY", mode="aead")
        return True

    # ---- main flow -----------------------------------------------------
    def run(self) -> int:
        try:
            self.sock = bind_udp(self.cfg.host, self.cfg.port)
            self.chan = self._channel()
            self.log.event("INFO", msg="bound", **self.cfg.redacted())
            self._emit("state", state="bound", name=self.cfg.name, peer=self.cfg.peer)
            self._collect_candidates()

            if self.cfg.role == "initiator":
                self.sid = secrets.token_hex(16)
                self._send_candidate(1)
                _, msg = self._recv_candidate(0, allow_any_sid=False)
            else:
                _, msg = self._recv_candidate(0, allow_any_sid=True)
                self._send_candidate(1)

            self.peer_candidates = self._peer_candidates_from_msg(msg)
            if not self.peer_candidates:
                self.log.event("ERROR", code="E_NO_CANDIDATE")
                self._emit("error", code="E_NO_CANDIDATE")
                return 6
            self.peer_addr = (self.peer_candidates[0].ip, self.peer_candidates[0].port)
            self.log.event(
                "INFO",
                msg="peer_ready",
                peer=fmt_addr(self.peer_addr),
                peer_nat=str(msg.get("nat") or "unknown"),
                peer_candidates=len(self.peer_candidates),
            )
            self._emit(
                "state",
                state="peer_ready",
                peer=fmt_addr(self.peer_addr),
                nat=str(msg.get("nat") or "unknown"),
                candidates=len(self.peer_candidates),
            )

            if not self._derive_session_key(msg):
                self._emit("error", code="E_KEY_EXCHANGE")
                return 4

            peer_nat = str(msg.get("nat") or "unknown")
            if self.nat_mapping == "apdm" and peer_nat == "apdm":
                self.log.event("ERROR", code="E_SYMMETRIC_NAT")
                self._emit("error", code="E_SYMMETRIC_NAT")
                return 5

            self._emit(
                "state",
                state="punching",
                peer=fmt_addr(self.peer_addr),
                candidates=len(self.peer_candidates),
                window=self.cfg.punch_window,
            )
            addr = punch(
                self.sock,
                [(c.ip, c.port) for c in self.peer_candidates],
                self.sid,
                self.my_lease,
                psk=self.psk,
                interval=self.cfg.punch_interval,
                window=self.cfg.punch_window,
                logger=self.log,
                avoid={(c.ip, c.port) for c in self.my_candidates},
                stop_event=self._stop,
            )
            if addr is None:
                self.log.event("ERROR", code="E_NO_PATH")
                self._emit("error", code="E_NO_PATH")
                return 2

            self.peer_addr = addr
            self.log.event("CONNECTED", peer=fmt_addr(addr))
            self._emit("state", state="connected", peer=fmt_addr(addr))
            self._chat()
            return 0
        except TimeoutError as exc:
            self.log.event("ERROR", code="E_SIGNAL_TIMEOUT", detail=str(exc))
            self._emit("error", code="E_SIGNAL_TIMEOUT", detail=str(exc))
            return 3
        finally:
            self._close()

    # ---- chat ----------------------------------------------------------
    def _send_chat(self, text: str) -> None:
        assert self.sock is not None and self.peer_addr is not None
        if self.session_key is not None:
            self.send_seq += 1
            frame = seal_frame(self.session_key, self.send_seq, text.encode("utf-8"))
            self.sock.sendto(b"DATA " + frame, self.peer_addr)
            self.log.event("DATA_TX", seq=self.send_seq, chars=len(text))
        else:
            self.sock.sendto(b"CHAT " + text.encode("utf-8"), self.peer_addr)
            self.log.event("CHAT_TX", chars=len(text))

    def _pump_chat(self, timeout: float) -> None:
        assert self.sock is not None
        r, _, _ = select.select([self.sock], [], [], timeout)
        if not r:
            return
        try:
            data, addr = self.sock.recvfrom(4096)
        except OSError:
            return
        if data.startswith(b"P2P1"):
            return  # a late punch packet; ignore

        if data.startswith(b"PING"):
            try:
                self.sock.sendto(b"PONG", addr)
            except OSError:
                pass
            return
        if data.startswith(b"PONG"):
            return

        if data.startswith(b"DATA "):
            if self.session_key is None:
                self.log.event("ERROR", code="E_AUTH", detail="data frame without session key")
                return
            try:
                seq, plain = open_frame(self.session_key, data[5:], self.recv_seq)
            except Exception as exc:  # noqa: BLE001
                self.log.event("ERROR", code="E_AUTH", detail=type(exc).__name__)
                return
            self.recv_seq = seq
            text = plain.decode("utf-8", errors="replace")
            if self.gui:
                self._emit("chat", text=text, peer=fmt_addr(addr))
            else:
                print(f"[{self.cfg.name}] CHAT {text}", flush=True)
            self.log.event("DATA_RX", peer=fmt_addr(addr), seq=seq, chars=len(text))
            return

        if data.startswith(b"CHAT "):
            if self.session_key is not None:
                self.log.event("ERROR", code="E_AUTH", detail="plaintext frame in encrypted mode")
                return
            text = data[5:].decode("utf-8", errors="replace")
            if self.gui:
                self._emit("chat", text=text, peer=fmt_addr(addr))
            else:
                print(f"[{self.cfg.name}] CHAT {text}", flush=True)
            self.log.event("CHAT_RX", peer=fmt_addr(addr), chars=len(text))

    def _keepalive_loop(self) -> None:
        interval = self.cfg.keepalive
        if interval <= 0:
            return
        while not self._stop.wait(interval):
            if self.sock is None or self.peer_addr is None:
                continue
            try:
                self.sock.sendto(b"PING", self.peer_addr)
            except OSError:
                return

    def _chat_gui(self) -> None:
        if self.cfg.keepalive > 0:
            threading.Thread(target=self._keepalive_loop, daemon=True).start()
        threading.Thread(target=self._recv_loop, daemon=True).start()
        while not self._stop.is_set():
            try:
                text = self.outbox.get(timeout=0.2)
            except queue.Empty:
                continue
            if text is None:
                break
            self._send_chat(text)
        self._stop.set()

    def _chat(self) -> None:
        if self.gui:
            self._chat_gui()
            return
        if self.cfg.keepalive > 0:
            threading.Thread(target=self._keepalive_loop, daemon=True).start()

        if self.cfg.message:
            # Give both sides time to leave the punch loop before the first
            # data frame, otherwise a punch loop could consume it.
            time.sleep(self.cfg.chat_delay)
            self._send_chat(self.cfg.message)
            end = time.time() + self.cfg.chat_wait
            while time.time() < end:
                self._pump_chat(0.1)
            return

        t = threading.Thread(target=self._recv_loop, daemon=True)
        t.start()
        print("type messages; Ctrl-D to quit", flush=True)
        try:
            for line in sys.stdin:
                line = line.rstrip("\n")
                if line:
                    self._send_chat(line)
        except KeyboardInterrupt:
            pass
        self._stop.set()

    def _recv_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._pump_chat(0.2)
            except OSError:
                break

    def _close(self) -> None:
        self._stop.set()
        if self.kp is not None:
            self.kp.close()
        if self.chan is not None:
            try:
                self.chan.close()
            except Exception:  # noqa: BLE001
                pass
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.log.close()
