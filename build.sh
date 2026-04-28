#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
rm -f lyric_video_blender.zip
python3 -c "
import zipfile, os
with zipfile.ZipFile('lyric_video_blender.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk('lyric_video_blender'):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in files:
            if not f.endswith('.pyc'):
                path = os.path.join(root, f)
                z.write(path)
"
echo "Built: lyric_video_blender.zip"
