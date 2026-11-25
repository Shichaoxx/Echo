#!/bin/sh
# From the extracted FFmpeg source directory, pass a destination for the
# relocatable tools. Only system libraries and the replaceable LGPL dylibs link.
set -eu
destination=$1
export MACOSX_DEPLOYMENT_TARGET=15.0
./configure --prefix=/ --arch=arm64 --cc=clang --disable-autodetect --disable-doc \
  --disable-debug --disable-network --disable-avdevice --disable-ffplay \
  --disable-static --enable-shared --install-name-dir=@loader_path \
  --disable-encoders --enable-encoder=pcm_s16le \
  --disable-muxers --enable-muxer=wav,pcm_s16le \
  --disable-filters --enable-filter=aresample,aformat,anull,atrim
make -j"$(sysctl -n hw.ncpu)"
mkdir -p "$destination"
cp ffmpeg ffprobe "$destination/"
for library in lib*/*.dylib; do
  cp -P "$library" "$destination/"
done
