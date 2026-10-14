"""Map Indian composition strings (e.g. "Amoxycillin  (500mg)") to DDInter drug names.

Indian labels use INN/BAN spellings and salt forms; DDInter uses US names. The
mapping is deterministic: exact match, then a synonym table, then spelling and
salt-suffix rules. Anything left over is reported as unmatched, never guessed.
"""
import re

# Indian / British name -> DDInter (US) name. Only well-known equivalents.
SYNONYMS = {
    "paracetamol": "acetaminophen",
    "salbutamol": "albuterol",
    "levosalbutamol": "levalbuterol",
    "glibenclamide": "glyburide",
    "frusemide": "furosemide",
    "adrenaline": "epinephrine",
    "noradrenaline": "norepinephrine",
    "clavulanic acid": "clavulanate",
    "amoxycillin": "amoxicillin",
    "cefalexin": "cephalexin",
    "beclometasone": "beclomethasone dipropionate",
    "vitamin b6": "pyridoxine",
    "paracetamol/acetaminophen": "acetaminophen",
    "niacinamide": "nicotinamide",
    "lactobacillus": "lactobacillus acidophilus",
    "tazobactum": "tazobactam",
    "lignocaine": "lidocaine",
    "thyroxine": "levothyroxine",
    "levo-thyroxine": "levothyroxine",
    "rifampicin": "rifampin",
    "cyclosporine": "ciclosporin",
    "beclomethasone": "beclomethasone dipropionate",
    "dicyclomine": "dicyclomine",
    "chlorpheniramine maleate": "chlorpheniramine",
    "chlorphenamine": "chlorpheniramine",
    "pheniramine": "pheniramine",
    "acetylsalicylic acid": "acetylsalicylic acid",
    "aspirin": "acetylsalicylic acid",
    "vitamin d3": "cholecalciferol",
    "vitamin b12": "cyanocobalamin",
    "methylcobalamin": "mecobalamin",
    "vitamin c": "ascorbic acid",
    "folic acid": "folic acid",
    "ofloxacin": "ofloxacin",
    "esomeprazole": "esomeprazole",
    "domperidone": "domperidone",
    "metformin": "metformin",
    "glimepiride": "glimepiride",
    "sodium valproate": "valproic acid",
    "divalproex": "valproic acid",
    "valproate": "valproic acid",
    "isosorbide mononitrate": "isosorbide mononitrate",
    "torsemide": "torasemide",
    "dothiepin": "dosulepin",
    "trihexyphenidyl": "trihexyphenidyl",
    "oestradiol": "estradiol",
    "ethinyloestradiol": "ethinylestradiol",
    "ethinyl estradiol": "ethinylestradiol",
    "phenobarbitone": "phenobarbital",
    "cholecalciferol": "cholecalciferol",
}

# Salt and form words that Indian labels add but DDInter omits.
SUFFIXES = [
    "hydrochloride", "hcl", "sodium", "potassium", "calcium", "magnesium",
    "maleate", "besylate", "besilate", "mesylate", "succinate", "tartrate",
    "fumarate", "citrate", "sulphate", "sulfate", "phosphate", "acetate",
    "bromide", "dihydrate", "trihydrate", "monohydrate", "hyclate",
    "dipropionate", "propionate", "furoate", "valerate", "palmitate",
    "disodium", "trometamol", "proxetil", "oxalate", "medoxomil", "decanoate",
    "gluconate", "axetil", "pivoxil", "cilexetil", "dihydrochloride", "bitartrate", "hydrobromide", "nitrate",
    "extended release", "sustained release", "controlled release", "ip", "bp", "usp",
]

STRENGTH = re.compile(r"\(([^)]*)\)")


def parse(component):
    """'Amoxycillin  (500mg)' -> ('amoxycillin', '500mg'); None for blanks."""
    if not isinstance(component, str) or not component.strip():
        return None
    m = STRENGTH.search(component)
    strength = m.group(1).strip() if m else None
    name = STRENGTH.sub("", component)
    name = re.sub(r"\s+", " ", name).strip().lower()
    return name, strength


def candidates(name):
    """Spellings to try, most specific first."""
    yield name
    if name in SYNONYMS:
        yield SYNONYMS[name]
    words = name.split()
    stripped = [w for w in words if w not in SUFFIXES]
    if stripped and stripped != words:
        base = " ".join(stripped)
        yield base
        if base in SYNONYMS:
            yield SYNONYMS[base]
    # British -> US spellings
    us = name.replace("ph", "f") if name.startswith("sulpha") else name
    us = us.replace("aluminium", "aluminum").replace("sulph", "sulf").replace("oe", "e").replace("ae", "e")
    if us != name:
        yield us
        if us in SYNONYMS:
            yield SYNONYMS[us]


class Resolver:
    def __init__(self, ddinter_names):
        self.by_lower = {n.lower(): n for n in ddinter_names}

    def resolve(self, name):
        for c in candidates(name):
            hit = self.by_lower.get(c)
            if hit:
                return hit
        return None
