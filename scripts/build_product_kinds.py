"""Build aggregate creation presets from a catalog export; no PN/GTIN is bundled."""
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nk_manager.creation import build_catalog




if __name__=='__main__':
    source=Path(sys.argv[1])
    with ZipFile(source) as z:
        manifest=json.loads(z.read('manifest.json'))
        for name,sha in manifest['files'].items():
            assert hashlib.sha256(z.read(name)).hexdigest()==sha
        cards=json.loads(z.read('catalog.json'))['cards']
    output=Path(sys.argv[2]) if len(sys.argv)>2 else ROOT/'presets/product-kinds.json'
    body={'schema_version':1,'source_archive_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'kinds':build_catalog(cards)}
    output.write_text(json.dumps(body,ensure_ascii=False,indent=2)+'\n')
    print(len(body['kinds']),'product kinds')
