"""Tokenising, stemming and entity extraction used for grouping and background lookups."""
import re
from collections import Counter

STOPWORDS = set(
    """
a about above after again against ago all almost also although am among an and another any are around as at
back be became because become been before being below between both but by can cannot could did do does doing
done down during each either else even ever every few for from further get gets getting got had has have
having he her here hers herself him himself his how however i if in into is it its itself just last later
least less let like made make makes many may me might more most much must my myself near need new news next
no nor not now of off often on once one only or other others our ours ourselves out over own per put rather
said same say says see seen she should since so some still such take than that the their theirs them
themselves then there these they this those though three through thus to today told too two under until
up upon us use used very via was way we week weeks well were what when where whether which while who whom
whose why will with within without would year years yesterday yet you your yours yourself first second
latest live update updates breaking watch video videos photos report reports reported reporting according
people time times day days amid after over says claims calls call called could new news more
""".split()
)

# Words that are capitalised in headlines but carry no topical meaning.
ENTITY_STOP = set(
    """
The A An And Or But If In On At To For Of By With From As Is Are Was Were Be After Before Over Under
Why How What When Where Who Which This That These Those It Its He She They We You I Our Your His Her Their
Mr Mrs Ms Dr Sir Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May
June July August September October November December Live Watch Video Opinion Analysis Breaking Update Exclusive
New News Report Says Said BBC NPR Fox CBS ABC NYT Guardian Times Al Jazeera Reuters AP Associated Press
Here Today Tonight Week Year One Two Three First Last Top
""".split()
)

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_ENTITY_RE = re.compile(r"\b[A-Z][\w'’\-\.]*[A-Za-z0-9](?:\s+(?:of|de|del|al|the|and)?\s*[A-Z][\w'’\-\.]*[A-Za-z0-9])*")


def stem(word: str) -> str:
    if len(word) <= 4 or word.isdigit():
        return word
    if word.endswith("ies") and len(word) >= 6:
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if word.endswith("ing") and len(word) >= 7:
        return word[:-3]
    if word.endswith("ed") and len(word) >= 6:
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    out = []
    for w in _WORD_RE.findall(text.lower().replace("’", "'")):
        if w.endswith("'s"):
            w = w[:-2]
        w = w.replace("'", "")
        if len(w) < 3 or w in STOPWORDS:
            continue
        if w.isdigit() and len(w) != 4:  # keep years, drop other bare numbers
            continue
        out.append(stem(w))
    return out


def entities(text: str) -> list[str]:
    """Capitalised phrases, e.g. 'Supreme Court', 'Gaza', 'Elon Musk'. Returned in original casing."""
    found = []
    for m in _ENTITY_RE.finditer(text):
        phrase = m.group(0).strip(" .-'’")
        words = [w for w in phrase.split() if w not in ENTITY_STOP]
        if not words:
            continue
        phrase = " ".join(words)
        if len(phrase) < 3 or phrase.isupper() and len(phrase) <= 2:
            continue
        found.append(phrase)
    return found


def entity_key(phrase: str) -> str:
    return " ".join(stem(w) for w in tokens(phrase))


def term_counts(title: str, excerpt: str, title_weight: int = 2) -> Counter:
    c = Counter()
    for t in tokens(title):
        c[t] += title_weight
    for t in tokens(excerpt):
        c[t] += 1
    return c
