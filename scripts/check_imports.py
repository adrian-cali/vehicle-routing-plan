import importlib, traceback

mods = [
    'backend.app',
    'backend.services.vroom_service',
    'backend.services.h3_service',
    'backend.services.redis_service',
]

for m in mods:
    try:
        importlib.import_module(m)
        print(f'OK: {m}')
    except Exception:
        print(f'FAIL: {m}')
        traceback.print_exc()
