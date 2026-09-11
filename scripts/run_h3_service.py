import sys
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
BACKEND = os.path.join(ROOT, 'backend')
sys.path.insert(0, BACKEND)

from services.h3_service import h3_service


def main():
    samples = [
        (14.5995, 120.9842),  # Manila
        (10.3157, 123.8854),  # Cebu
    ]

    print('H3 enabled:', h3_service.enabled)
    print('Resolution:', h3_service.resolution)

    for lat, lng in samples:
        try:
            cell = h3_service.get_h3_cell(lat, lng)
            center = h3_service.get_cluster_center(cell)
            print(f'coord=({lat},{lng}) -> cell={cell} center={center}')
        except Exception as e:
            print('Error for', (lat, lng), e)


if __name__ == '__main__':
    main()
