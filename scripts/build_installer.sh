#!/usr/bin/env bash
# Cross-platform installer builder for JARVIS
# Usage: ./scripts/build_installer.sh [version] [channel]

set -euo pipefail

VERSION="${1:-2.0.0-dev}"
CHANNEL="${2:-stable}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/build"
DIST_DIR="${ROOT_DIR}/dist"

echo "🔨 Building JARVIS v${VERSION} (${CHANNEL})"

# Clean previous builds
rm -rf "${BUILD_DIR}" "${DIST_DIR}"
mkdir -p "${BUILD_DIR}" "${DIST_DIR}"

# Detect OS
OS="$(uname -s)"
case "${OS}" in
    Linux*)     PLATFORM="linux"; INSTALLER_TYPE="appimage" ;;
    Darwin*)    PLATFORM="macos"; INSTALLER_TYPE="dmg" ;;
    CYGWIN*|MINGW*|MSYS*) PLATFORM="windows"; INSTALLER_TYPE="exe" ;;
    *) echo "Unsupported OS: ${OS}"; exit 1 ;;
esac

echo "📦 Building for ${PLATFORM} (${INSTALLER_TYPE})"

# Install dependencies
echo "📦 Installing dependencies..."
pip install -e ".[build]" --quiet

# Build with PyInstaller
echo "🔨 Building with PyInstaller..."
cd "${ROOT_DIR}"
python -m PyInstaller \
    --noconfirm \
    --onefile \
    --windowed \
    --name "jarvis" \
    --add-data "src/jarvis/assets:jarvis/assets" \
    --add-data "src/jarvis/integrations:jarvis/integrations" \
    --hidden-import=jarvis.managers \
    --hidden-import=jarvis.transports \
    --hidden-import=jarvis.integrations \
    --hidden-import=jarvis.core \
    --hidden-import=google.genai \
    --hidden-import=faster_whisper \
    --hidden-import=mediapipe \
    --hidden-import=psutil \
    --hidden-import=pyautogui \
    --hidden-import=cv2 \
    src/jarvis/__main__.py

# Create platform-specific installer
case "${PLATFORM}" in
    linux)
        echo "📦 Creating AppImage..."
        # Create AppDir structure
        APPDIR="${BUILD_DIR}/jarvis.AppDir"
        mkdir -p "${APPDIR}/usr/bin"
        mkdir -p "${APPDIR}/usr/share/applications"
        mkdir -p "${APPDIR}/usr/share/icons/hicolor/256x256/apps"
        
        cp "dist/jarvis" "${APPDIR}/usr/bin/jarvis"
        chmod +x "${APPDIR}/usr/bin/jarvis"
        
        # Desktop entry
        cat > "${APPDIR}/usr/share/applications/jarvis.desktop" <<EOF
[Desktop Entry]
Name=JARVIS
Comment=Personal AI Assistant
Exec=jarvis
Icon=jarvis
Terminal=false
Type=Application
Categories=Utility;
EOF
        
        # Icon (placeholder)
        convert -size 256x256 xc:transparent "${APPDIR}/usr/share/icons/hicolor/256x256/apps/jarvis.png" 2>/dev/null || true
        
        # Download appimagetool if needed
        if ! command -v appimagetool &> /dev/null; then
            wget -q "https://github.com/AppImage/AppImageTool/releases/download/continuous/appimagetool-x86_64.AppImage" -O /tmp/appimagetool
            chmod +x /tmp/appimagetool
            APPIMAGETOOL="/tmp/appimagetool"
        else
            APPIMAGETOOL="appimagetool"
        fi
        
        ARCH=x86_64 ${APPIMAGETOOL} "${APPDIR}" "${DIST_DIR}/jarvis-${VERSION}-${PLATFORM}.AppImage"
        ;;
        
    darwin)
        echo "📦 Creating DMG..."
        # Create .app bundle
        APP_DIR="${BUILD_DIR}/JARVIS.app"
        mkdir -p "${APP_DIR}/Contents/MacOS"
        mkdir -p "${APP_DIR}/Contents/Resources"
        
        cp "dist/jarvis" "${APP_DIR}/Contents/MacOS/jarvis"
        chmod +x "${APP_DIR}/Contents/MacOS/jarvis"
        
        # Info.plist
        cat > "${APP_DIR}/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>jarvis</string>
    <key>CFBundleIdentifier</key>
    <string>com.jarvis.ai</string>
    <key>CFBundleName</key>
    <string>JARVIS</string>
    <key>CFBundleVersion</key>
    <string>${VERSION}</string>
    <key>CFBundleShortVersionString</key>
    <string>${VERSION}</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF
        
        # Create DMG
        hdiutil create -volname "JARVIS ${VERSION}" -srcfolder "${APP_DIR}" -ov -format UDZO "${DIST_DIR}/jarvis-${VERSION}-${PLATFORM}.dmg"
        ;;
        
    windows|CYGWIN*|MINGW*|MSYS*)
        echo "📦 Windows installer handled by GitHub Actions (Inno Setup)"
        # Windows build handled by GitHub Actions (build-installer.yml)
        ;;
esac

# Generate checksums
echo "🔐 Generating checksums..."
cd "${DIST_DIR}"
for file in *; do
    if [[ -f "${file}" ]]; then
        sha256sum "${file}" > "${file}.sha256"
        echo "  ${file}: $(cat ${file}.sha256 | cut -d' ' -f1)"
    fi
done

# Create manifest
cat > "${DIST_DIR}/manifest.json" <<EOF
{
  "version": "${VERSION}",
  "channel": "${CHANNEL}",
  "platform": "${PLATFORM}",
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "git_commit": "$(git rev-parse HEAD 2>/dev/null || echo 'unknown')",
  "files": [
EOF

for file in "${DIST_DIR}"/*; do
    if [[ -f "${file}" && "${file}" != *.sha256 && "${file}" != *manifest.json ]]; then
        filename=$(basename "${file}")
        sha256=$(cat "${file}.sha256" 2>/dev/null | cut -d' ' -f1 || echo "unknown")
        size=$(stat -c%s "${file}" 2>/dev/null || stat -f%z "${file}" 2>/dev/null)
        echo "    {\"name\": \"${filename}\", \"sha256\": \"${sha256}\", \"size\": ${size}}," >> "${DIST_DIR}/manifest.json"
    fi
done

# Remove trailing comma
sed -i '$ s/,$//' "${DIST_DIR}/manifest.json"

cat >> "${DIST_DIR}/manifest.json" <<EOF
  ]
}
EOF

echo "✅ Build complete! Artifacts in ${DIST_DIR}"
ls -la "${DIST_DIR}"