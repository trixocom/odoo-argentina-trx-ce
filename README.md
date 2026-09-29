# 📦 l10n_ar_trixocom

> **Localización Argentina completa para Odoo 19 Community Edition**
>
> Facturación electrónica AFIP/ARCA · Padrones provinciales · Contingencia · Auditoría

![Odoo](https://img.shields.io/badge/Odoo-19.0-7c3aed) ![License](https://img.shields.io/badge/License-LGPL--3-blue) ![Status](https://img.shields.io/badge/Status-Producci%C3%B3n-10b981) ![Modules](https://img.shields.io/badge/M%C3%B3dulos-14-orange)

---

## 📊 En números

| | |
|---|---|
| **14** | módulos |
| **7** | Web Services AFIP/ARCA implementados |
| **4** | padrones provinciales (BA, CABA, Santa Fe, Córdoba) |
| **9** | crones automáticos |
| **60+** | errores AFIP humanizados |

---

## 🎯 Visión general

**l10n_ar_trixocom** es un paquete completo de módulos que extiende Odoo 19 Community con todas las funcionalidades necesarias para operar fiscalmente en Argentina.

**Cubre todo el ciclo fiscal:**
- ✅ Emisión electrónica de comprobantes contra AFIP/ARCA
- ✅ Impresión PDF con QR oficial RG 4291
- ✅ Verificación de facturas recibidas (WSCDC)
- ✅ Reportes fiscales mensuales
- ✅ Percepciones IIBB de las 4 jurisdicciones más facturadas
- ✅ Contingencia con CAEA cuando AFIP cae
- ✅ Auditoría completa de todas las llamadas a los WS

### Lo que aporta sobre Odoo Community

Odoo Community trae el módulo base `l10n_ar` con el plan de cuentas, tipos de comprobante y la condición frente a IVA — pero **no incluye** emisión electrónica ni los reportes que la operativa fiscal argentina requiere. Este paquete agrega:

- **Emisión electrónica completa** contra los WS oficiales WSAA, WSFEv1, WSFEX, WSCDC, CAEA
- **QR de validación AFIP** en el PDF según RG 4291
- **Reportes regulatorios**: Libro IVA Digital RG 5616, IVA Simple del portal ARCA, cotejo Mis Comprobantes
- **Padrones IIBB de 4 jurisdicciones** con aplicación automática de percepciones
- **Régimen de contingencia CAEA** con solicitud, fallback y rendición automatizadas
- **POS con factura electrónica** emitida en el momento del cobro
- **Cotización USD/EUR diaria** automática desde fuente oficial
- **Auditoría WS**: log completo de cada llamada a AFIP/ARCA con XML req/resp

---

## 🗺️ Mapa de módulos

```
                          ┌──────────────────┐
                          │   l10n_ar (std)  │
                          └─────────┬────────┘
                                    │
        ┌───────────────────────────┼─────────────────────────┐
        │                           │                          │
┌───────▼──────────────┐ ┌──────────▼───────┐    ┌────────────▼─────┐
│ trx_l10n_ar_edi_base │ │  l10n_ar_afip_ws │    │   certificate    │
│    (campos AFIP)     │ │  (WSAA + WSFE)   │    │ (cert + key)     │
└──────────┬───────────┘ └────────┬─────────┘    └──────────────────┘
           │                      │
           └──────────┬───────────┘
                      │
            ┌─────────▼────────┐
            │  trx_l10n_ar_edi │  Orquestador emisión + QR
            └─────────┬────────┘
                      │
        ┌─────────────┼──────────────┬────────────┬────────────┐
        │             │              │            │            │
   l10n_ar_caea  trx_l10n_ar_    Libro IVA   IVA Simple   Mis Cbtes
   (contingencia)  pos_edi        Digital                   (cotejo)
                 (POS+FE)

              ┌──────────────────────┐
              │  l10n_ar_padron_base │   ← helpers compartidos
              └──────────┬───────────┘
                         │
         ┌───────────────┼───────────────┬───────────────┐
         │               │               │               │
   ARBA (BA)        AGIP (CABA)    API (SF)        Rentas (CBA)
   l10n_ar_iibb_   l10n_ar_       l10n_ar_         l10n_ar_
   percepciones    padron_caba    padron_santafe   padron_cordoba
```

---

## 🚀 Funcionalidades por área

### 1️⃣ Emisión electrónica

#### 📄 Facturación mercado interno (WSFEv1)

Emite **FA-A, FA-B, FA-C, NC, ND** contra el WS WSFEv1 de AFIP. El sistema:

- Construye el payload XML completo (concepto, IVA discriminado, tributos, condición frente IVA del receptor según RG 5616)
- Autentica vía WSAA con cert X.509 + firma CMS
- Llama a `FECAESolicitar` y obtiene el CAE
- Genera el QR de validación según RG 4291 e incluye en el PDF
- Persiste CAE, vencimiento, observaciones y XML completo para auditoría

**Validado en producción AFIP**: FA-A, FA-B, NC-A, NC-B, FA-A en USD, FA-A con percepción IIBB BA.

#### 🌍 Factura de exportación (WSFEX)

Emite **FA-E, NC-E, ND-E** contra el WS WSFEX:

- Soporta 3 tipos: bienes, servicios, otros
- Para bienes pide `Permisos` (DUA / despachos)
- Asocia NC-E / ND-E con la FA-E original (`Cmps_asoc`)
- Valida país destino, CUIT del país, incoterms, forma de pago

**Validado en producción** — FA-E $100 ARS con CAE 86173136340331.

#### 🛡️ CAEA — Contingencia (RG 2926)

Cuando WSFEv1 cae (timeouts, errores), el sistema sigue facturando con un código pre-asignado por quincena.

**Full automatizado:**

| Cron | Frecuencia | Acción |
|---|---|---|
| Solicitud | 09:00 días 11-15 / 27-fin | Pide CAEA de la próxima quincena |
| Fallback `_post` | Al postear factura | Si WSFE da timeout, usa CAEA vigente |
| Rendición | 03:00 diario | `FECAEARegInformativo` con comprobantes pendientes |
| Sin movimiento | 03:00 diario | Informa quincenas cerradas sin uso |

**Para el operador:** activar el switch en Configuración y listo. Si AFIP cae, sigue facturando sin enterarse.

#### 🛒 POS con factura electrónica

- Botón **"Factura Electrónica"** en pantalla de pago
- CAE en tiempo real durante el cobro
- Ticket impreso con QR + CAE legibles

---

### 2️⃣ Verificación de comprobantes recibidos (WSCDC)

Verifica facturas de proveedores **directo contra AFIP** — útil para detectar facturas truchas antes de pagar.

**Cómo se usa:**
1. Cargar factura del proveedor en Odoo
2. En tab "AFIP" cargar CAE/CAI/CAEA + modo
3. Click en **"Verificar en ARCA"**
4. Resultado: **A** (Aprobado), **O** (Observado), **R** (Rechazado)

**Modo company:**
- *No disponible*: botón oculto
- *Disponible*: validación opcional
- *Requerido*: bloquea post si AFIP devuelve R

---

### 3️⃣ Reportes fiscales

#### 📊 Libro IVA Digital RG 5616

Wizard que genera los **5 archivos TXT** oficiales que pide AFIP por mes, en un ZIP listo para subir.

- Vista SQL `account.ar.vat.line` (fuente única)
- Subdiario IVA Compras / Ventas en PDF + XLSX
- Vista interactiva con groupby por mes / partner / tipo cbte
- Coherencia exacta entre TXT, PDF y vista

#### 💼 IVA Simple (4 CSV ARCA)

Genera los 4 archivos CSV que ARCA pide en su portal de IVA Simple:

- DEBITO + REST_DEBITO (ventas)
- CREDITO + REST_CREDITO (compras)
- Encoding Latin-1, separador `;`, decimales con coma

#### 🔁 Cotejo Mis Comprobantes

Importa el XLS del portal "Mis Comprobantes" de ARCA y cruza contra Odoo.

| Estado | Significa |
|---|---|
| ✅ **OK** | Match exacto AFIP ↔ Odoo |
| 🟡 **Sólo AFIP** | Existe en AFIP, no en Odoo |
| 🔴 **Sólo Odoo** | Existe en Odoo, no en AFIP |
| 🟠 **Diff $** | Match con diferencia de importe |

**Bonus — auto-creación:** para los `Sólo AFIP` recibidos, click en **"Crear comprobantes seleccionados"** y el sistema arma el `account.move`:
- Partner por CUIT (busca o crea)
- IVA correcto por alícuota (parser per-bucket)
- Otros tributos como línea separada
- Monedas extranjeras con su `currency_id`

---

### 4️⃣ Padrones provinciales IIBB

Cuando emitís a un cliente con CUIT en padrón provincial, el sistema aplica automáticamente la percepción del % que dice el padrón.

| Jurisdicción | Módulo | Marco normativo | Estado |
|---|---|---|---|
| **BA — ARBA** | `l10n_ar_iibb_percepciones` | RG ARBA general | ✅ + WS auto |
| **CABA — AGIP** | `l10n_ar_padron_caba` | RG 296/2019, 352/2022 | ✅ |
| **Santa Fe — API** | `l10n_ar_padron_santafe` | RG API 14/2025 | ✅ |
| **Córdoba — Rentas** | `l10n_ar_padron_cordoba` | RN DGR 1/2023 | ✅ |

**🔧 El "magic":** los 4 padrones reusan los **templates oficiales** de l10n_ar Odoo 19 (`P. IIBB CABA 0%`, `P. IIBB SF 0%`, etc.) que vienen desactivados. Cuando el padrón requiere un % nuevo, el sistema clona el template con el nombre limpio `P. IIBB CABA 3.50%`.

**🤖 ARBA WS auto:** para Buenos Aires, hay descarga automática del padrón mensual vía web service del DFE de ARBA. Cron mensual el 1° del mes 09:00 + retry cada hora días 1-5 si falla. Switch on/off por empresa.

---

### 5️⃣ Funcionalidades transversales

#### 💱 Cotización USD/EUR diaria

Cron diario que actualiza la cotización de monedas extranjeras desde una fuente oficial.

**3 fuentes a elegir:**
1. **BNA Oficial (DolarApi)** ⭐ default — API JSON pública
2. **BNA scraping** — scrapea el HTML de www.bna.com.ar
3. **Dólar Mayorista** — cotización mayorista BNA

#### 📋 Errores AFIP humanizados

Catálogo de **60+ códigos** AFIP/ARCA traducidos a mensajes accionables.

**Antes:** `[10048] Error de cálculo`
**Después:**
> **[10048] ImpTotal mal calculado**
> → ImpTotal = ImpNeto + ImpIVA + ImpTrib + ImpOpEx + ImpTotConc. Probable diff por redondeo en el detalle de IVA o tributos.

Cubre WSAA, WSFEv1, WSFEX, WSCDC, CAEA. Cada hint dice **dónde revisar** y **qué acción tomar**.

#### 🔐 CSR Wizard

Para tramitar el certificado AFIP, el wizard genera RSA-2048 + CSR PEM:
- Configuración → "Generar solicitud de renovación"
- Llenás CN + razón social + CUIT
- Descargás el archivo `.csr` listo para AFIP
- La clave privada queda en Odoo (no hay que reimportar)

#### 🔎 Padrón A13 AFIP

Al cargar un partner por CUIT, autocompleta:
- Razón social
- Domicilio fiscal
- Condición frente a IVA
- Estado del CUIT

#### 📊 Dashboard logs WS

Configuración → Contabilidad → **Logs Web Services AFIP/ARCA**:
- Log CAEA (solicitudes, rendiciones)
- Log ARBA WS (descargas mensuales)
- Constataciones WSCDC

---

## 🛠️ Instalación

### Requisitos

| Componente | Versión |
|---|---|
| Odoo | 19.0 Community |
| Python | 3.10+ |
| Localización base | `l10n_ar` (incluida en Odoo Community) |
| Librerías Python | `zeep`, `cryptography`, `openpyxl`, `requests` |
| Certificado AFIP | X.509 + private key |

### Pasos rápidos

```bash
# 1. Clonar el repo
git clone https://github.com/trixocom/odoo-argentina-trx-ce.git \
    /opt/odoo/addons/argentina/

# 2. Agregar al odoo.conf
addons_path = /opt/odoo/addons,/opt/odoo/addons/argentina

# 3. Reiniciar Odoo y actualizar lista de aplicaciones
sudo systemctl restart odoo

# 4. En Odoo: Apps → buscar "trx_l10n_ar_edi" → Instalar
#    (las dependencias se instalan en cascada)

# 5. Subir certificado AFIP en Configuración → Certificados

# 6. Configurar empresa, journals, puntos de venta
```

---

## 📦 Estado de cada módulo

| Módulo | Versión | Estado |
|---|---|---|
| `trx_l10n_ar_edi_base` | 19.0.0.6.0 | ✅ Producción |
| `l10n_ar_afip_ws` | 19.0.0.8.1 | ✅ Producción |
| `trx_l10n_ar_edi` | 19.0.0.9.0 | ✅ Producción |
| `l10n_ar_padron_query` | 19.0.0.1.1 | ✅ Producción |
| `l10n_ar_libro_iva_digital` | 19.0.0.2.2 | ✅ Producción |
| `l10n_ar_iva_simple` | 19.0.0.2.0 | ✅ Producción |
| `trx_l10n_ar_pos_edi` | 19.0.0.3.0 | ✅ Producción |
| `l10n_ar_caea` | 19.0.2.1.1 | ✅ Producción |
| `l10n_ar_mis_comprobantes` | 19.0.2.0.3 | ✅ Producción |
| `l10n_ar_iibb_percepciones` | 19.0.1.1.3 | ✅ Producción + ARBA WS |
| `l10n_ar_padron_base` | 19.0.1.0.0 | ✅ Implementado |
| `l10n_ar_padron_caba` | 19.0.1.3.0 | ✅ Implementado |
| `l10n_ar_padron_santafe` | 19.0.1.3.0 | ✅ Implementado |
| `l10n_ar_padron_cordoba` | 19.0.1.3.0 | ✅ Implementado |

### ⚠️ Renombrado de módulos (septiembre 2026)

Tres módulos cambiaron de nombre técnico para no confundirse con el módulo
`l10n_ar_edi` de **Odoo Enterprise** (mismo nombre, producto distinto). Todo
módulo de Trixocom lleva el prefijo `trx_`:

| Nombre original | Nombre intermedio (mirror 08-09 → 27-09-2026) | Nombre definitivo |
|---|---|---|
| `l10n_ar_edi` | `l10n_ar_trx_edi` | `trx_l10n_ar_edi` |
| `l10n_ar_edi_base` | `l10n_ar_trx_edi_base` | `trx_l10n_ar_edi_base` |
| `l10n_ar_pos_edi` | `l10n_ar_trx_pos_edi` | `trx_l10n_ar_pos_edi` |

El resto de los módulos no cambia de nombre (sólo actualizan su `depends`).

**Instalación nueva:** no hay nada que hacer, instalar `trx_l10n_ar_edi`.

**Base que ya tenía instalado alguno de los nombres anteriores** (original o
intermedio): Odoo no renombra módulos instalados solo; hay que migrar la base
UNA vez, antes de arrancar con el código nuevo, porque si el directorio del
módulo desaparece del addons path Odoo arranca con el módulo "instalado pero
inexistente" y la facturación deja de funcionar. Los datos (facturas, CAE,
logs de WS, certificados) no se tocan: sólo cambian las referencias al nombre
del módulo.

1. Backup de la base y del filestore (es el rollback).
2. Detener Odoo (workers y cron de esa base).
3. Con el código **viejo** todavía en disco, correr el SQL de abajo:
   `psql -U <usuario> -d <base> -v ON_ERROR_STOP=1 -f rename_trx_modules.sql`
   (idempotente; cubre ambos orígenes; aborta si el `l10n_ar_edi` instalado
   no es de Trixocom, p. ej. el de Odoo Enterprise; el `SELECT` final debe
   devolver 0 filas).
4. Reemplazar el código por esta versión.
5. `odoo -d <base> -u trx_l10n_ar_edi_base --stop-after-init` (propaga a los
   dependientes) y arrancar normalmente.
6. Verificar: Apps muestra los tres módulos con el nombre nuevo, una factura
   con CAE conserva el tab AFIP y el PDF con QR, el POS emite un ticket.

<details>
<summary><code>rename_trx_modules.sql</code> (mismas tablas que <code>util.rename_module</code> de odoo/upgrade-util)</summary>

```sql
-- rename_trx_modules.sql — Trixocom
--
-- Renombra en una base Odoo 19 YA INSTALADA los módulos de Trixocom que
-- cambiaron de nombre técnico (sept. 2026), sin perder datos ni
-- configuración:
--
--     l10n_ar_edi           -> trx_l10n_ar_edi
--     l10n_ar_edi_base      -> trx_l10n_ar_edi_base
--     l10n_ar_pos_edi       -> trx_l10n_ar_pos_edi
--
-- y, para bases instaladas desde el mirror público entre el 08-09-2026 y
-- el 27-09-2026 (nombres intermedios):
--
--     l10n_ar_trx_edi       -> trx_l10n_ar_edi
--     l10n_ar_trx_edi_base  -> trx_l10n_ar_edi_base
--     l10n_ar_trx_pos_edi   -> trx_l10n_ar_pos_edi
--
-- Cubre las mismas tablas que `util.rename_module` de odoo/upgrade-util:
-- ir_module_module, ir_module_module_dependency, ir_model_data (registros
-- del módulo y el xmlid base.module_<nombre>) e ir_ui_view.key.
--
-- CÓMO USARLO (ver docs/migracion_rename_trx.md):
--   1. Backup de la base (y filestore).
--   2. Odoo DETENIDO (ningún worker corriendo contra esta base).
--   3. psql -d <base> -v ON_ERROR_STOP=1 -f rename_trx_modules.sql
--   4. Reemplazar el código por la versión con los nombres nuevos.
--   5. Arrancar Odoo con: -u trx_l10n_ar_edi_base  (propaga a dependientes).
--
-- Es idempotente: si los módulos ya están renombrados no hace nada.
-- Aborta si el `l10n_ar_edi` instalado NO es el de Trixocom (p. ej. el de
-- Odoo Enterprise), que no debe tocarse.

BEGIN;

DO $$
DECLARE
    pares  text[][] := ARRAY[
        ['l10n_ar_edi',          'trx_l10n_ar_edi'],
        ['l10n_ar_edi_base',     'trx_l10n_ar_edi_base'],
        ['l10n_ar_pos_edi',      'trx_l10n_ar_pos_edi'],
        ['l10n_ar_trx_edi',      'trx_l10n_ar_edi'],
        ['l10n_ar_trx_edi_base', 'trx_l10n_ar_edi_base'],
        ['l10n_ar_trx_pos_edi',  'trx_l10n_ar_pos_edi']
    ];
    viejo  text;
    nuevo  text;
    autor  text;
    estado text;
    n      int;
BEGIN
    FOR i IN 1 .. array_length(pares, 1) LOOP
        viejo := pares[i][1];
        nuevo := pares[i][2];

        SELECT author, state INTO autor, estado
          FROM ir_module_module WHERE name = viejo;

        IF NOT FOUND THEN
            RAISE NOTICE '[%] no existe en ir_module_module: nada que hacer', viejo;
            CONTINUE;
        END IF;

        IF estado <> 'installed' THEN
            -- No instalado: basta con borrar la fila vieja; Odoo recrea la
            -- nueva al actualizar la lista de apps.
            DELETE FROM ir_module_module_dependency
             WHERE module_id = (SELECT id FROM ir_module_module WHERE name = viejo);
            DELETE FROM ir_model_data
             WHERE module = 'base' AND model = 'ir.module.module' AND name = 'module_' || viejo;
            DELETE FROM ir_module_module WHERE name = viejo;
            RAISE NOTICE '[%] estaba en estado % (no instalado): fila eliminada', viejo, estado;
            CONTINUE;
        END IF;

        IF coalesce(autor, '') NOT ILIKE '%trixocom%' THEN
            RAISE EXCEPTION '[%] está instalado pero su autor es "%", no Trixocom. '
                            'Puede ser el módulo de Odoo Enterprise: NO se renombra. Abortando.',
                            viejo, autor;
        END IF;

        -- Si alguien actualizó la lista de apps con el código nuevo ya en
        -- disco, existe una fila "uninstalled" con el nombre nuevo: se
        -- elimina para no violar el unique(name).
        IF EXISTS (SELECT 1 FROM ir_module_module WHERE name = nuevo) THEN
            IF EXISTS (SELECT 1 FROM ir_module_module WHERE name = nuevo AND state <> 'uninstalled') THEN
                RAISE EXCEPTION '[%] ya existe con estado distinto de uninstalled. Revisar a mano.', nuevo;
            END IF;
            DELETE FROM ir_module_module_dependency
             WHERE module_id = (SELECT id FROM ir_module_module WHERE name = nuevo);
            DELETE FROM ir_model_data
             WHERE module = 'base' AND model = 'ir.module.module' AND name = 'module_' || nuevo;
            DELETE FROM ir_module_module WHERE name = nuevo;
            RAISE NOTICE '[%] fila "uninstalled" previa eliminada', nuevo;
        END IF;

        UPDATE ir_module_module SET name = nuevo WHERE name = viejo;

        UPDATE ir_module_module_dependency SET name = nuevo WHERE name = viejo;
        GET DIAGNOSTICS n = ROW_COUNT;
        RAISE NOTICE '[% -> %] dependencias actualizadas: %', viejo, nuevo, n;

        UPDATE ir_model_data SET module = nuevo WHERE module = viejo;
        GET DIAGNOSTICS n = ROW_COUNT;
        RAISE NOTICE '[% -> %] ir_model_data actualizados: %', viejo, nuevo, n;

        UPDATE ir_model_data SET name = 'module_' || nuevo
         WHERE module = 'base' AND model = 'ir.module.module' AND name = 'module_' || viejo;

        UPDATE ir_ui_view
           SET key = nuevo || substr(key, length(viejo) + 1)
         WHERE key LIKE replace(viejo, '_', '\_') || '.%';
        GET DIAGNOSTICS n = ROW_COUNT;
        RAISE NOTICE '[% -> %] ir_ui_view.key actualizados: %', viejo, nuevo, n;
    END LOOP;
END $$;

-- Verificación: no debe quedar ninguna referencia a los nombres viejos.
SELECT 'ir_module_module' AS tabla, name AS ref FROM ir_module_module
 WHERE name IN ('l10n_ar_edi', 'l10n_ar_edi_base', 'l10n_ar_pos_edi', 'l10n_ar_trx_edi', 'l10n_ar_trx_edi_base', 'l10n_ar_trx_pos_edi')
UNION ALL
SELECT 'ir_module_module_dependency', name FROM ir_module_module_dependency
 WHERE name IN ('l10n_ar_edi', 'l10n_ar_edi_base', 'l10n_ar_pos_edi', 'l10n_ar_trx_edi', 'l10n_ar_trx_edi_base', 'l10n_ar_trx_pos_edi')
UNION ALL
SELECT 'ir_model_data', module || '.' || name FROM ir_model_data
 WHERE module IN ('l10n_ar_edi', 'l10n_ar_edi_base', 'l10n_ar_pos_edi', 'l10n_ar_trx_edi', 'l10n_ar_trx_edi_base', 'l10n_ar_trx_pos_edi')
UNION ALL
SELECT 'ir_ui_view', key FROM ir_ui_view
 WHERE key LIKE 'l10n\_ar\_edi.%' OR key LIKE 'l10n\_ar\_edi\_base.%' OR key LIKE 'l10n\_ar\_pos\_edi.%' OR key LIKE 'l10n\_ar\_trx\_edi.%' OR key LIKE 'l10n\_ar\_trx\_edi\_base.%' OR key LIKE 'l10n\_ar\_trx\_pos\_edi.%';

COMMIT;
```

</details>

### Backlog (sin demanda actual)

- ⚪ **WSBFE** — Bono Fiscal Electrónico
- ⚪ **WSMTXCA** — Factura con detalle de ítems
- ⚪ **A122R** — Submission de retenciones IIBB BA (REST con token)
- ⚪ **SIRADIG / SIRCREB** — Reportes nacionales

---

## 📚 Documentación

- 📄 [`docs/l10n_ar_trixocom.md`](docs/l10n_ar_trixocom.md) — documentación técnica completa del paquete
- 📄 [`docs/l10n_ar_trixocom.html`](docs/l10n_ar_trixocom.html) — la misma, en versión visual (abrir en navegador)

---

## 📜 Licencia

LGPL-3 · Compatible con Odoo Community

## 👤 Autor

**Trixocom** · 2026

## 🔗 Repo

- 🌐 [github.com/trixocom/odoo-argentina-trx-ce](https://github.com/trixocom/odoo-argentina-trx-ce)
