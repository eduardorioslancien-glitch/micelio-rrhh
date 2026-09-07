#!/bin/bash
# Backup diario de MICELIO (base de datos + archivos subidos en tiempo de
# ejecucion). Se guarda localmente en ~/micelio-rrhh/backups/ (se borran los
# que tengan mas de 30 dias) y ademas se sube a la Unidad compartida de
# Google Drive "MICELIO Backups" (cuenta erios@digetelgroup.com) via rclone,
# usando una Service Account (remoto "gdrive_micelio" en rclone.conf).
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

# Subida a Google Drive. No usar "set -e" estricto aca: si Google Drive falla
# (red caida, cuota, etc.) el backup local ya quedo a salvo y no debe abortar
# el script con error; solo se deja constancia en el log.
if rclone copy "$DEST" gdrive_micelio: --quiet 2>>backups/backup.log; then
  echo "$(date): subido a Google Drive OK ($DEST)" >> backups/backup.log
else
  echo "$(date): ERROR subiendo a Google Drive ($DEST)" >> backups/backup.log
fi
