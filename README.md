# Sistema RR.HH. DIGETEL GROUP — Prototipo

Este prototipo es el arranque del sistema completo de Recursos Humanos del
grupo (Digetel, Intecno, UM Infraestructura, UM Digital, Evolution,
Enjambre): base de datos de personal, reclutamiento/selección, onboarding,
control de accesos por rol y, más adelante, capacitación, esquemas
salariales, proyecciones de cobertura de puestos y clima organizacional.

Tiene dos módulos conectados a la misma base de datos:

## Módulo 1 — Administración de Personal (`/rrhh`) — NUEVO

La base de datos maestra del grupo, organizada por Unidad de Negocio →
Empresa, con control de accesos por rol:

- **Administrador**: acceso total.
- **Conta**: acceso a la parte de planillas (datos bancarios, previsionales
  y remuneración) de todo el personal.
- **OpeOKA**: acceso a la parte operativa (datos laborales, sin ver banco ni
  sueldo).
- **Usuario**: acceso solo a su propia información (autoservicio).

Cada trabajador tiene: foto (actualizable), bitácora de acciones registrada
por RR.HH., documentos del legajo (certificados de cursos, memos, documentos
de salud, constancias de alta/baja en SUNAT, y los documentos firmados del
proceso de Selección), y estado activo/cesado.

La **Parametrización** (`/rrhh/parametrizacion`, solo Administrador) permite
crear/editar las Unidades de Negocio y Empresas del grupo, con su régimen
laboral (MYPE, Régimen General, etc.). Ya viene sembrada con la estructura
que describiste (UM Servicios: Digetel e Intecno; UM Infraestructura; UM
Digital; Evolution; Enjambre) — las dos empresas sin nombre confirmado
quedaron como "(completar nombre)", edítalas desde ahí.

Los **Usuarios del sistema** (`/rrhh/usuarios`, solo Administrador) se crean
con usuario/contraseña y rol; para el rol "Usuario" se vinculan a su propio
registro de trabajador.

## Módulo 3 — Control de Asistencia (`/rrhh/asistencia`) — NUEVO

Marcado de entrada/salida: cada trabajador con cuenta propia (rol "Usuario")
marca su propia asistencia desde su ficha ("Marcar entrada" / "Marcar
salida"); RR.HH. (Administrador/OpeOKA) puede marcar en nombre de alguien o
registrar una corrección manual con fecha/hora y motivo (para quien olvidó
marcar o todavía no tiene cuenta). La vista de asistencia del día muestra
quién marcó y quién falta, filtrable por empresa.

## Módulo 4 — Dashboard de KPIs (`/rrhh/dashboard`) — NUEVO

Indicadores calculados sobre un periodo configurable (7/30/90 días):

- **Headcount activo**, total y desglosado por Empresa y por Unidad de Negocio.
- **Incorporaciones** (altas) y **bajas** del periodo.
- **Rotación** = bajas del periodo / headcount activo actual × 100 (fórmula
  simplificada de prototipo — se puede afinar con más historia de datos).
- **Ausentismo** = % de días hábiles del periodo en que un trabajador activo
  no marcó su entrada.

## Navegación: menú lateral y control de accesos

Todas las páginas de Administración de Personal usan un menú lateral
colapsable (clic en cada tema para expandirlo) con 5 secciones principales:
Dashboard, Administración, Reclutamiento y Selección, Capacitación, Clima y
Cultura. Qué ve cada rol:

- **Administrador**: los 5 temas completos.
- **Conta / OpeOKA**: Dashboard, Administración (Personal, Asistencia — sin
  Parámetros ni Usuarios del Sistema, eso es solo Administrador),
  Reclutamiento y Selección, Capacitación, Clima y Cultura.
- **Usuario**: solo su propia ficha (Administración > Personal) y
  Capacitación — nada más aparece en su menú, y el resto de las rutas están
  bloqueadas también del lado del servidor aunque escriba la URL a mano.

Las secciones que todavía no tienen desarrollo propio (Entrevista por
Competencias, Firma de Contrato, "Principios, Valores y Competencias") se
muestran atenuadas con la etiqueta "Próximamente" — están bloqueadas porque
falta información de negocio que solo RR.HH. de DIGETEL GROUP tiene (el
marco de competencias, el formato de contrato).

## Módulo 5 — Reclutamiento y Selección — NUEVO

- **Registro de Pedidos** (`/rrhh/reclutamiento/pedidos`): RR.HH. registra
  cada solicitud de personal de un área (cargo, empresa, cantidad, motivo,
  urgencia, solicitante, fecha requerida) y le hace seguimiento de estado
  (abierto → en proceso → cubierto/cancelado).
- **Control de Leads** (`/rrhh/reclutamiento/leads`): pipeline de candidatos
  (nuevo → contactado → en entrevista → oferta enviada → contratado/
  descartado), opcionalmente vinculados a un Pedido.
- **Selección** (`/admin`): el módulo original — enlace único por
  postulante, ficha, documentos y firma electrónica de los 5 documentos
  legales. Sigue siendo una sola pantalla; separarla en Ficha de Datos /
  Control Documentos / Firma de Contrato queda pendiente.
- **Onboarding**: pestaña nueva dentro de la ficha de cada trabajador
  (`/rrhh/personal/{id}#onboarding`) para registrar Inducción General,
  Acompañamiento, Evaluación y Feedback; la vista
  `/rrhh/reclutamiento/onboarding` muestra el avance (X/4 etapas) de todos
  los trabajadores activos.

## Módulo 6 — Clima y Cultura — NUEVO

- **Encuesta 360** (`/rrhh/clima/encuestas`): RR.HH. crea campañas con sus
  propias preguntas (escala 1-5 — no hay un set fijo de competencias
  todavía) y carga las respuestas de cada evaluador sobre cada evaluado
  (autoevaluación/jefe/par/subordinado/otro). El sistema calcula el
  promedio por pregunta y el promedio general de cada campaña. Un portal
  público para que cada evaluador responda por su cuenta (en vez de que
  RR.HH. cargue las respuestas) queda como posible siguiente paso.
- **Indicadores de Gestión** (`/rrhh/clima/indicadores`): reutiliza
  headcount/rotación/ausentismo del Dashboard y agrega la participación
  (% del headcount activo evaluado) y el promedio general de cada campaña
  de Encuesta 360.

### Usuario administrador por defecto

Al arrancar el servidor por primera vez se crea automáticamente:

```
Usuario:    admin
Contraseña: digetel2026
```

El sistema te **obliga a cambiarla** apenas inicias sesión (no te deja
navegar a ninguna otra parte hasta que la cambies) — o define
`RRHH_ADMIN_USER` / `RRHH_ADMIN_PASSWORD` como variables de entorno antes del
primer arranque para que se cree con otras credenciales. Lo mismo aplica a
cualquier usuario nuevo que cree un Administrador, o a quien le reseteen la
contraseña: deben cambiarla en su primer ingreso siguiente.

La cookie de sesión se firma con una clave que se genera sola la primera vez
y se guarda en `data/secret_key.txt` (no es un valor compartido igual en
todas las instalaciones). Si prefieres definir la tuya, usa la variable de
entorno `RRHH_SECRET_KEY`.

## Checklist para cuando decidan subirlo a un servidor

Este prototipo ya está preparado para correr en tu computadora con
seguridad razonable (login obligatorio, contraseñas hasheadas, clave de
sesión propia). Antes de subirlo a un servidor real (así sea Render,
DigitalOcean u otro), falta:

1. **Definir `RRHH_SECRET_KEY` explícitamente** como variable de entorno del
   servidor (no dejar que se autogenere en el disco del hosting).
2. **Activar HTTPS** — normalmente lo da el proveedor de hosting o un proxy
   como Nginx/Caddy delante de la app; sin esto, las contraseñas y datos
   viajan sin cifrar.
3. **Evaluar si SQLite alcanza**: para pocas personas de RR.HH. usándolo a
   la vez, SQLite funciona bien igual en producción; si va a haber uso
   simultáneo más intenso, migrar a Postgres (avísame y lo hago).
4. **Backups automáticos** de `data/hrapp.db` y de `app/generated/`,
   `app/uploads/`, `app/fotos/`, `app/signatures/` (documentos y fotos del
   legajo) — hoy solo viven en el disco del servidor.
5. **Configurar el correo automático** (`SMTP_*`, ver sección arriba) si se
   quiere usar en producción.

## Cómo desplegarlo en cPanel (hosting compartido con "Setup Python App")

Este es el camino pensado para un hosting compartido tipo cPanel/CloudLinux
que tenga los íconos **"Setup Python App"** y acceso a **Administrador de
archivos** (no hace falta terminal/SSH ni Node.js ni LibreOffice — el PDF se
genera con `reportlab`, que se instala solo con `pip`).

1. **Sube el código.** Comprime en un .zip la carpeta completa de este
   prototipo (`07 Prototipo Portal Web (idea)/`, sin `node_modules/` ni
   `data/hrapp.db` si ya tienes datos de prueba que no quieras subir) y
   súbelo con el **Administrador de archivos** de cPanel a la carpeta donde
   quieras que viva la app (por ejemplo `/home/usuario/hrapp/`). Extráelo ahí.
2. **Crea la aplicación Python.** Entra a **Software → Setup Python App →
   Create Application**:
   - **Python version**: la más alta disponible (3.10+).
   - **Application root**: la carpeta donde extrajiste el código (ej. `hrapp`).
   - **Application URL**: el dominio o subdominio donde va a vivir (ej.
     `digetelgroup.com` o `rrhh.digetelgroup.com`).
   - **Application startup file**: `passenger_wsgi.py`
   - **Application Entry point**: `application`
3. **Instala las dependencias.** Ya dentro de la app creada, en el mismo
   panel de "Setup Python App" hay un campo para apuntar a
   `requirements.txt` y un botón para instalarlas — no necesitas terminal.
   Si en tu panel no aparece ese botón, copia el comando que cPanel muestra
   arriba (algo como `source /home/usuario/virtualenv/hrapp/3.x/bin/activate
   && cd /home/usuario/hrapp`) y agrégale `&& pip install -r
   requirements.txt`; puedes correrlo desde una **Tarea Cron** de una sola
   vez (Avanzado → Tareas Cron) si tampoco tienes terminal.
4. **Define las variables de entorno** en la misma pantalla de "Setup Python
   App" (sección "Environment variables"): como mínimo `RRHH_SECRET_KEY`
   (un valor propio y secreto); opcionalmente `SMTP_*` para el correo
   automático (ver sección arriba) y `HRAPP_DB_PATH` si quieres la base de
   datos fuera de una carpeta sincronizada.
5. **Reinicia la app** con el botón "Restart" del panel cada vez que subas
   cambios de código.
6. **Backups**: agrega `data/hrapp.db` y `app/generated/`, `app/uploads/`,
   `app/fotos/`, `app/signatures/` al backup automático del hosting (cPanel
   suele traer esto en "Backup" — estos archivos son el legajo real de cada
   trabajador, no viven en ningún otro lado).

Si cuando llegues al paso 2 tu cPanel **no** tiene "Setup Python App" (varía
según el proveedor), o el hosting no permite procesos Python de larga
duración, avísame y lo adaptamos a un VPS o a Render/Railway en su lugar.

## Módulo 2 — Selección de Personal (`/admin`, enlaces `/f/{token}`)

Prototipo funcional de un portal donde RR.HH. genera un enlace único por
trabajador/postulante. La persona completa su Ficha de Datos del Personal
—ahora con las 11 secciones completas: datos personales, información
familiar, contactos de emergencia, datos laborales, información bancaria,
información previsional, educación, experiencia laboral, capacitaciones,
tallas y salud—, adjunta sus documentos de sustento (CV, Certificado Único
Laboral, antecedentes policiales, otros) y firma electrónicamente los 5
documentos legales del legajo (Ficha, Declaración Jurada, Autorización de
Datos Personales, Derechohabientes EsSalud, Autorización de Depósito de
Haberes y CTS). El sistema:

- guarda todo en una base de datos (empleados, ficha completa, familia,
  educación, experiencia, capacitaciones, documentos adjuntos, firmas),
- genera automáticamente el PDF final de cada documento (con el mismo diseño
  corporativo del Kit RR.HH., ver `app/pdf_signed.py`) con la firma incrustada
  y un pie de auditoría (fecha/hora, IP, huella digital/hash del contenido) —
  el PDF se arma directo en Python (reportlab), sin depender de Word, Node.js
  ni LibreOffice, para que funcione igual en tu computadora que en cualquier
  hosting compartido,
- deja un registro (bitácora) de cuándo se generó el enlace, cuándo se abrió,
  cuándo se guardó la ficha, cuándo se subió cada documento y cuándo se firmó
  cada documento,
- al completarse el legajo, envía automáticamente un correo al
  trabajador/postulante con copia de todos sus documentos firmados (si el
  correo está configurado, ver sección "Correo automático" más abajo),
- permite a RR.HH. presentar ante SUNAFIL solo las secciones del legajo que
  necesite, como un único PDF con carátula (ver "Exportación selectiva para
  SUNAFIL"),
- permite a RR.HH. exportar a un solo Excel (multi-hoja) la información de
  TODAS las personas registradas.

El legajo está pensado para terminar con la firma del **contrato de
trabajo**; esa pieza queda prevista en el modelo de datos pero todavía no
implementada porque falta que RR.HH. entregue el formato/plantilla según el
tipo de contratación (ver la nota al inicio de `app/models.py`).

## Estructura

```
hrapp/
  app/
    main.py              FastAPI: arranque de la app, rutas de Selección (/admin, /f/token)
    rrhh.py               Rutas de Administración de Personal (/rrhh/*): login incluido en main.py
    reclutamiento.py        Registro de Pedidos, Control de Leads, Onboarding (resumen)
    clima.py                  Encuesta 360 e Indicadores de Gestión
    auth.py                     Login, hash de contraseñas, control de accesos por rol
    seed.py                      Datos iniciales: Unidades de Negocio, Empresas, bancos, usuario admin
    models.py                     Tablas: Employee, Empresa, UnidadNegocio, User, BitacoraEntry,
                                   Document, Attachment, Signature, AuditLog, Catalogo,
                                   PedidoPersonal, LeadCandidato, OnboardingRegistro,
                                   EncuestaCampana, EncuestaRespuesta
    database.py                    Conexión SQLite
    export_xlsx.py                   Exportación masiva a Excel (multi-hoja)
    sunafil_export.py                 Exportación selectiva a PDF para una revisión de SUNAFIL
    pdf_signed.py                       Genera el PDF final de cada documento firmado (reportlab,
                                         Python puro — reemplaza el pipeline anterior de Word/LibreOffice)
    legal_texts.json                     Textos legales (única fuente, la usan la web y los PDF)
    templates/                             login.html, rrhh_*.html (Administración de Personal,
                                            Reclutamiento, Clima y Cultura), _rrhh_topbar.html (menú
                                            lateral), admin_dashboard.html, admin_detalle.html,
                                            formulario.html (Selección)
    static/                          style.css, dg_logo.png
    fotos/                            fotos de perfil de cada trabajador
    generated/                        PDFs generados (se crean en tiempo de ejecución)
    signatures/                       imágenes de firma capturadas
    uploads/                          documentos adjuntados a cada legajo (CV, CUL, memos, etc.)
  data/
    hrapp.db                      Base de datos SQLite (se crea sola)
  passenger_wsgi.py           Punto de entrada para Passenger (despliegue en cPanel)
  test_e2e.py               Prueba automatizada del flujo de Selección
  requirements.txt
```

### Si ya tenías el prototipo instalado (versión anterior)

El modelo de datos volvió a cambiar (Empresa, UnidadNegocio, User,
BitacoraEntry son nuevos; Employee ganó columnas). Antes de levantar esta
versión, borra la base de datos anterior para que se regenere con el
esquema nuevo (vas a perder los datos de prueba que hayas cargado; si son
datos reales que quieres conservar, avísame para escribir una migración en
vez de borrar):

```powershell
Remove-Item "data\hrapp.db" -ErrorAction SilentlyContinue
```

También instala las dependencias nuevas (`pypdf`, `itsdangerous`, ya están
en `requirements.txt`):

```powershell
pip install -r requirements.txt
```

## Cómo correrlo en Windows (tu computadora)

### 1. Instalar lo necesario (una sola vez)

| Programa | Para qué | Descarga |
|---|---|---|
| Python 3.10 o superior | Correr el servidor y generar los PDF firmados | https://www.python.org/downloads/ (marca "Add python.exe to PATH" durante la instalación) |

Ya no hace falta Node.js ni Word/LibreOffice: los documentos firmados se
generan directo en PDF con `reportlab` (Python), que se instala con el resto
de dependencias en el paso 3.

### 2. Abrir PowerShell en la carpeta del proyecto

Abre PowerShell (o CMD) y entra a la carpeta donde quedó el prototipo:

```powershell
cd "C:\Users\Edu\OneDrive\DIGITEL-INTECNO\HHRR\Kit RRHH DIGETEL GROUP\07 Prototipo Portal Web (idea)"
```

### 3. Instalar dependencias (una sola vez)

```powershell
pip install -r requirements.txt
```

### 4. Levantar el servidor

```powershell
python -m uvicorn app.main:app --reload --port 8000
```

Déjalo corriendo (la ventana de PowerShell debe quedar abierta) y abre en tu
navegador:

```
http://localhost:8000
```

Te pedirá iniciar sesión (usuario `admin` / contraseña `digetel2026` la
primera vez — cámbiala desde "Mi cuenta"). Desde ahí navegas a:

- **Administración de Personal** (`/rrhh/personal`): la BD maestra del grupo.
- **Selección de Personal** (`/admin`): donde generas el enlace único que
  llena y firma cada postulante/trabajador. El enlace generado
  (`http://localhost:8000/f/<token>`) es el que le compartes — no requiere
  login, es de un solo uso por persona. Por ahora solo funciona dentro de tu
  propia red/computadora; para que sea accesible desde el celular de otra
  persona hace falta desplegarlo (ver sección siguiente).
- **Parametrización** y **Usuarios** (solo rol Administrador).

Para detener el servidor, vuelve a la ventana de PowerShell y presiona
`Ctrl+C`.

**Si al firmar un documento aparece "Error al generar el documento firmado"**:
el mensaje incluye el motivo real (por ejemplo, un campo con un formato
inesperado). Revisa el detalle del error en pantalla o en la consola donde
corre `uvicorn` y avísame con ese texto si no es evidente la causa.

**Si ves el error "disk I/O error"**: significa que SQLite no puede escribir
en esa carpeta (pasa a veces en carpetas sincronizadas). Soluciónalo
apuntando la base de datos a una carpeta local antes de levantar el
servidor:

```powershell
$env:HRAPP_DB_PATH = "C:\hrapp_data\hrapp.db"
python -m uvicorn app.main:app --reload --port 8000
```

### Prueba automatizada

Con el servidor corriendo en el puerto 8000:

```bash
python test_e2e.py
```

Simula todo el flujo (crear empleado, llenar ficha, firmar los 5 documentos)
y verifica que los PDFs se generan correctamente.

## Alternativas de despliegue (si no usas cPanel)

Si el hosting no es cPanel/CloudLinux (ver la sección "Cómo desplegarlo en
cPanel" más arriba, que es el camino pensado para digetelgroup.com), estas
son otras opciones sencillas:

1. **Render.com o Railway.app** (recomendado para empezar): conectas el
   repositorio, defines el comando de arranque
   (`uvicorn app.main:app --host 0.0.0.0 --port $PORT`), y usan disco
   persistente para `data/hrapp.db` y `app/generated/`. Ambos tienen plan
   gratuito/económico suficiente para una PYME.
2. **Un VPS propio** (DigitalOcean, AWS Lightsail, etc.) con Nginx +
   Gunicorn/Uvicorn.

Antes de usarlo en producción con datos reales, hay que:

- **Agregar autenticación al panel `/admin`** (hoy no tiene login — cualquiera
  con la URL puede entrar). Lo más simple: usuario/contraseña con
  `fastapi.security.HTTPBasic`, o una cuenta de Google/Microsoft si ya usan
  Workspace/365.
- **Restringir la descarga de PDFs** (`/descargas/{id}`) para que solo el
  propio trabajador (con su token) o un admin autenticado puedan acceder.
- **Migrar de SQLite a Postgres** si va a haber varias personas de RR.HH.
  usando el sistema a la vez (SQLite alcanza para una PYME con un solo
  operador, pero Postgres es más robusto para uso concurrente).
- **Enviar el enlace por correo automáticamente** en vez de copiarlo a mano:
  se agrega un proveedor de correo (SendGrid, Amazon SES, o SMTP de Gmail
  corporativo) y se dispara un email al crear el enlace.
- **Decidir el nivel de firma legal que necesitas.** Tal como está, el
  sistema implementa *firma electrónica simple* con buen registro de
  auditoría (imagen de la firma, fecha/hora, IP, hash del documento) — válida
  para la mayoría de documentos internos de RR.HH. en Perú. Si en algún
  momento se necesita *firma digital certificada* (Ley N.° 27269, con
  certificado de una entidad acreditada), eso requiere integrar un proveedor
  externo (DocuSign, o uno peruano) en vez de la firma dibujada a mano que
  usa hoy el prototipo.

## Correo automático al completar el legajo

Al firmar el último documento del legajo, el sistema intenta enviar un
correo al trabajador/postulante con copia de todos sus documentos firmados
en PDF. Por defecto esto está "apagado" (no falla nada si no lo configuras);
para activarlo define estas variables de entorno antes de levantar el
servidor:

```powershell
$env:SMTP_HOST = "smtp.gmail.com"
$env:SMTP_PORT = "587"
$env:SMTP_USER = "rrhh@digetelgroup.pe"
$env:SMTP_PASS = "tu-clave-de-aplicacion"
$env:SMTP_FROM = "rrhh@digetelgroup.pe"
python -m uvicorn app.main:app --reload --port 8000
```

Si usas Gmail/Google Workspace necesitas una "contraseña de aplicación" (no
tu contraseña normal). Si `SMTP_HOST` no está definido, el legajo se marca
como completo igual, simplemente no se envía el correo (queda registrado en
la bitácora como intento sin configurar).

## Exportación selectiva para SUNAFIL

En `/admin/empleado/{id}` hay una sección "Presentar información ante
SUNAFIL" con casillas para elegir qué documentos firmados y qué adjuntos
incluir. Al generar, se descarga un único PDF con una carátula (nombre,
empresa, fecha de generación y detalle de qué se incluyó) seguido de cada
documento seleccionado, ideal para entregar en una inspección sin exponer
todo el legajo completo.

## Cómo extender los campos de la ficha

El formulario web ahora captura las 11 secciones completas (ver
`app/templates/formulario.html`, pasos `1a` a `1h`). Para agregar un campo
nuevo dentro de una sección existente:

1. Agrega el `<input>`/`<select>` correspondiente en el paso adecuado de
   `app/templates/formulario.html`.
2. Agrega su `id` a la lista `simpleIds` dentro de la función
   `collectFicha()` en el mismo archivo (o, si es una tabla dinámica como
   familia/educación/experiencia/capacitaciones, agrégalo a la fila
   correspondiente en su función `addXxxRow()`/`collectXxx()`).
3. El campo queda disponible automáticamente en la exportación a Excel si
   sigue el mismo patrón; para que aparezca en el PDF, agrégalo en
   `build_doc_fields()` (`app/main.py`) y en la tabla correspondiente dentro
   de `app/pdf_signed.py` (funciones `_doc_ficha`, `_doc_derechohabientes`,
   `_doc_autorizacion_deposito`, según el documento).

## Limitaciones conocidas de este prototipo

- El login ya existe (usuario/contraseña + roles), pero la cookie de sesión
  se firma con una clave de desarrollo fija; antes de usarlo con datos reales
  en un servidor accesible desde internet, define `RRHH_SECRET_KEY` con un
  valor propio y secreto.
- El control de accesos por rol está aplicado a nivel de página/sección (lo
  que pediste: Conta ve planillas, OpeOKA ve lo operativo, Usuario ve solo lo
  suyo); no hay todavía un permiso campo por campo más fino.
- El correo automático requiere configurar SMTP (ver sección arriba); sin
  esa configuración, el enlace inicial se sigue mostrando en pantalla para
  copiar y enviar manualmente.
- La firma es dibujada a mano (firma electrónica simple), no firma digital
  certificada.
- El contrato de trabajo todavía no está implementado como documento
  firmable (falta el formato de RR.HH.); el modelo de datos ya lo prevé.
- Un solo idioma (español) y una sola moneda (soles).
- Los PDFs y documentos adjuntos se guardan en el disco del servidor; en
  producción conviene moverlos a almacenamiento en la nube (S3, Google Cloud
  Storage) para no perderlos si el servidor se reinicia.
