#!/bin/bash
set -e

echo "========================================="
echo "OSRM Map Update Script"
echo "========================================="
echo ""

# Configuration
DOWNLOAD_URL="https://download.geofabrik.de/asia/philippines-latest.osm.pbf"
DATA_DIR="./vroom_ors/osrm-philippines"
PBF_FILE="$DATA_DIR/philippines-latest.osm.pbf"
OSRM_BASE="$DATA_DIR/philippines-latest.osrm"

# Create data directory if it doesn't exist
mkdir -p "$DATA_DIR"

echo "Step 1: Downloading latest Philippines map from Geofabrik..."
echo "URL: $DOWNLOAD_URL"
echo ""

# Download the PBF file
wget -O "$PBF_FILE" "$DOWNLOAD_URL"

if [ ! -f "$PBF_FILE" ]; then
    echo "ERROR: Failed to download PBF file"
    exit 1
fi

echo "✓ Download complete"
echo ""

echo "Step 2: Building OSRM data files..."
echo "This may take several minutes depending on your system..."
echo ""

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed or not in PATH"
    exit 1
fi

# Extract
echo "2a. Extracting..."
docker run --rm -v "$PWD/$DATA_DIR:/data" osrm/osrm-backend:latest \
    osrm-extract -p /opt/car.lua /data/philippines-latest.osm.pbf

if [ $? -ne 0 ]; then
    echo "ERROR: osrm-extract failed"
    exit 1
fi
echo "✓ Extract complete"

# Partition
echo "2b. Partitioning..."
docker run --rm -v "$PWD/$DATA_DIR:/data" osrm/osrm-backend:latest \
    osrm-partition /data/philippines-latest.osrm

if [ $? -ne 0 ]; then
    echo "ERROR: osrm-partition failed"
    exit 1
fi
echo "✓ Partition complete"

# Customize
echo "2c. Customizing..."
docker run --rm -v "$PWD/$DATA_DIR:/data" osrm/osrm-backend:latest \
    osrm-customize /data/philippines-latest.osrm

if [ $? -ne 0 ]; then
    echo "ERROR: osrm-customize failed"
    exit 1
fi
echo "✓ Customize complete"
echo ""

# Cleanup PBF file (optional)
echo "Step 3: Cleanup..."
read -p "Remove downloaded PBF file to save space? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    rm "$PBF_FILE"
    echo "✓ PBF file removed"
fi

echo ""
echo "========================================="
echo "OSRM MAP UPDATE COMPLETED!"
echo "========================================="
echo ""
echo "The OSRM data files are ready in: $DATA_DIR"
echo ""
echo "Next steps:"
echo "1. Start the services: docker-compose up -d"
echo "2. The OSRM service will use the updated map data"
echo ""
echo "Note: If containers are already running, restart them:"
echo "  docker-compose restart osrm vroom"
echo ""
