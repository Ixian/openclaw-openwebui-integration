# Shared, bounded presentation state. This module performs no file or network I/O.
MARVIN_RELEASE = "marvin-v1.0.0"
ACTIVITY_STREAM = "bridge_activity"
ACTIVITY_STREAMS = (ACTIVITY_STREAM, "command_output", "run_status", "plan", "lifecycle")
ACTIVITY_PREVIEW_CHARS = 8000
ACTIVITY_MAX_CALLS = 50


def _activity_plain(value, limit=160):
    text = " ".join(str(value or "").split())
    return text[:limit]


def _activity_escape(value):
    # Gateway text must never create links, HTML, headings or code fences.
    return re.sub(r"([\\`*_{}\[\]()#!|~])", r"\\\1", html.escape(str(value or "")))


def _activity_output(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_activity_output(v) for v in value)
    if not isinstance(value, dict):
        return ""
    content = value.get("content")
    if isinstance(content, list):
        return "\n".join(b["text"] for b in content
                         if isinstance(b, dict) and b.get("type") in ("text", "output_text")
                         and isinstance(b.get("text"), str))
    for key in ("output", "stdout", "text"):
        if isinstance(value.get(key), str):
            return value[key]
    return ""


def _activity_title(name, args):
    args = args if isinstance(args, dict) else {}
    for key in ("title", "label", "description"):
        if isinstance(args.get(key), str) and args[key].strip():
            return _activity_plain(args[key])
    if name in ("read", "write", "edit", "apply_patch"):
        return _activity_plain(f"{name} {args.get('path') or args.get('file_path') or ''}")
    if name in ("web_search", "memory_search"):
        return _activity_plain(f"{name}: {args.get('query') or ''}")
    if name in ("exec", "bash", "shell"):
        # Do not expose an inline script, a token, or a whole shell pipeline in a title.
        return "Run command"
    return _activity_plain(name or "Tool")


def _activity_card(value, session):
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("sessionKey") != session:
        raise ValueError("Progress card belongs to another session")
    rev = value.get("revision")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 1:
        raise ValueError("Invalid progress revision")
    steps = value.get("steps", [])
    if not isinstance(steps, list) or len(steps) > 50:
        raise ValueError("Invalid progress steps")
    clean = []
    for step in steps:
        if (not isinstance(step, dict) or not isinstance(step.get("step"), str)
                or step.get("status") not in ("pending", "in_progress", "completed")):
            raise ValueError("Invalid progress step")
        clean.append({"step": step["step"][:512], "status": step["status"]})
    markdown = value.get("markdown", "")
    if not isinstance(markdown, str):
        raise ValueError("Invalid progress note")
    return {"revision": rev, "updatedAt": value.get("updatedAt"),
            "markdown": markdown[:8192], "steps": clean}


class _ActivityState:
    def __init__(self):
        self.session = ""
        self.run = ""
        self.calls = {}
        self.sequences = {}
        self.card = None
        self.card_error = None
        self.card_epoch = 0
        self.plan = None
        self.status = "running"
        self.summary = ""
        self._shown = ""
        self._last_emit = 0.0

    def feed(self, payload, now=None):
        session, run = payload.get("sessionKey"), payload.get("runId")
        if not session or not run or (self.session and self.session != session):
            return None
        self.session = session
        if self.run != run:
            # A continuation has its own tool IDs and sequence namespace.
            self.run = run
            self.calls = {}
            self.sequences = {}
            self.plan = None
            self.status = "running"
            self.summary = ""
            self._shown = ""
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            return None
        stream, phase = payload.get("stream"), data.get("phase")
        tcid = data.get("toolCallId") or data.get("itemId")
        seq = payload.get("seq")
        identity = (stream, tcid or "")
        if isinstance(seq, int):
            if seq <= self.sequences.get(identity, -1):
                return None
            self.sequences[identity] = seq
        force = False
        if stream == ACTIVITY_STREAM:
            if "card" in data:
                card = _activity_card(data["card"], session)
                if card and self.card and card["revision"] < self.card["revision"]:
                    return None
                self.card = card
                self.card_epoch += 1
            self.card_error = data.get("error")
            force = True
        elif stream == "plan":
            self.plan = _activity_card({"sessionKey": session, "revision": 1,
                                      "steps": data.get("steps", []),
                                      "markdown": data.get("explanation", "")}, session)
            force = True
        elif stream == "run_status":
            self.summary = _activity_plain(data.get("message") or phase or "Working")
        elif stream == "item" and isinstance(data.get("progressText"), str):
            self.summary = _activity_plain(data["progressText"])
        elif stream == "lifecycle" and phase in ("end", "error"):
            self.finish("failed" if phase == "error" else "ended")
            force = True
        elif stream in ("tool", "command_output") and tcid:
            if _is_nested_tool_call(data, self.calls):
                return None
            call = self.calls.get(tcid)
            if call and call["status"] != "running" and phase not in ("result", "completed"):
                return None
            if call is None:
                if len(self.calls) >= ACTIVITY_MAX_CALLS:
                    return None
                name, args = data.get("name", "tool"), data.get("args") or {}
                deferred = _deferred_call_view(name, json.dumps(args))
                if deferred:
                    name, args = deferred[0], _json_object(deferred[1])
                call = {"id": tcid, "name": name, "title": _activity_title(name, args),
                        "status": "running", "output": "", "clipped": False,
                        "startedAt": payload.get("ts"), "durationMs": None}
                self.calls[tcid] = call
            if isinstance(data.get("title"), str) and data["title"].strip():
                call["title"] = _activity_plain(data["title"])
            if isinstance(data.get("durationMs"), (int, float)):
                call["durationMs"] = max(0, data["durationMs"])
            raw = (data.get("partialResult") if phase == "update" else
                   data.get("result") if phase == "result" else data.get("output"))
            text = _activity_output(raw)
            if text:
                force = force or not call["output"]
                call["output"] = text[-ACTIVITY_PREVIEW_CHARS:]
                call["clipped"] = len(text) > ACTIVITY_PREVIEW_CHARS
            if phase == "result" or data.get("status") in ("completed", "failed", "cancelled"):
                result = raw if isinstance(raw, dict) else {}
                details = result.get("details") or {}
                exit_code = data.get("exitCode", result.get("exitCode", details.get("exitCode")))
                call["status"] = ("failed" if data.get("isError") or exit_code not in (None, 0)
                                  or data.get("status") == "failed" else
                                  "cancelled" if data.get("status") == "cancelled" else "completed")
                force = True
            elif phase == "start":
                force = True
        else:
            return None
        if not (self.card or self.plan or self.calls or self.summary or self.card_error):
            return None
        text = self.markdown()
        stamp = time.monotonic() if now is None else now
        if text == self._shown or (not force and stamp - self._last_emit < 0.5):
            return None
        self._shown, self._last_emit = text, stamp
        tag = hashlib.sha256(f"{session}:{run}".encode()).hexdigest()[:24]
        return {"id": f"msg_note_activity_{tag}", "text": text}

    def finish(self, status):
        self.status = status
        for call in self.calls.values():
            if call["status"] == "running":
                call["status"] = "interrupted" if status != "running" else "running"

    def markdown(self):
        lines = ["### Activity"]
        for label, card in (("Run plan", self.plan), ("Task progress", self.card)):
            if not card:
                continue
            steps = card.get("steps") or []
            done = sum(s["status"] == "completed" for s in steps)
            lines.append(f"**{label}" + (f" · {done}/{len(steps)} steps complete**" if steps else "**"))
            for step in steps:
                marker = "x" if step["status"] == "completed" else " "
                suffix = " (current)" if step["status"] == "in_progress" else ""
                lines.append(f"- [{marker}] {_activity_escape(step['step'])}{suffix}")
            if card.get("markdown"):
                lines.extend(["", _activity_escape(card["markdown"])])
            if isinstance(card.get("updatedAt"), (int, float)):
                updated = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(card["updatedAt"] / 1000))
                lines.append(f"\nLast progress update: {updated}")
            if self.status != "running" and done < len(steps) and (label == "Run plan" or not self.plan):
                lines.append("\nWork remains unfinished. The response ending does not complete the plan.")
        if self.card_error:
            lines.append("\nProgress could not be refreshed; showing the last confirmed snapshot.")
        if self.summary:
            lines.append("\n" + _activity_escape(self.summary))
        for call in list(self.calls.values())[-8:]:
            lines.append(f"\n**{call['status'].capitalize()} · {_activity_escape(call['title'])}**")
            if call["output"] and call["status"] in ("running", "interrupted", "failed"):
                # A longer fence keeps arbitrary command output inside the code block.
                fence = "`" * max(3, 1 + max((len(m.group()) for m in re.finditer(r"`+", call["output"])), default=0))
                preview = re.sub(r"</details", "&lt;/details", call["output"], flags=re.I)
                lines.append(fence + "text\n" + preview + "\n" + fence)
                if call["clipped"]:
                    lines.append("\nPreview limited to the latest 8,000 characters. Open Activity for retained output.")
        current = self.plan or self.card
        steps = (current or {}).get("steps", [])
        done = sum(s["status"] == "completed" for s in steps)
        summary = "Task activity" + (f" · {done}/{len(steps)}" if steps else "")
        if self.calls:
            last = list(self.calls.values())[-1]
            summary += f" · {last['status'].capitalize()}: {last['title']}"
        return '<details>\n<summary>' + html.escape(summary) + '</summary>\n\n' + "\n\n".join(lines) + "\n\n</details>"


async def _activity_read_card(conn, session):
    response = await conn.send_request("progressCard.get", {"sessionKey": session}, timeout=5)
    value = response.get("card")
    _activity_card(value, session)
    return value


async def _activity_load_output(conn, session, message_id, call_id):
    response = await conn.send_request("chat.message.get", {
        "sessionKey": session, "messageId": message_id, "maxChars": 1_000_000}, timeout=10)
    if not response.get("ok"):
        return {"available": False, "reason": response.get("unavailableReason", "unavailable")}
    message = response.get("message") or {}
    if message.get("toolCallId") == call_id:
        return {"available": True, "text": _activity_output(message),
                "truncated": bool((message.get("__openclaw") or {}).get("truncated"))}
    matches = [b for b in message.get("content", []) if isinstance(b, dict)
               and b.get("type") in ("toolResult", "tool_result")
               and (b.get("toolCallId") or b.get("tool_use_id")) == call_id]
    if len(matches) == 1:
        metadata = matches[0].get("__openclaw") or message.get("__openclaw") or {}
        return {"available": True, "text": _activity_output(matches[0]),
                "truncated": bool(metadata.get("truncated"))}
    return {"available": False, "reason": "Tool result does not match this call"}


async def _activity_snapshot(conn, session):
    """Read current state, then merge bounded history without regressing live calls."""
    before = getattr(conn, "_activity_states", {}).get(session)
    before_epoch = before.card_epoch if before else 0
    card_result, history_result = await asyncio.gather(
        _activity_read_card(conn, session),
        conn.send_request("chat.history", {"sessionKey": session, "limit": 50,
                                          "maxChars": 32000}, timeout=5),
        return_exceptions=True)
    live = getattr(conn, "_activity_states", {}).get(session)
    state = copy.deepcopy(live) if live else _ActivityState()
    history = history_result if isinstance(history_result, dict) else {}
    refs = {}
    historical = _ActivityState()
    recent_calls = {}
    for message in history.get("messages", []):
        if not isinstance(message, dict):
            continue
        meta = message.get("__openclaw") or {}
        run = meta.get("runId") or "history"
        for event in _history_events([message]):
            if event.get("stream") != "tool":
                continue
            historical.feed({**event, "sessionKey": session, "runId": run})
            recent_calls.update(copy.deepcopy(historical.calls))
        if message.get("role") == "toolResult" and message.get("toolCallId") and meta.get("id"):
            refs[message["toolCallId"]] = meta["id"]
        for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") in ("toolResult", "tool_result"):
                tid = block.get("toolCallId") or block.get("tool_use_id")
                mid = (block.get("__openclaw") or {}).get("id") or meta.get("id")
                if tid and mid:
                    refs[tid] = mid
    if not live:
        state = historical
    in_flight = history.get("inFlightRun") or {}
    active_run = in_flight.get("runId")
    if active_run and (not live or live.run == active_run or live.status != "running"):
        for event in in_flight.get("events", []):
            try:
                state.feed({**event, "runId": active_run, "sessionKey": session})
            except (TypeError, ValueError):
                continue
        if isinstance(in_flight.get("plan"), dict):
            state.feed({"runId": active_run, "sessionKey": session, "stream": "plan",
                        "data": in_flight["plan"]})
    elif not live and not active_run:
        state.finish("ended")
    if isinstance(card_result, BaseException):
        state.card_error = "Progress read failed"
    elif not live or state.card_epoch == before_epoch:
        state.card = _activity_card(card_result, session)
    calls = []
    for old in recent_calls.values():
        if old["status"] == "running":
            old["status"] = "no retained result"
    recent_calls.update(state.calls)
    for call in list(recent_calls.values())[-20:]:
        calls.append({**call, "messageId": refs.get(call["id"])})
    return {"release": MARVIN_RELEASE, "card": state.card, "plan": state.plan,
            "cardError": bool(state.card_error), "calls": calls,
            "historyError": isinstance(history_result, BaseException),
            "historyLimited": bool(history.get("hasMore")), "status": state.status}


def _activity_result_preview(text):
    if len(text) <= 8000:
        return text
    note = "\n\n[Bridge preview clipped. Open Activity for retained output.]"
    return text[:8000 - len(note)] + note
