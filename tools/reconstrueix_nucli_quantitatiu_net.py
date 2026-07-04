from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.storage import get_db
from joanbot.institutional.nucli_quantitatiu_net import get_core


def main():
    db = get_db()
    core = get_core(db)
    print(core.backfill())
    print(core.report())


if __name__ == "__main__":
    main()
