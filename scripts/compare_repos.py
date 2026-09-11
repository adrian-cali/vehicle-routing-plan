import os
from pathlib import Path

root = Path(__file__).resolve().parent.parent
ext = root / 'external_vrp'
local = root / 'backend'

def list_files(base: Path):
    files = set()
    for p in base.rglob('*'):
        if p.is_file():
            rel = p.relative_to(base)
            files.add(str(rel).replace('\\','/'))
    return files

ext_files = list_files(ext) if ext.exists() else set()
loc_files = list_files(local) if local.exists() else set()

only_in_ext = sorted(list(ext_files - loc_files))
only_in_loc = sorted(list(loc_files - ext_files))

print('ONLY_IN_EXTERNAL_VRP:')
for f in only_in_ext:
    print(f)

print('\nONLY_IN_BACKEND:')
for f in only_in_loc:
    print(f)
