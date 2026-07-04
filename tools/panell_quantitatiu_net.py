from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.storage import get_db
from joanbot.institutional.nucli_quantitatiu_net import get_core


def main():
    db = get_db()
    core = get_core(db)
    text = core.report()
    out = ROOT / "live_export" / "informe_quantitatiu_net.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
