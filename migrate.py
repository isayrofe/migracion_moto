import os
import re
import uuid
import json
import hashlib
import unicodedata
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

def clean_text(value):
    if value is None:
        return None

    if pd.isna(value):
        return None

    text = str(value).strip().upper()
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip()

    if text in ("", "#N/D", "N/D", "NA", "S/N", "SIN DATO"):
        return None

    return text

def clean_optional(value):
    if value is None or pd.isna(value):
        return None

    text = str(value).strip()
    if text.upper() in ("", "#N/D", "N/D", "NA"):
        return None

    return text

def remove_accents(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")

def normalize_key(value):
    text = clean_text(value)

    if not text:
        return None

    text = remove_accents(text)
    text = re.sub(r"\s*[-–—]\s*", " - ", text)
    text = text.replace(".", "")

    replacements = {
        "ORGNIZACION": "ORGANIZACION",
        "ORGANIZACON": "ORGANIZACION",
        "ORGANIZACIÓN": "ORGANIZACION",
        "MOTOTAXISTAS": "MOTOTAXIS",
        "SECCIÓN": "SECCION",
        "MÁRTIRES": "MARTIRES",
        "MAÍZ": "MAIZ",
    }

    for wrong, right in replacements.items():
        text = text.replace(remove_accents(wrong), remove_accents(right))

    text = re.sub(r"\s+", " ", text).strip()

    return text

def clean_serie(value):
    """
    Devuelve la serie limpia si parece una serie real.
    Devuelve None si el valor representa ausencia de serie/documento.
    """

    raw = clean_text(value)

    if not raw:
        return None

    frase_key = normalize_key(raw)

    if not frase_key:
        return None

    # Versión compacta para detectar variantes pegadas:
    # SIN FACTURA -> SINFACTURA
    # SIN  FACTURA -> SINFACTURA
    compact_key = re.sub(r"[^A-Z0-9]", "", frase_key)

    invalid_exact = {
        "FALTA NUMERO DE SERIE",
        "FALTA NÚMERO DE SERIE",
        "SIN NUMERO DE SERIE",
        "SIN NÚMERO DE SERIE",
        "SIN NUEMERO DE SERIE",
        "SIN SERIE",
        "SIN FACTURA",
        "SIN FATURA",
        "SIN COPIA",
        "NO PRESENTO FACTURA",
        "NO PRESENTÓ FACTURA",
        "NO SE APRECIA LA SERIE",
        "FACTURA REPETIDA",
        "SIN FOLIO",
        "SINFOLIO",
        "S/N",
        "SN",
        "#N/D",
        "N/D",
        "NA",
        "SIN DATO",
    }

    invalid_compact = {
        re.sub(r"[^A-Z0-9]", "", normalize_key(v))
        for v in invalid_exact
        if normalize_key(v)
    }

    # Si coincide con una frase inválida exacta o compacta
    if frase_key in {normalize_key(v) for v in invalid_exact if normalize_key(v)}:
        return None

    if compact_key in invalid_compact:
        return None

    # Si contiene frases inválidas
    contains_invalid = [
        "SINFACTURA",
        "SINFATURA",
        "SINNUMERODESERIE",
        "SINNUEMERODESERIE",
        "SINSERIE",
        "SINCOPIA",
        "FACTURAREPETIDA",
        "NOPRESENTOFACTURA",
        "NOSEAPRECIALASERIE",
    ]

    for invalid in contains_invalid:
        if invalid in compact_key:
            return None

    # Limpieza final de la serie real
    serie = compact_key

    # Rechazar valores demasiado cortos: MD6, MD2A25, etc.
    if len(serie) < 8:
        return None

    return serie

def is_fallecido(value):
    """
    En el CSV, una persona está fallecida solo si FALLECIDO = 'F'.
    """
    text = clean_text(value)

    if not text:
        return False

    return text == "F"

def split_sitio_org(raw_value):
    display = clean_text(raw_value)
    key = normalize_key(raw_value)

    if not key:
        return {
            "org_display": None,
            "sitio_display": None,
            "org_key": None,
            "sitio_key": None,
        }

    if " - " in key:
        key_parts = key.split(" - ", 1)
        org_key = key_parts[0].strip()
        sitio_key = key_parts[1].strip()

        display_parts = re.split(r"\s*[-–—]\s*", display, maxsplit=1)
        org_display = display_parts[0].strip()
        sitio_display = display_parts[1].strip() if len(display_parts) > 1 else sitio_key

        return {
            "org_display": org_display,
            "sitio_display": sitio_display,
            "org_key": org_key,
            "sitio_key": sitio_key,
        }

    return {
        "org_display": display,
        "sitio_display": display,
        "org_key": key,
        "sitio_key": key,
    }

def stable_key(prefix, value):
    raw = normalize_key(value) or str(value)
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()
    return f"{prefix}_{digest}"

def normalize_sn(text):
    if not text:
        return None

    text = clean_text(text)
    if not text:
        return None

    text = re.sub(r"\bS\s*/?\s*N\b", "S/N", text)
    text = re.sub(r"\bSN\b", "S/N", text)
    text = re.sub(r"\bS\.N\.\b", "S/N", text)
    return text

def parse_address(raw_address):
    """
    Regresa:
    {
        calle,
        numero_exterior,
        numero_interior,
        referencia,
        direccion_original
    }
    """

    original = clean_optional(raw_address)
    address = normalize_sn(raw_address)

    if not address:
        return {
            "calle": None,
            "numero_exterior": None,
            "numero_interior": None,
            "referencia": None,
            "direccion_original": original,
        }

    referencia = None
    numero_interior = None
    numero_exterior = None
    calle = address

    # Referencias típicas
    ref_match = re.search(
        r"\b(ESQ\.?|ESQUINA|JUNTO|FRENTE|POSTE|LOCAL|LOTE|MZA|MZNA|LT|LTE|MANZANA|CASA|DEPTO|DPTO)\b.*$",
        address,
    )
    if ref_match:
        referencia = ref_match.group(0).strip()

    # Interior
    int_match = re.search(r"\b(INT\.?|INTERIOR|DEPTO|DPTO)\s*\.?\s*([A-Z0-9/-]+)?", address)
    if int_match:
        numero_interior = int_match.group(2)

    # Número con NO. / NUM. / #
    no_match = re.search(r"\b(NO\.?|NUM\.?|NÚM\.?|#)\s*([0-9]+)\s*([A-Z])?\b", address)
    if no_match:
        try:
            numero_exterior = int(no_match.group(2))
        except ValueError:
            numero_exterior = None
            
        if no_match.group(3):
            numero_interior = no_match.group(3)

        calle = re.sub(r"\s*\b(NO\.?|NUM\.?|NÚM\.?|#)\s*[0-9]+[A-Z]?.*$", "", address).strip()
        return {
            "calle": calle or None,
            "numero_exterior": numero_exterior,
            "numero_interior": numero_interior,
            "referencia": referencia,
            "direccion_original": original,
        }

    # S/N
    if "S/N" in address:
        calle = re.sub(r"\s*\bS/N\b.*$", "", address).strip()
        after_sn = re.sub(r"^.*\bS/N\b\s*", "", address).strip()
        if after_sn and after_sn != address:
            referencia = referencia or after_sn

        return {
            "calle": calle or address,
            "numero_exterior": None,
            "numero_interior": numero_interior,
            "referencia": referencia,
            "direccion_original": original,
        }

    # Número al final: VICENTE GUERRERO 140 / CONSTITUCION 10 A / CALLEJON 38B
    end_match = re.search(r"\s+([0-9]+)([A-Z])?\s*$", address)
    if end_match:
        try:
            numero_exterior = int(end_match.group(1))
        except ValueError:
            numero_exterior = None
            
        if end_match.group(2):
            numero_interior = end_match.group(2)

        calle = re.sub(r"\s+[0-9]+[A-Z]?\s*$", "", address).strip()

    return {
        "calle": calle or None,
        "numero_exterior": numero_exterior,
        "numero_interior": numero_interior,
        "referencia": referencia,
        "direccion_original": original,
    }

def normalize_municipio(value):
    text = clean_text(value)
    if not text:
        return None

    # JUCHITAN, OAX -> JUCHITAN
    return re.sub(r",.*$", "", text).strip()

def valid_uuid(value):
    try:
        if not value:
            return None
        return str(uuid.UUID(str(value).strip()))
    except Exception:
        return None

def connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )

def fetch_one_id(cur, query, params):
    cur.execute(query, params)
    row = cur.fetchone()
    return row["id"] if row else None

def insert_rechazo(cur, row_number, tabla, motivo, data):
    cur.execute(
        """
        INSERT INTO migracion.rechazos_csv (
            row_number,
            tabla_destino,
            motivo,
            data
        )
        VALUES (%s, %s, %s, %s::jsonb)
        """,
        (
            row_number,
            tabla,
            motivo,
            json.dumps(data, ensure_ascii=False),
        ),
    )

def apply_schema_adjustments(cur):
    """
    Ajustes permitidos para la migración.
    - No agrega columnas nuevas a direccion.
    - Agrega uuid y folio_carpeta a vehiculo.
    - Crea tabla historial vehiculo_propietario.
    """
    print("Aplicando ajustes de esquema...")

    cur.execute("ALTER TABLE public.persona ALTER COLUMN curp DROP NOT NULL;")
    cur.execute("ALTER TABLE public.vehiculo ALTER COLUMN modelo DROP NOT NULL;")
    cur.execute("ALTER TABLE public.vehiculo ALTER COLUMN numero_motor DROP NOT NULL;")
    cur.execute("ALTER TABLE public.vehiculo ALTER COLUMN color DROP NOT NULL;")
    cur.execute('ALTER TABLE public.direccion ALTER COLUMN "numeroExterior" DROP NOT NULL;')

    cur.execute('ALTER TABLE public.vehiculo DROP CONSTRAINT IF EXISTS "UQ_0ded33db9dbb4b8690ef92059ca";')

    cur.execute('ALTER TABLE public.vehiculo ADD COLUMN IF NOT EXISTS uuid uuid;')
    cur.execute('ALTER TABLE public.vehiculo ADD COLUMN IF NOT EXISTS folio_carpeta varchar(255);')

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS public.vehiculo_propietario (
            id serial PRIMARY KEY,
            "vehiculoId" int NOT NULL REFERENCES public.vehiculo(id),
            "propietarioId" int NOT NULL REFERENCES public.propietario(id),
            "isActive" bool DEFAULT true NOT NULL,
            "createdAt" timestamp DEFAULT now() NOT NULL,
            "updatedAt" timestamp DEFAULT now() NOT NULL,
            "deletedAt" timestamp NULL
        );
        """
    )

    print("Ajustes de esquema aplicados correctamente.")

def setup_migration_tables(cur):
    cur.execute("CREATE SCHEMA IF NOT EXISTS migracion;")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS migracion.rechazos_csv (
            id bigserial PRIMARY KEY,
            row_number bigint,
            tabla_destino text,
            motivo text,
            data jsonb,
            created_at timestamp DEFAULT now()
        );
        """
    )

def get_or_create_municipio(cur, municipio):
    if not municipio:
        return None

    existing_id = fetch_one_id(
        cur,
        """
        SELECT id
        FROM public.municipio
        WHERE upper(trim(nombre)) = %s
          AND "deletedAt" IS NULL
        LIMIT 1
        """,
        (municipio,),
    )

    if existing_id:
        return existing_id

    cur.execute(
        """
        INSERT INTO public.municipio (
            "createdAt",
            "updatedAt",
            "isActive",
            nombre,
            "cotranId"
        )
        VALUES (now(), now(), true, %s, %s)
        RETURNING id
        """,
        (municipio, stable_key("CSV_MUN", municipio)),
    )

    return cur.fetchone()["id"]

def get_or_create_localidad(cur, municipio_id, nombre):
    if not municipio_id:
        return None

    localidad = nombre or "SIN LOCALIDAD"

    existing_id = fetch_one_id(
        cur,
        """
        SELECT id
        FROM public.localidad
        WHERE upper(trim(nombre)) = %s
          AND "municipioId" = %s
          AND "deletedAt" IS NULL
        LIMIT 1
        """,
        (localidad, municipio_id),
    )

    if existing_id:
        return existing_id

    cur.execute(
        """
        INSERT INTO public.localidad (
            "createdAt",
            "updatedAt",
            "isActive",
            nombre,
            "municipioId",
            "cotranId"
        )
        VALUES (now(), now(), true, %s, %s, %s)
        RETURNING id
        """,
        (localidad, municipio_id, stable_key("CSV_LOC", f"{municipio_id}_{localidad}")),
    )

    return cur.fetchone()["id"]

def get_or_create_codigo_postal(cur, cp, municipio_id):
    cp = clean_optional(cp)

    if not cp:
        return None

    cp = str(cp).strip()

    if not re.match(r"^[0-9]{5}$", cp):
        return None

    existing_id = fetch_one_id(
        cur,
        """
        SELECT id
        FROM public.codigo_postal
        WHERE "codigoPostal" = %s
          AND "municipioId" = %s
          AND "deletedAt" IS NULL
        LIMIT 1
        """,
        (cp, municipio_id),
    )

    if existing_id:
        return existing_id

    cur.execute(
        """
        INSERT INTO public.codigo_postal (
            "createdAt",
            "updatedAt",
            "isActive",
            "codigoPostal",
            "municipioId"
        )
        VALUES (now(), now(), true, %s, %s)
        RETURNING id
        """,
        (cp, municipio_id),
    )

    return cur.fetchone()["id"]

def get_or_create_colonia(cur, colonia, municipio_id):
    colonia = clean_text(colonia)

    if not colonia or not municipio_id:
        return None

    existing_id = fetch_one_id(
        cur,
        """
        SELECT id
        FROM public.colonia
        WHERE upper(trim(nombre)) = %s
          AND "municipioId" = %s
          AND "deletedAt" IS NULL
        LIMIT 1
        """,
        (colonia, municipio_id),
    )

    if existing_id:
        return existing_id

    tipo = "COLONIA"
    if "SECCION" in colonia or "SECCIÓN" in colonia:
        tipo = "SECCION"

    cur.execute(
        """
        INSERT INTO public.colonia (
            "createdAt",
            "updatedAt",
            "isActive",
            nombre,
            tipo_asentamiento,
            "municipioId"
        )
        VALUES (now(), now(), true, %s, %s, %s)
        RETURNING id
        """,
        (colonia, tipo, municipio_id),
    )

    return cur.fetchone()["id"]

def load_organizacion_cache(cur):
    cur.execute(
        """
        SELECT id, nombre
        FROM public.organizacion
        WHERE "deletedAt" IS NULL
        """
    )

    cache = {}

    for row in cur.fetchall():
        key = normalize_key(row["nombre"])
        if key and key not in cache:
            cache[key] = row["id"]

    return cache

def load_sitio_cache(cur):
    cur.execute(
        """
        SELECT id, nombre_sitio, "localidadId"
        FROM public.sitio
        WHERE "deletedAt" IS NULL
        """
    )

    cache = {}

    for row in cur.fetchall():
        key = normalize_key(row["nombre_sitio"])
        localidad_id = row["localidadId"] or 0

        if key:
            cache[(localidad_id, key)] = row["id"]

    return cache

def get_or_create_organizacion_y_sitio(
    cur,
    raw_sitio_org,
    localidad_id,
    organizacion_cache,
    sitio_cache
):
    data = split_sitio_org(raw_sitio_org)

    if not data["org_key"]:
        return None, None

    org_key = data["org_key"]
    sitio_key = data["sitio_key"]

    org_display = data["org_display"]
    sitio_display = data["sitio_display"]

    if org_key in organizacion_cache:
        organizacion_id = organizacion_cache[org_key]
    else:
        cur.execute(
            """
            INSERT INTO public.organizacion (
                "createdAt",
                "updatedAt",
                "isActive",
                nombre
            )
            VALUES (now(), now(), true, %s)
            RETURNING id
            """,
            (org_display,)
        )

        organizacion_id = cur.fetchone()["id"]
        organizacion_cache[org_key] = organizacion_id

    localidad_key = localidad_id or 0
    sitio_cache_key = (localidad_key, sitio_key)

    if sitio_cache_key in sitio_cache:
        sitio_id = sitio_cache[sitio_cache_key]
    else:
        cur.execute(
            """
            INSERT INTO public.sitio (
                "createdAt",
                "updatedAt",
                "isActive",
                nombre_sitio,
                "localidadId"
            )
            VALUES (now(), now(), true, %s, %s)
            RETURNING id
            """,
            (sitio_display, localidad_id)
        )

        sitio_id = cur.fetchone()["id"]
        sitio_cache[sitio_cache_key] = sitio_id

    return organizacion_id, sitio_id

def create_direccion(cur, row):
    parsed = parse_address(row.get("CALLE Y NÚMERO"))

    if not parsed["calle"]:
        return None

    municipio = normalize_municipio(row.get("MUNICIPIO"))
    municipio_id = get_or_create_municipio(cur, municipio)
    localidad_id = get_or_create_localidad(cur, municipio_id, municipio)
    cp_id = get_or_create_codigo_postal(cur, row.get("CODIGO POSTAL"), municipio_id)
    colonia_id = get_or_create_colonia(cur, row.get("COLONIA"), municipio_id)

    cur.execute(
        """
        INSERT INTO public.direccion (
            "createdAt",
            "updatedAt",
            "isActive",
            calle,
            "numeroExterior",
            "numeroInterior",
            "codigoPostalId",
            "localidadId",
            "coloniaId"
        )
        VALUES (
            now(), now(), true,
            %s, %s, %s, %s, %s, %s
        )
        RETURNING id
        """,
        (
            parsed["calle"],
            parsed["numero_exterior"],
            parsed["numero_interior"],
            cp_id,
            localidad_id,
            colonia_id,
        ),
    )

    return cur.fetchone()["id"]

def load_persona_cache(cur):
    cur.execute(
        """
        SELECT id, nombre, "isActive"
        FROM public.persona
        WHERE "deletedAt" IS NULL
        """
    )

    cache = {}

    for row in cur.fetchall():
        nombre_key = normalize_key(row["nombre"])
        activo = bool(row["isActive"])

        if nombre_key:
            cache[(nombre_key, activo)] = row["id"]

    return cache

def get_or_create_persona(cur, nombre, direccion_id=None, activo=True, persona_cache=None):
    nombre = clean_text(nombre)

    if not nombre:
        return None

    nombre_key = normalize_key(nombre)
    cache_key = (nombre_key, bool(activo))

    if persona_cache is not None and cache_key in persona_cache:
        persona_id = persona_cache[cache_key]

        if direccion_id:
            cur.execute(
                """
                UPDATE public.persona
                SET
                    "direccionId" = COALESCE("direccionId", %s),
                    "updatedAt" = now()
                WHERE id = %s
                  AND "deletedAt" IS NULL
                """,
                (direccion_id, persona_id),
            )

        return persona_id

    existing_id = fetch_one_id(
        cur,
        """
        SELECT id
        FROM public.persona
        WHERE upper(trim(nombre)) = %s
          AND "isActive" = %s
          AND "deletedAt" IS NULL
        LIMIT 1
        """,
        (nombre, activo),
    )

    if existing_id:
        if persona_cache is not None:
            persona_cache[cache_key] = existing_id

        return existing_id

    cur.execute(
        """
        INSERT INTO public.persona (
            "createdAt",
            "updatedAt",
            "isActive",
            nombre,
            curp,
            "direccionId"
        )
        VALUES (now(), now(), %s, %s, NULL, %s)
        RETURNING id
        """,
        (activo, nombre, direccion_id),
    )

    persona_id = cur.fetchone()["id"]

    if persona_cache is not None:
        persona_cache[cache_key] = persona_id

    return persona_id

def get_or_create_propietario(cur, persona_id, organizacion_id=None):
    if not persona_id:
        return None

    existing_id = fetch_one_id(
        cur,
        """
        SELECT id
        FROM public.propietario
        WHERE "personaId" = %s
          AND COALESCE("organizacionId", 0) = COALESCE(%s, 0)
          AND "deletedAt" IS NULL
        LIMIT 1
        """,
        (persona_id, organizacion_id),
    )

    if existing_id:
        return existing_id

    cur.execute(
        """
        INSERT INTO public.propietario (
            "createdAt",
            "updatedAt",
            "isActive",
            "personaId",
            "organizacionId"
        )
        VALUES (now(), now(), true, %s, %s)
        RETURNING id
        """,
        (persona_id, organizacion_id),
    )

    return cur.fetchone()["id"]

def get_or_create_chofer(cur, nombre):
    nombre = clean_text(nombre)

    if not nombre:
        return None

    if nombre in ("S/N", "#N/D"):
        return None

    # Como no hay CURP, se busca por nombre.
    persona_id = get_or_create_persona(
        cur,
        nombre=nombre,
        direccion_id=None,
        activo=True
    )

    if not persona_id:
        return None

    existing_id = fetch_one_id(
        cur,
        """
        SELECT id
        FROM public.chofer
        WHERE "personaId" = %s
          AND "deletedAt" IS NULL
        LIMIT 1
        """,
        (persona_id,),
    )

    if existing_id:
        return existing_id

    cur.execute(
        """
        INSERT INTO public.chofer (
            "createdAt",
            "updatedAt",
            "isActive",
            "personaId"
        )
        VALUES (now(), now(), true, %s)
        RETURNING id
        """,
        (persona_id,),
    )

    return cur.fetchone()["id"]

def vehiculo_exists_by_uuid(cur, vehiculo_uuid):
    if not vehiculo_uuid:
        return None

    return fetch_one_id(
        cur,
        """
        SELECT id
        FROM public.vehiculo
        WHERE uuid = %s::uuid
          AND "deletedAt" IS NULL
        LIMIT 1
        """,
        (vehiculo_uuid,),
    )

def find_vehiculo_by_serie(cur, serie):
    if not serie:
        return None

    cur.execute(
        """
        SELECT
            id,
            uuid,
            numero_serie,
            "propietarioId"
        FROM public.vehiculo
        WHERE upper(trim(numero_serie)) = %s
          AND "deletedAt" IS NULL
        LIMIT 1
        """,
        (serie,),
    )

    return cur.fetchone()

def create_vehiculo(cur, row, propietario_id, sitio_id):
    serie = clean_serie(row.get("NÚMERO DE SERIE DE LA UNIDAD"))
    vehiculo_uuid = valid_uuid(clean_optional(row.get("UUID")))
    folio_carpeta = clean_optional(row.get("FOLIO DE CARPETA"))

    if not serie:
        return None

    if not vehiculo_uuid:
        return None

    cur.execute(
        """
        INSERT INTO public.vehiculo (
            "createdAt",
            "updatedAt",
            "isActive",
            uuid,
            folio_carpeta,
            modelo,
            numero_serie,
            numero_motor,
            color,
            "sitioId",
            "propietarioId"
        )
        VALUES (
            now(), now(), true,
            %s::uuid, %s,
            NULL, %s, NULL, NULL, %s, %s
        )
        RETURNING id
        """,
        (
            vehiculo_uuid,
            folio_carpeta,
            serie,
            sitio_id,
            propietario_id,
        ),
    )

    return cur.fetchone()["id"]

def get_active_propietario_vehiculo(cur, vehiculo_id):
    cur.execute(
        """
        SELECT id, "propietarioId"
        FROM public.vehiculo_propietario
        WHERE "vehiculoId" = %s
          AND "isActive" = true
          AND "deletedAt" IS NULL
        LIMIT 1
        """,
        (vehiculo_id,),
    )

    return cur.fetchone()

def baja_propietarios_anteriores(cur, vehiculo_id):
    cur.execute(
        """
        UPDATE public.vehiculo_propietario
        SET
            "isActive" = false,
            "updatedAt" = now()
        WHERE "vehiculoId" = %s
          AND "isActive" = true
          AND "deletedAt" IS NULL
        """,
        (vehiculo_id,),
    )

def link_vehiculo_propietario(cur, vehiculo_id, propietario_id):
    if not vehiculo_id or not propietario_id:
        return None

    active_link = get_active_propietario_vehiculo(cur, vehiculo_id)

    if active_link and active_link["propietarioId"] == propietario_id:
        return active_link["id"]

    baja_propietarios_anteriores(cur, vehiculo_id)

    existing_id = fetch_one_id(
        cur,
        """
        SELECT id
        FROM public.vehiculo_propietario
        WHERE "vehiculoId" = %s
          AND "propietarioId" = %s
          AND "deletedAt" IS NULL
        LIMIT 1
        """,
        (vehiculo_id, propietario_id),
    )

    if existing_id:
        cur.execute(
            """
            UPDATE public.vehiculo_propietario
            SET
                "isActive" = true,
                "updatedAt" = now()
            WHERE id = %s
            """,
            (existing_id,),
        )
        return existing_id

    cur.execute(
        """
        INSERT INTO public.vehiculo_propietario (
            "vehiculoId",
            "propietarioId",
            "isActive",
            "createdAt",
            "updatedAt"
        )
        VALUES (%s, %s, true, now(), now())
        RETURNING id
        """,
        (vehiculo_id, propietario_id),
    )

    return cur.fetchone()["id"]

def link_vehiculo_chofer(cur, vehiculo_id, chofer_id):
    if not vehiculo_id or not chofer_id:
        return

    cur.execute(
        """
        INSERT INTO public.vehiculo_chofer (
            "vehiculoId",
            "choferId"
        )
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        (vehiculo_id, chofer_id),
    )

def migrate():
    csv_path = os.getenv("CSV_PATH")
    separator = os.getenv("CSV_SEPARATOR", ",")

    if separator == "\\t":
        separator = "\t"

    if not os.path.exists(csv_path):
        print(f"Error: No se encontró el archivo CSV en {csv_path}")
        return

    try:
        df = pd.read_csv(csv_path, sep=separator, dtype=str, keep_default_na=False, encoding='utf-8')
    except UnicodeDecodeError:
        try:
            df = pd.read_csv(csv_path, sep=separator, dtype=str, keep_default_na=False, encoding='latin1')
        except UnicodeDecodeError:
            df = pd.read_csv(csv_path, sep=separator, dtype=str, keep_default_na=False, encoding='cp1252')

    conn = connect()
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            apply_schema_adjustments(cur)
            setup_migration_tables(cur)

            organizacion_cache = load_organizacion_cache(cur)
            sitio_cache = load_sitio_cache(cur)
            persona_cache = load_persona_cache(cur)

            total = len(df)
            migrated = 0
            rejected = 0

            print(f"Cachés cargadas. Procesando {total} filas...")

            for index, row in df.iterrows():
                row_number = index + 1
                data = row.to_dict()

                try:
                    cur.execute(f"SAVEPOINT row_{index}")

                    nombre_propietario = clean_text(row.get("NOMBRE DEL PROPIETARIO"))

                    if not nombre_propietario:
                        insert_rechazo(
                            cur,
                            row_number,
                            "persona",
                            "Propietario vacío",
                            data,
                        )
                        rejected += 1
                        cur.execute(f"RELEASE SAVEPOINT row_{index}")
                        continue

                    serie = clean_serie(row.get("NÚMERO DE SERIE DE LA UNIDAD"))

                    if not serie:
                        insert_rechazo(
                            cur,
                            row_number,
                            "vehiculo",
                            "Número de serie vacío o inválido",
                            data,
                        )
                        rejected += 1
                        cur.execute(f"RELEASE SAVEPOINT row_{index}")
                        continue

                    vehiculo_uuid = valid_uuid(clean_optional(row.get("UUID")))

                    if not vehiculo_uuid:
                        insert_rechazo(
                            cur,
                            row_number,
                            "vehiculo",
                            "UUID de vehículo vacío o inválido",
                            data,
                        )
                        rejected += 1
                        cur.execute(f"RELEASE SAVEPOINT row_{index}")
                        continue

                    direccion_id = create_direccion(cur, row)

                    fallecido_flag = is_fallecido(row.get("FALLECIDO"))
                    activo = not fallecido_flag

                    municipio = normalize_municipio(row.get("MUNICIPIO"))
                    municipio_id = get_or_create_municipio(cur, municipio)
                    localidad_id = get_or_create_localidad(cur, municipio_id, municipio)

                    organizacion_id, sitio_id = get_or_create_organizacion_y_sitio(
                        cur,
                        row.get("SITIO-ORG"),
                        localidad_id,
                        organizacion_cache,
                        sitio_cache
                    )

                    persona_id = get_or_create_persona(
                        cur,
                        nombre=nombre_propietario,
                        direccion_id=direccion_id,
                        activo=activo,
                        persona_cache=persona_cache,
                    )

                    propietario_id = get_or_create_propietario(
                        cur,
                        persona_id,
                        organizacion_id=organizacion_id,
                    )

                    existing_vehiculo_id = vehiculo_exists_by_uuid(cur, vehiculo_uuid)

                    if existing_vehiculo_id:
                        vehiculo_id = existing_vehiculo_id

                        cur.execute(
                            """
                            UPDATE public.vehiculo
                            SET
                                folio_carpeta = COALESCE(folio_carpeta, %s),
                                numero_serie = COALESCE(numero_serie, %s),
                                "sitioId" = COALESCE("sitioId", %s),
                                "propietarioId" = %s,
                                "updatedAt" = now()
                            WHERE id = %s
                              AND "deletedAt" IS NULL
                            """,
                            (
                                clean_optional(row.get("FOLIO DE CARPETA")),
                                serie,
                                sitio_id,
                                propietario_id,
                                vehiculo_id,
                            ),
                        )

                    else:
                        vehiculo_por_serie = find_vehiculo_by_serie(cur, serie)

                        if vehiculo_por_serie and str(vehiculo_por_serie["uuid"]) != str(vehiculo_uuid):
                            insert_rechazo(
                                cur,
                                row_number,
                                "vehiculo",
                                f"Serie ya existe con otro UUID, revisar manualmente vehiculo_id={vehiculo_por_serie['id']}",
                                data,
                            )
                            rejected += 1
                            cur.execute(f"RELEASE SAVEPOINT row_{index}")
                            continue

                        vehiculo_id = create_vehiculo(
                            cur,
                            row,
                            propietario_id=propietario_id,
                            sitio_id=sitio_id,
                        )

                    link_vehiculo_propietario(
                        cur,
                        vehiculo_id=vehiculo_id,
                        propietario_id=propietario_id,
                    )

                    chofer_1_id = get_or_create_chofer(cur, row.get("CHOFER 1"))
                    chofer_2_id = get_or_create_chofer(cur, row.get("CHOFER 2"))

                    link_vehiculo_chofer(cur, vehiculo_id, chofer_1_id)
                    link_vehiculo_chofer(cur, vehiculo_id, chofer_2_id)

                    migrated += 1
                    cur.execute(f"RELEASE SAVEPOINT row_{index}")

                    if (index + 1) % 100 == 0:
                        print(f"Migrados: {index + 1}/{total}")

                except Exception as row_error:
                    cur.execute(f"ROLLBACK TO SAVEPOINT row_{index}")
                    insert_rechazo(
                        cur,
                        row_number,
                        "general",
                        str(row_error),
                        data,
                    )
                    rejected += 1
                    cur.execute(f"RELEASE SAVEPOINT row_{index}")

            conn.commit()

            print("Migración terminada")
            print(f"Total CSV: {total}")
            print(f"Migrados: {migrated}")
            print(f"Rechazados: {rejected}")

    except Exception as e:
        conn.rollback()
        print("Error general. Se hizo rollback.")
        raise e

    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
