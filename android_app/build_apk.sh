#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

BUILD_TOOLS="/opt/android-sdk/build-tools/34.0.0"
PLATFORM="/opt/android-sdk/platforms/android-34/android.jar"

echo "=== Building Lexi Connect Native Android APK ==="

rm -rf gen bin compiled_res.zip unaligned.apk aligned.apk lexi-connect.apk
mkdir -p gen bin

echo "[1/6] Compiling Android resources..."
$BUILD_TOOLS/aapt2 compile --dir res -o compiled_res.zip

echo "[2/6] Linking Android package & generating R.java..."
$BUILD_TOOLS/aapt2 link -I "$PLATFORM" compiled_res.zip --manifest AndroidManifest.xml -o unaligned.apk --java gen

echo "[3/6] Compiling Java source files..."
javac --release 17 -cp "$PLATFORM:src:gen" -d bin gen/com/lexi/connect/*.java src/com/lexi/connect/*.java

echo "[4/6] Converting Java bytecodes to Android Dalvik/ART Dex..."
$BUILD_TOOLS/d8 --lib "$PLATFORM" bin/com/lexi/connect/*.class --output bin/

echo "[5/6] Packaging DEX and Aligning APK..."
cd bin
zip -u ../unaligned.apk classes.dex > /dev/null
cd ..
$BUILD_TOOLS/zipalign -p -f 4 unaligned.apk aligned.apk

echo "[6/6] Cryptographically signing APK..."
if [ ! -f debug.keystore ]; then
    keytool -genkey -v -keystore debug.keystore -storepass android -alias androiddebugkey -keypass android -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Lexi Connect,O=Lexi,C=US" > /dev/null 2>&1
fi

$BUILD_TOOLS/apksigner sign --ks debug.keystore --ks-pass pass:android --key-pass pass:android --out lexi-connect.apk aligned.apk

# Verify signature
$BUILD_TOOLS/apksigner verify lexi-connect.apk

# Deploy to drop_web directory so users can download it directly from phone browser
cp lexi-connect.apk ../drop_web/lexi-connect.apk

echo "SUCCESS: Built and signed $(du -h lexi-connect.apk | cut -f1) APK at $DIR/lexi-connect.apk"
echo "Public Download Link: http://10.93.59.11:7890/lexi-connect.apk"
