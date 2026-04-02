#!/bin/bash

# Local release creation script
# This builds the app and creates a distributable ZIP for manual uploads

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION=${1:-"dev"}
APP_NAME="DroidProxy"

echo -e "${BLUE}📦 Creating ${APP_NAME} Release ${VERSION}${NC}"
echo ""

# Clean previous builds
echo -e "${BLUE}🧹 Cleaning previous builds...${NC}"
cd "$PROJECT_DIR"
rm -rf "${APP_NAME}.app"
rm -f "${APP_NAME}.zip"
rm -f "${APP_NAME}.dmg"

# Build the app
echo -e "${BLUE}🔨 Building ${APP_NAME}...${NC}"
./create-app-bundle.sh

if [ ! -d "${APP_NAME}.app" ]; then
    echo -e "${RED}❌ Build failed - ${APP_NAME}.app not found${NC}"
    exit 1
fi

# Create ZIP
echo -e "${BLUE}📦 Creating ZIP archive...${NC}"
ditto -c -k --sequesterRsrc --keepParent "${APP_NAME}.app" "${APP_NAME}-${VERSION}.zip"

# Calculate checksum
echo -e "${BLUE}🔐 Calculating checksum...${NC}"
CHECKSUM=$(shasum -a 256 "${APP_NAME}-${VERSION}.zip" | awk '{print $1}')

# Summary
echo ""
echo -e "${GREEN}✅ Release created successfully!${NC}"
echo ""
echo -e "${BLUE}Files created:${NC}"
echo "  - ${APP_NAME}.app (local testing)"
echo "  - ${APP_NAME}-${VERSION}.zip (for distribution)"
echo ""
echo -e "${BLUE}SHA-256 Checksum:${NC}"
echo "  ${CHECKSUM}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Test the .app locally"
echo "  2. Create a new release on GitHub"
echo "  3. Upload ${APP_NAME}-${VERSION}.zip"
echo "  4. Add the checksum to release notes"
echo ""
echo -e "${BLUE}GitHub Release Command:${NC}"
echo "  gh release create v${VERSION} ${APP_NAME}-${VERSION}.zip --generate-notes"
echo ""
