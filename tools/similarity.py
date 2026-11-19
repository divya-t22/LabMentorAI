from difflib import SequenceMatcher

def compare(left: list[str], right: list[str]) -> float:
    if not left or not right: return 0.0
    return round(SequenceMatcher(None, left, right, autojunk=False).ratio() * 100, 1)
