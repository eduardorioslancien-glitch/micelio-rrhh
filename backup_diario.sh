#!/bin/bash
# Backup diario de MICELIO (base de datos + archivos subidos en tiempo de
# ejecucion). Se guarda localmente en ~/micelio-rrhh/backups/ y se borran
# los que tengan mas de 30 dias. La subida a Google Drive se agrega aparte
# una vez que este definido el metodo de autenticacion.
set -e
cd /home/ubuntu/micelio-rrhh
FECHA=$(date +%Y%m%d_%H%M%S)
DEST="backups/micelio_backup_${FECHA}.tar.gz"
tar -czf "$DEST" \
  data/hrapp.db \
  app/generated app/uploads app/fotos app/signatures app/firmas_empresa app/cv_postulantes app/anuncios_imagenes \
  2>/dev/null
echo "$(date): backup creado en $DEST ($(du -h "$DEST" | cut -f1))" >> backups/backup.log
find backups/ -name "micelio_backup_*.tar.gz" -mtime +30 -delete
