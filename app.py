"""Chateauma webapp. Run: pip install flask && python app.py  ->  http://localhost:5000"""
import json
import secrets
import threading
import time
from pathlib import Path
import random

from flask import Flask, jsonify, request, send_from_directory

BASE = Path(__file__).parent
DB_FILE = BASE / "data" / "state.json"
BOARDS = {"standard": 35, "advanced": 36}
COOKIE = "sid"
DECK = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
CASTLES = ["1", "4", "7", "8"]

app = Flask(__name__, static_folder=str(BASE / "static"), static_url_path="/static")

_lock = threading.Lock()
DB_FILE.parent.mkdir(exist_ok=True)
try:
    db: dict = json.loads(DB_FILE.read_text())
except (FileNotFoundError, json.JSONDecodeError):
    db = {}

with open(BASE / "static" / "cards" / "cards.json") as f:
    ALL_CARDS = json.load(f)

def _save() -> None:
    tmp = DB_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(db))
    tmp.replace(DB_FILE)  # atomic swap


def _new_state() -> dict:
    return {"board": None, "clicks": [], "silver": 1, "goods": [1, 1, 1, 1, 1, 1], "difficulty": None, "firstPlayer": None, "modifier": None, "points": 0, "gameStarted": False, "selectedModifiers": []}


def _sid() -> tuple[str, bool]:
    """Return (sid, is_new). Unknown/forged cookies just get a fresh state."""
    sid = request.cookies.get(COOKIE, "")
    if len(sid) == 32 and all(c in "0123456789abcdef" for c in sid):
        return sid, False
    return secrets.token_hex(16), True


def _respond(sid: str, is_new: bool, status: int = 200, payload: dict | None = None):
    resp = jsonify(payload if payload is not None else db[sid])
    resp.status_code = status
    if is_new:
        resp.set_cookie(COOKIE, sid, max_age=365 * 24 * 3600, httponly=True, samesite="Lax")
    return resp


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html") # type: ignore

@app.get("/api/state")
def get_state():
    sid, is_new = _sid()
    with _lock:
        db.setdefault(sid, _new_state())
        return _respond(sid, is_new)


@app.post("/api/setup")
def update_setup():
    sid, is_new = _sid()
    data = request.get_json(silent=True) or {}
    with _lock:
        state = db.setdefault(sid, _new_state())
        if state.get("board"):
            if set(data) != {"gameStarted"} or data["gameStarted"] is not True:
                return _respond(sid, is_new, 400, {"error": "game already configured"})
            state["gameStarted"] = True
        else:
            if "gameStarted" in data:
                return _respond(sid, is_new, 400, {"error": "no board selected"})
            if "difficulty" in data:
                difficulty = data["difficulty"]
                if difficulty not in (None, "Intermediate", "Advanced"):
                    return _respond(sid, is_new, 400, {"error": "invalid difficulty"})
                state["difficulty"] = difficulty
                state["selectedModifiers"] = []
            if "selectedModifiers" in data:
                modifiers = data["selectedModifiers"]
                if (not isinstance(modifiers, list) or
                    any(m not in ("A", "B", "C", "D") for m in modifiers) or
                    len(modifiers) != len(set(modifiers)) or
                    (modifiers and state.get("difficulty") != "Advanced")):
                    return _respond(sid, is_new, 400, {"error": "invalid modifiers"})
                state["selectedModifiers"] = modifiers
        _save()
        return _respond(sid, is_new)


@app.post("/api/back-to-setup")
def back_to_setup():
    """Leave Ready to Begin without discarding the chosen difficulty/modifiers."""
    sid, is_new = _sid()
    with _lock:
        state = db.setdefault(sid, _new_state())
        if not state.get("board") or state.get("gameStarted"):
            return _respond(sid, is_new, 400, {"error": "not on Ready to Begin"})
        difficulty = state.get("difficulty")
        chosen = state.get("modifier") or ""
        modifiers = [m for m in chosen.split(",") if m in ("A", "B", "C", "D")]
        db[sid] = _new_state()
        if difficulty in ("Intermediate", "Advanced"):
            db[sid]["difficulty"] = difficulty
            if difficulty == "Advanced":
                db[sid]["selectedModifiers"] = modifiers
        _save()
        return _respond(sid, is_new)


@app.post("/api/board")
def set_board():
    sid, is_new = _sid()
    response = request.get_json(silent=True) or {}
    board = response.get("board")
    starting_player = response.get("firstPlayer")
    modifier = response.get("modifier")
    difficulty = response.get("difficulty")
    print(f"Received board: {board}, starting_player: {starting_player}, modifier: {modifier}, difficulty: {difficulty}")
    if board not in BOARDS:
        return _respond(sid, is_new, 400, {"error": "unknown board"})
    with _lock:
        state = db.setdefault(sid, _new_state())
        state["board"], state["clicks"] = board, []
        state["firstPlayer"] = starting_player
        state["modifier"] = modifier
        state["difficulty"] = difficulty
        state["gameStarted"] = False
        state["selectedModifiers"] = []
        state["cards"] = get_cards(difficulty)
        _save()
        return _respond(sid, is_new)


@app.post("/api/click")
def click():
    sid, is_new = _sid()
    data = request.get_json(silent=True) or {}
    x, y = data.get("x"), data.get("y")
    with _lock:
        state = db.setdefault(sid, _new_state())
        valid = (
            state["board"]
            and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in (x, y))
        )
        if not valid:
            return _respond(sid, is_new, 400, {"error": "bad click"})
        state["clicks"].append({"x": x, "y": y, "t": int(time.time() * 1000)})
        _save()
        return _respond(sid, is_new)

def get_cards(difficulty, replace=None):
    if len(DECK) == 9:
        card1 = random.choice(CASTLES)
        DECK.remove(card1)
        card2 = random.choice(DECK)
        DECK.remove(card2)
    else:
        if replace == "card1":
            card1 = random.choice(DECK)
            card2 = None
            DECK.remove(card1)
        else:
            card1 = None
            card2 = random.choice(DECK)
            DECK.remove(card2)
    print(f"Difficulty: {difficulty}")
    difficulty_index = 0
    if difficulty == "Intermediate":
        difficulty_index = 1
    elif difficulty == "Advanced":
        difficulty_index = 2

    points1 = ALL_CARDS[card1][difficulty_index] if card1 is not None else None
    points2 = ALL_CARDS[card2][difficulty_index] if card2 is not None else None

    return [(card1, points1), (card2, points2)]

@app.post("/api/reset")
def reset():
    sid, is_new = _sid()
    global DECK
    DECK = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
    with _lock:
        db[sid] = _new_state()
        _save()
        return _respond(sid, is_new)

@app.post("/api/add-point")
def add_point():
    sid, is_new = _sid()
    with _lock:
        state = db.setdefault(sid, _new_state())
        state["points"] += 1
        _save()
        return _respond(sid, is_new)

@app.post("/api/remove-point")
def remove_point():
    sid, is_new = _sid()
    with _lock:
        state = db.setdefault(sid, _new_state())
        if state["points"] > 0:
            state["points"] -= 1
        _save()
        return _respond(sid, is_new)

@app.post("/api/change-card")
def change_card():
    sid, is_new = _sid()
    data = request.get_json(silent=True) or {}
    index = data.get("index", -1)
    with _lock:
        state = db.setdefault(sid, _new_state())
        if 0 <= index < len(state["cards"]):
            cards = get_cards(state["difficulty"], replace="card1" if index == 0 else "card2")
            state["cards"][index] = cards[0] if index == 0 else cards[1]
        _save()
        return _respond(sid, is_new)


if __name__ == "__main__":
    app.run(debug=True)