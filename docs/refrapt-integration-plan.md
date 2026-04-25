# Integrationsplan: refrapt → astrapi-mirror

> **Status:** Geplant – noch nicht umgesetzt  
> **Voraussetzung:** GPL-3.0-Lizenz muss im Repo vorhanden sein (siehe PR "Add GPL-3.0 License")  
> **Quelle:** [Progeny42/refrapt](https://github.com/Progeny42/refrapt) (GPL-3.0)

---

## Ziel

Die externe Abhängigkeit `refrapt` (aktuell als CLI via `subprocess` aufgerufen) wird als
internes Python-Modul in `astrapi-mirror` integriert. Dadurch wird:

- der `subprocess`-Aufruf durch direkten Python-Code ersetzt,
- die Abhängigkeit `refrapt>=0.4.10` und der `setuptools<71`-Workaround entfernt,
- der übernommene Code schrittweise modernisiert.

---

## Neue Verzeichnisstruktur

```
astrapi_mirror/
  modules/
    debian/
      _refrapt/              ← neues internes Paket (aus Progeny42/refrapt übernommen)
        __init__.py
        classes.py           ← aus refrapt/classes.py
        settings.py          ← aus refrapt/settings.py
        helpers.py           ← aus refrapt/helpers.py
        runner.py            ← aus refrapt/refrapt.py (Hauptlogik)
      jobs.py                ← subprocess-Aufruf wird durch Python-API ersetzt
      engine.py              ← generate_refrapt_config() kann vereinfacht/entfernt werden
```

---

## Schritt-für-Schritt-Umsetzung

### Schritt 1 – Dateien kopieren

Folgende Dateien aus `Progeny42/refrapt` nach `astrapi_mirror/modules/debian/_refrapt/` kopieren:

| Quelldatei (refrapt)       | Zieldatei (_refrapt)  |
|----------------------------|-----------------------|
| `refrapt/classes.py`       | `classes.py`          |
| `refrapt/settings.py`      | `settings.py`         |
| `refrapt/helpers.py`       | `helpers.py`          |
| `refrapt/refrapt.py`       | `runner.py`           |

In jede Datei oben folgenden Herkunftshinweis einfügen:

```python
# Ursprünglich basierend auf Progeny42/refrapt (GPL-3.0)
# https://github.com/Progeny42/refrapt
```

### Schritt 2 – Imports anpassen

Alle internen Imports in den kopierten Dateien von `refrapt.*` auf `._refrapt.*` umschreiben:

```python
# Vorher (in runner.py / classes.py):
from refrapt.classes import Repository
from refrapt.settings import Settings
from refrapt.helpers import some_helper

# Nachher:
from astrapi_mirror.modules.debian._refrapt.classes import Repository
from astrapi_mirror.modules.debian._refrapt.settings import Settings
from astrapi_mirror.modules.debian._refrapt.helpers import some_helper
```

### Schritt 3 – jobs.py umschreiben

Den `subprocess`-Aufruf in `jobs.py` durch direkten Python-Aufruf ersetzen:

```python
# Vorher (jobs.py, _run_refrapt):
cmd = ["refrapt", "--conf", conf_path]
proc = subprocess.Popen(cmd, ...)

# Nachher:
from astrapi_mirror.modules.debian._refrapt.runner import run as refrapt_run
refrapt_run(conf_path=conf_path, on_line=on_line)
```

Die temporäre `.conf`-Datei wird zunächst beibehalten, da `runner.py` sie erwartet.
In einem späteren Schritt kann die Konfiguration direkt als Python-Objekt übergeben werden.

### Schritt 4 – Abhängigkeiten entfernen

In `requirements.txt` und `pyproject.toml` folgende Zeilen entfernen:

```
refrapt>=0.4.10
setuptools<71
```

### Schritt 5 – Modernisierungen (optional, schrittweise)

Nach erfolgreicher Integration können folgende Modernisierungen vorgenommen werden:

| Bereich              | Maßnahme                                                      |
|----------------------|---------------------------------------------------------------|
| Typ-Annotierungen    | `List[str]` → `list[str]`, `Optional[X]` → `X \| None`       |
| Dateipfade           | `os.path.*` → `pathlib.Path`                                  |
| Klassen              | Manuelle `__init__`-Klassen → `@dataclass`                    |
| Konfiguration        | `.conf`-Datei → direktes Python-Objekt (Settings-Dataclass)   |
| Parallelisierung     | Threading → `asyncio` (optional, aufwändig)                   |
| Tests                | Unit-Tests mit `pytest` für `_refrapt`-Modul ergänzen         |

---

## Abhängigkeiten nach der Integration

| Paket        | Vorher  | Nachher |
|--------------|---------|---------|
| `refrapt`    | ✅ extern | ❌ entfernt (intern) |
| `setuptools` | `<71` (Workaround) | keine Einschränkung mehr |
| `wget` / `subprocess` | indirekt über refrapt | direkt durch `_refrapt/runner.py` |

---

## Lizenz-Hinweis

Da refrapt unter **GPL-3.0** steht, muss `astrapi-mirror` ebenfalls unter **GPL-3.0** lizenziert sein.
Dies ist durch den PR "Add GPL-3.0 License" bereits sichergestellt.

Quelldateien im `_refrapt/`-Verzeichnis müssen den Herkunftshinweis tragen (siehe Schritt 1).