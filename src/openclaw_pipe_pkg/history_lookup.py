# Session-column lookup shared by the Pipe and its read-only Action.
HISTORY_LINE_DIR = "history-line"
MULTI_MODEL_COLUMNS_MAX = 16


def _history_line_id(chat_id: str, column: int = 0) -> str:
    """The name of a session's line file: the chat's id, then `-m<column>`."""
    return f"{chat_id}-m{column}" if column else chat_id


def _history_column_of(state_dir: str, chat_id: str, ids) -> int | None:
    """The column whose line holds one of the message `ids`, if any: where a
    reply the user goes on from was answered. Columns are made in order, so
    the first one without a line is the last."""
    ids = [i for i in ids if i]
    for column in range(MULTI_MODEL_COLUMNS_MAX):
        path = _history_line_path(state_dir, _history_line_id(chat_id, column))
        if column and not os.path.exists(path):
            break
        tail = _history_line_read(state_dir, _history_line_id(chat_id, column))["tail"]
        if any(i in tail for i in ids):
            return column
    return None


def _history_line_path(state_dir: str, chat_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", chat_id)[:120]
    return os.path.join(state_dir, HISTORY_LINE_DIR, f"{safe}.json")


def _history_line_read(state_dir: str, chat_id: str) -> dict:
    data = _read_json_file(_history_line_path(state_dir, chat_id))
    if not isinstance(data, dict) or not isinstance(data.get("tail"), list):
        return {"tail": []}
    return data

