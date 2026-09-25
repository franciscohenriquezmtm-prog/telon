#!/bin/bash
# Une varios clips de iPhone (HDR, vertical u horizontal) en un solo video SDR 1080x1920 a 30 fps,
# para analizarlos como una sola capacitación.  Edite la lista de archivos y ejecute: bash tools/unir_clips.sh
set -e
cd /home/user/telon/resumen-videos
FF=.venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2
INS=""; FC=""; N=0
for f in videos/IMG_4976.mov videos/IMG_4978.mov videos/IMG_4980.mov videos/IMG_4981.mov videos/IMG_4982.mov videos/IMG_4983.mov videos/IMG_4984.mov videos/IMG_4985.mov videos/IMG_4986.mov videos/IMG_4987.mov videos/IMG_4988.mov videos/IMG_4989.mov videos/IMG_4992.mov videos/IMG_4993.mov; do
  INS="$INS -i $f"
  FC="$FC[$N:v]zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1[v$N];[$N:a]aresample=48000,aformat=channel_layouts=mono[a$N];"
  N=$((N+1))
done
CAT=""; for i in $(seq 0 $((N-1))); do CAT="$CAT[v$i][a$i]"; done
FC="$FC${CAT}concat=n=$N:v=1:a=1[v][a]"
$FF -y -hide_banner -loglevel warning -stats $INS -filter_complex "$FC" -map "[v]" -map "[a]" -c:v libx264 -preset fast -crf 16 -pix_fmt yuv420p -c:a aac -b:a 96k -movflags +faststart videos/Capacitacion_arco_en_C.mp4
echo UNIDO
