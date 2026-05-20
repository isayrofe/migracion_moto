import os
import difflib
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

    # Limpiar caracteres especiales de basura al inicio y al final (como |, *, _, etc.)
    text = re.sub(r"^[^\w\s]+|[^\w\s]+$", "", text).strip()

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

def normalize_sitio_org_value(value):
    if not value:
        return ""

    val = value.strip().upper()
    val = re.sub(r'\s+', ' ', val)
    val = val.rstrip('.')

    # Parentheses cleanup
    val = re.sub(r'\(\s+', '(', val)
    val = re.sub(r'\s+\)', ')', val)
    # If there is a mismatched 9 or ) at the end
    val = re.sub(r'9$', ')', val)
    val = re.sub(r'/SEPTIMA', '(SEPTIMA', val)

    # General typos replacements
    val = val.replace("ORGANIAACION", "ORGANIZACION")
    val = val.replace("ORGANIACION", "ORGANIZACION")
    val = val.replace("ORGANIZACIÒN", "ORGANIZACION")
    val = val.replace("ORGANIZACIN", "ORGANIZACION")
    val = val.replace("MOVIMENTO", "MOVIMIENTO")
    val = val.replace("MOVIENTO", "MOVIMIENTO")
    val = val.replace("MODIMIENTO", "MOVIMIENTO")
    val = val.replace("MIAZ", "MAIZ")
    val = val.replace("MRIANO", "MARIANO")
    val = val.replace("MARIANOSANTIAGO", "MARIANO SANTIAGO")
    val = val.replace("MARINO SANTIAGO", "MARIANO SANTIAGO")
    val = val.replace("SANTIGO", "SANTIAGO")
    val = val.replace("SIIOT", "SITIO")
    val = val.replace("SIITIO", "SITIO")
    val = val.replace("STIO", "SITIO")
    val = val.replace("INTERICEANICO", "INTEROCEANICO")
    val = val.replace("BEBRERO", "FEBRERO")
    val = val.replace("JUEREZ", "JUAREZ")
    val = val.replace("JUREZ", "JUAREZ")
    val = val.replace("SEPYIMA", "SEPTIMA")
    val = val.replace("SEPTIM ", "SEPTIMA ")
    val = val.replace("SEPTIMA SECCIOMN", "SEPTIMA SECCION")
    val = val.replace("SEPTIMA SECCIOM", "SEPTIMA SECCION")
    val = val.replace("SEPTIM SECCION", "SEPTIMA SECCION")
    val = val.replace("SEPTIAM", "SEPTIMA")
    val = val.replace("CHE GORIO", "CHE GORIO")
    val = val.replace("CHEGORIO", "CHE GORIO")
    val = val.replace("CHEN GORIO", "CHE GORIO")
    val = val.replace("CHR GORIO", "CHE GORIO")
    val = val.replace("MELENDE", "MELENDRE")
    val = val.replace("MELENDR", "MELENDRE")
    val = val.replace("MENDRE", "MELENDRE")
    val = val.replace("MEENDRE", "MELENDRE")
    val = val.replace("ZUMA ZAPOTECA", "ZUMA")
    val = val.replace("AGOPE", "AGAPE")
    val = val.replace("BINNI LANY", "BINNI LANUU")
    val = val.replace("BINNI NA CHA HUI", "BINNI CHAHUI")
    val = val.replace("BINNI NACHAHUI", "BINNI CHAHUI")
    val = val.replace("3 DEMAYO", "3 DE MAYO")
    val = val.replace("3 MAYO", "3 DE MAYO")
    val = val.replace("MODI", "MODI")
    val = val.replace("MOVI", "MODI")
    val = val.replace("CODI", "MODI")
    val = val.replace("MIDI", "MODI")
    val = val.replace("NEZAA", "NEZA")
    val = val.replace("XHA VIZENDE", "XHAVIZENDE")
    val = val.replace("NOVENA SECION", "NOVENA SECCION")
    val = val.replace("NAA´ZIAA", "NAA ZIAA")

    # Clean A.C. suffix
    val = re.sub(r'\s+A\.?\s*C\.?$', '', val)
    # Clean leading 0 in numbers like 07 -> 7, 05 -> 5, 01 -> 1
    val = re.sub(r'\b0(\d)\b', r'\1', val)

    # Dictionary mappings for normalized inputs
    mappings = {
        # UCO / UCO (MARIANO SANTIAGO)
        "UCO (MARIANO SANTIAGO)": "UCO (MARIANO SANTIAGO)",
        "UCO (MARIANO SANTIAGO": "UCO (MARIANO SANTIAGO)",
        "UCO (MARIANO SANTIAGO9": "UCO (MARIANO SANTIAGO)",
        "UCO ( MARIANO SANTIAGO)": "UCO (MARIANO SANTIAGO)",
        "UCO (MARIANOSANTIAGO)": "UCO (MARIANO SANTIAGO)",
        "UCO (MARINO SANTIAGO)": "UCO (MARIANO SANTIAGO)",
        "UO (MARIANO SANTIAGO)": "UCO (MARIANO SANTIAGO)",
        "OCO (MARIANO SANTIAGO)": "UCO (MARIANO SANTIAGO)",
        "UCO(MARIANO SANTIAGO)": "UCO (MARIANO SANTIAGO)",
        "UCO": "UCO (MARIANO SANTIAGO)",
        "UO (MARIANO SANTIGO)": "UCO (MARIANO SANTIAGO)",

        # FUCO
        "FRENTE UNIDAD DE COMUNIDADES OAXAQUEÑAS (FUCO)": "FUCO",
        "FRENTE UNIDAD COMUNIDADES OAXAQUEÑAS (FUCO)": "FUCO",
        "FRENDE UNIDAD COMUNIDADES OAXAQUEÑAS (FUCO)": "FUCO",
        "FRENTE UNIDAD DE COMUNIDADES OAXAQUEÑAS FUCO": "FUCO",
        "FRENTE UNIDAS DE COMUNIDADES OAXAQUEÑAS (FUCO)": "FUCO",
        "FRENTE UNIDO DE COMUNIDADES OAXAQUEÑAS (FUCO)": "FUCO",
        "FRENTE UNIDAD DE COMUNIDADES OAXAQUEÑAS (FUCO": "FUCO",
        "FUCO": "FUCO",

        # FUCO BASE JUAREZ
        "FUCO 8BASE JUAREZ)": "FUCO (BASE JUAREZ)",
        "FUCO (BASE JUAREZ)": "FUCO (BASE JUAREZ)",
        "FUCO (BASE JUAREZ )": "FUCO (BASE JUAREZ)",
        "FUCO (BASE JUREZ )": "FUCO (BASE JUAREZ)",
        "FUCO (BASE JUEREZ )": "FUCO (BASE JUAREZ)",
        "FUCO  (BASE JUAREZ)": "FUCO (BASE JUAREZ)",

        # FUCO SEPTIMA SECCION
        "FUCO (SEPTIMA SECCION)": "FUCO (SEPTIMA SECCION)",
        "FUCO (SEPTIMA SECCION )": "FUCO (SEPTIMA SECCION)",
        "FUCO /SEPTIMA SECCION)": "FUCO (SEPTIMA SECCION)",
        "FUCO (SEPTIM SECCION 9": "FUCO (SEPTIMA SECCION)",
        "FUCO ( SEPTIMA SECCION )": "FUCO (SEPTIMA SECCION)",
        "FUCO (SEPTIMA SECCION9": "FUCO (SEPTIMA SECCION)",
        "FUCO (SEPYIMA SECCION)": "FUCO (SEPTIMA SECCION)",
        "FUCO (SEPTIMA SECCIOM)": "FUCO (SEPTIMA SECCION)",
        "FUCO (SEPTIMA SECCIOMN)": "FUCO (SEPTIMA SECCION)",
        "FUCO (SEPTIMA SECCION": "FUCO (SEPTIMA SECCION)",

        # ORG DEMOCRATICA DE REGENERACION
        "ORG DEMOCRATICA DE REGENERACION": "ORGANIZACION DEMOCRATICA DE REGENERACION",
        "0RG DEMOCRATICO DE REGENERACION": "ORGANIZACION DEMOCRATICA DE REGENERACION",
        "ORGANIZACIÓN DEMOCRÁTICO DE REGENERACIÓN VIVA JUCHITAN": "ORGANIZACION DEMOCRATICA DE REGENERACION",
        "ORGANIZACION DEMOCRATICO DE REGENERACION VIVA JUCHITAN": "ORGANIZACION DEMOCRATICA DE REGENERACION",

        # MOSI / MOVIMIENTO SOCIAL INDIGENA
        "M.O.S.I": "MOSI",
        "MOSI": "MOSI",
        "MOVIMIENTO SOCIAL INDIGENA": "MOSI",
        "MOVIMENTO SOCIAL INDIGENA": "MOSI",
        "MOVIENTO SOCIAL INDIGENA": "MOSI",
        "MOSI SEPTIMA SECCION": "MOSI 7MA SECCION",
        "MOSI 8A SECCION": "MOSI 8VA SECCION",
        "M.O.S.I 8VA SECCION": "MOSI 8VA SECCION",

        # OCLI
        "OCLI [CHE GORIO MELENDRE]": "OCLI (CHE GORIO MELENDRE)",
        "OCLI (CHE GORIO MELENDRE)": "OCLI (CHE GORIO MELENDRE)",
        "OCLI (CHE GORIO MELENDRE )": "OCLI (CHE GORIO MELENDRE)",
        "OCLI ( CHE GORIO MELENDRE )": "OCLI (CHE GORIO MELENDRE)",
        "OCLI (CHEN GORIO MELENDRE)": "OCLI (CHE GORIO MELENDRE)",
        "OCLI (CHE GORIO MENDRE)": "OCLI (CHE GORIO MELENDRE)",
        "OCLI (CHE GORIO MELENDRE9": "OCLI (CHE GORIO MELENDRE)",
        "OCLI (CHE GORIO MEENDRE)": "OCLI (CHE GORIO MELENDRE)",
        "OCLI(CHE GORIO MELENDRE)": "OCLI (CHE GORIO MELENDRE)",
        "CHE GORIO MELENDE": "OCLI (CHE GORIO MELENDRE)",
        "CHE GORIO MELENDR": "OCLI (CHE GORIO MELENDRE)",
        "CHR GORIO MELENDRE": "OCLI (CHE GORIO MELENDRE)",
        "CHEGORIO MELENDRE": "OCLI (CHE GORIO MELENDRE)",
        "OCLI (FRENTE DEMOCRATICO)": "OCLI (FRENTE DEMOCRATICO)",
        "OCLI (FRETE DEMOCRATICO)": "OCLI (FRENTE DEMOCRATICO)",
        "OCLI (FRENTE DEMOCRETICO)": "OCLI (FRENTE DEMOCRATICO)",
        "OCLI (FRENTE DEMCRATICO)": "OCLI (FRENTE DEMOCRATICO)",
        "OCLI (FRENTA DEMOCRATICO)": "OCLI (FRENTE DEMOCRATICO)",
        "OCLI (FRENRTE DEMOCRATICO)": "OCLI (FRENTE DEMOCRATICO)",
        "OCLI ( FRENTE DEMOCRATICO)": "OCLI (FRENTE DEMOCRATICO)",

        # SITIO EL CALVARIO
        "SITIO CALVARIO": "SITIO EL CALVARIO",
        "SITIO EL CALVARIO": "SITIO EL CALVARIO",
        "EL CALVARIO": "SITIO EL CALVARIO",

        # BINNI LANUU
        "BINNI LANY A.C": "BINNI LANUU A.C.",
        "BINNI LANUU 1RO DE MAYO": "BINNI LANUU 1RO DE MAYO",
        "BINNI LANUU 1RA DE MAYO": "BINNI LANUU 1RO DE MAYO",
        "BINNI LANUU 1O DE MAYO": "BINNI LANUU 1RO DE MAYO",

        # AGAPE AMOR DE DIOS
        "AGOPE AMOR DE DIOS": "AGAPE AMOR DE DIOS",
        "AGAPE AMOR DE DIOS": "AGAPE AMOR DE DIOS",
        "UNIÓN DE MOTOTAXIS ÁGAPE AMOR DE DIOS": "AGAPE AMOR DE DIOS",

        # COCEI COL. MARTIRES
        "COCEI COL. MARTIRES": "COCEI COLONIA MARTIRES",
        "COCEI COL.MARTIRES": "COCEI COLONIA MARTIRES",
        "COCEI  COL. MARTIRES": "COCEI COLONIA MARTIRES",
        "COCEI COL MARTIRES": "COCEI COLONIA MARTIRES",

        # COCEI NUEVA GENERACION
        "UNION DE MOTOTAXI COCEI NUEVA GENERACION": "UNION DE MOTOTAXI COCEI NUEVA GENERACION",
        "UNION DE MOTOTAXI  COCEI NUEVA GENERACION": "UNION DE MOTOTAXI COCEI NUEVA GENERACION",
        "UNION DE MOTOTAXI COCEI NUEVA  GENERACION": "UNION DE MOTOTAXI COCEI NUEVA GENERACION",
        "COCEI NUEVA GENERACION": "UNION DE MOTOTAXI COCEI NUEVA GENERACION",

        # DESPIERTA JUCHITAN
        "DESPIERTAJUCHITAN OAX": "DESPIERTA JUCHITAN",
        "DESPIERTA JCUHITAN": "DESPIERTA JUCHITAN",
        "DESPIERTA JUCHITAN": "DESPIERTA JUCHITAN",

        # UCP
        "UCP ( JAVIER SANCHEZ OROZCO KALY)": "UCP (JAVIER SANCHEZ OROZCO KALY)",
        "UCP (JAVIER SANCHEZ OROZCO KALY)": "UCP (JAVIER SANCHEZ OROZCO KALY)",
        "UCP (JAVIER SANCHEZOROZCO KALY)": "UCP (JAVIER SANCHEZ OROZCO KALY)",
        "UCP (JAVIER SANCHEZ OROZCO KALLY)": "UCP (JAVIER SANCHEZ OROZCO KALY)",
        "UCP - UNIDAD DE COLONIAS POPULARES JAVIER": "UCP (JAVIER SANCHEZ OROZCO KALY)",
        "U.C.P": "UCP (JAVIER SANCHEZ OROZCO KALY)",
        "UCP": "UCP (JAVIER SANCHEZ OROZCO KALY)",

        # GRUPO MAIZ 2 DE ABRIL
        "GRUPO MAIZ (COLONIA GUSTAVO)": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "GRUPO MAIZ 2 DE ABRIL (GUSTAVO PINEDA DE LA CRUZ)": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "GRUPO MAIZ 2 DE ABRIL (GUSTAVO PINEDA DE LA CRUZ )": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "GRUPO MAIZ 2 DE ABRIL (GUSTAVO PINEDA DE  LA CRUZ)": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "GRUPO MAIZ 2 DE ABRIL (COL. GUSTAVO)": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "GRUPO MAIZ (GUSTAVO PINEDA DE LA CRUZ)": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "GRUPO; MAIZ (COL. GUSTAVO PINEDA DE LA CRUZ)": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "GRUPO: MAIZ (COL. GUSTAVO PINEDA DE LA CRUZ)": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "GRUPO: MAIZ ( COL. GUSTAVO PINEDA DE LA CRUZ)": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "GRUPO: (COL. GUSTAVO PINEDA DE LA CRUZ)": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "GRUPO: MAIZ (GUSTAVO PINEDA DE LA CRUZ)": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "GRUPO MAIZ (GUSTAVO PINEDA DE  LA CRUZ)": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "2 DE ABRIL GRUPO  MAIZ": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "2 DE ABRIL GRUPO MIAZ": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "2 DE ABRIL GRUPO MAIZ": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "2 DE ABRILGRUPO MAIZ": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "2  DE ABRIL GRUPO MAIZ": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "2 DE ABRIL": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",
        "GRUPO MAIZ (COL 2 DE ABRIL)": "GRUPO MAIZ 2 DE ABRIL (COLONIA GUSTAVO)",

        # GRUPO MAIZ 7MA SECCION
        "GRUPO MAIZ 7MA SECCION": "GRUPO MAIZ 7MA SECCION (CANDIDO JIMENEZ GONZALEZ)",
        "GRUPO MAIZ 7A SECCION": "GRUPO MAIZ 7MA SECCION (CANDIDO JIMENEZ GONZALEZ)",
        "GRUPO: MAIZ 7MA SECCION (CANDIDO JIMENEZ GONZALEZ)": "GRUPO MAIZ 7MA SECCION (CANDIDO JIMENEZ GONZALEZ)",
        "GRUPO: MAIZ 7MA SECCION (CANDIDO JIMENEZ GONZALES)": "GRUPO MAIZ 7MA SECCION (CANDIDO JIMENEZ GONZALEZ)",
        "GRUPO: MAIZ 7MA SECCION  (CANDIDO JIMENEZ GONZALES)": "GRUPO MAIZ 7MA SECCION (CANDIDO JIMENEZ GONZALEZ)",
        "GRUPO: MAIZ 7MA SECCION": "GRUPO MAIZ 7MA SECCION (CANDIDO JIMENEZ GONZALEZ)",
        "GRUPO: MAIZ  7MA SECCION": "GRUPO MAIZ 7MA SECCION (CANDIDO JIMENEZ GONZALEZ)",

        # GRUPO MAIZ 9A SECCION (CASA DEL PINTOR)
        "GRUPO MAIZ 9A SECCION (CASA DEL PINTOR)": "GRUPO MAIZ 9A SECCION (CASA DEL PINTOR)",
        "GRUPO MAIZ 9A SECCION (CASA DE PINTOR)": "GRUPO MAIZ 9A SECCION (CASA DEL PINTOR)",
        "GRUPO MAIZ 9MA SECCION (CASA DEL PINTOR)": "GRUPO MAIZ 9A SECCION (CASA DEL PINTOR)",
        "GRUPO MAIZ 9 SECCION (CASA DEL PINTOR)": "GRUPO MAIZ 9A SECCION (CASA DEL PINTOR)",
        "GRUPO MAIZ 9NA SECCION (BASE DEL PINTOR)": "GRUPO MAIZ 9A SECCION (CASA DEL PINTOR)",
        "GRUPO MAIZ 9NA SECCION (CASA DEL PINTOR)": "GRUPO MAIZ 9A SECCION (CASA DEL PINTOR)",

        # INTEROCEANICO
        "INTEROCEANICO (SEGUNDA SECCION )": "INTEROCEANICO (SEGUNDA SECCION)",
        "INTEROCEANICO (SEGUNDA SECCION)": "INTEROCEANICO (SEGUNDA SECCION)",
        "INTEROCEANICO (LA ESTACION )": "INTEROCEANICO (LA ESTACION)",
        "INTEROCEANICO (LA ESTACION)": "INTEROCEANICO (LA ESTACION)",
        "INTEROCEANICO": "INTEROCEANICO",
        "INTERICEANICO": "INTEROCEANICO",

        # GUENDA NE STIPA DIDXAZA
        "GUENDA NE STIPA DI DXA ZA": "GUENDA NE STIPA DIDXAZA",
        "GUENDA NE STIPA DIDXAZA": "GUENDA NE STIPA DIDXAZA",

        # ORGANIZACION 21 DE JUNIO
        "ORGANIZACION 21 DE JUNIO": "ORGANIZACION 21 DE JUNIO",
        "ORGANIACION 21 DE JUNIO": "ORGANIZACION 21 DE JUNIO",

        # 7 DE OCTUBRE
        "7 DE OCTUBRE": "7 DE OCTUBRE",
        "7 DE OCTUBRE.": "7 DE OCTUBRE",
        "07 DE OCTUBRE": "7 DE OCTUBRE",

        # 5 DE MAYO
        "5 DE MAYO": "5 DE MAYO",
        "05 DE MAYO": "5 DE MAYO",
        "5 MAYO": "5 DE MAYO",

        # 1 DE JUNIO
        "1 DE JUNIO": "1 DE JUNIO",
        "01 DE JUNIO": "1 DE JUNIO",

        # 7MA SECCION MAIZ
        "7MA SECCION MAIZ": "7MA SECCION MAIZ",
        "7MA SECCION MIAZ": "7MA SECCION MAIZ",
        "7A SECCION MAIZ": "7MA SECCION MAIZ",

        # 9 SECCION NORMAN EDDI
        "9 SECCION (NORMAN EDDI)": "9 SECCION (NORMAN EDDI)",
        "9 SECCION(NORMAN EDDI)": "9 SECCION (NORMAN EDDI)",
        "9 SEECCION (NORMAN EDDI)": "9 SECCION (NORMAN EDDI)",
        "9 SECCION(NORMA EDDI)": "9 SECCION (NORMAN EDDI)",
        "9 SECCION (NORMAN EDDI9": "9 SECCION (NORMAN EDDI)",
        "9 SECCION ( NORMAN EDDI9": "9 SECCION (NORMAN EDDI)",

        # 3 DE MAYO (FUCO)
        "3 DE MAYO FUCO": "3 DE MAYO (FUCO)",
        "3 DEMAYO FUCO": "3 DE MAYO (FUCO)",
        "3 MAYO FUCO": "3 DE MAYO (FUCO)",

        # ZAPANDU
        "ZAPAMDU": "ZAPANDU",
        "ZAPANDU": "ZAPANDU",
        "GRUPO DE MOTOTAXIS ORGANIZACIÓN ZAPANDU": "ZAPANDU",

        # SITIO MARIANO MONTERO
        "STIO MARIANO MONTERO": "SITIO MARIANO MONTERO",
        "SIITIO MARIANO MONTERO": "SITIO MARIANO MONTERO",

        # GUENDA BIICHI
        "GUENDA BICHI": "GUENDA BIICHI",
        "GUENDA BIICHI": "GUENDA BIICHI",

        # UMI
        "UMI (CDP)": "UMI",
        "UMI": "UMI",

        # EMP
        "EMP (EMILIO MONTERO PEREZ)": "EMP",
        "EMP": "EMP",

        # CADEPP
        "CADED": "CADEPP",
        "CADEPP": "CADEPP",

        # OMI
        "ORGANIZACION DE MOTOTAXIS ISTMEÑOS (OMI)": "ORGANIZACION DE MOTOTAXIS ISTMEÑOS (OMI)",
        "ORGANIZACION DE MOTOTAXIS ISTMEÑOS  (OMI)": "ORGANIZACION DE MOTOTAXIS ISTMEÑOS (OMI)",
        "ORGANIZACION DE MOTOTAXIS ISTMEÑOS": "ORGANIZACION DE MOTOTAXIS ISTMEÑOS (OMI)",
        "OMI": "ORGANIZACION DE MOTOTAXIS ISTMEÑOS (OMI)",

        # OMJ
        "OMJ ORGANIZACION DE MOTOTAXIS JUCHITAN": "ORGANIZACION DE MOTOTAXIS JUCHITAN (OMJ)",
        "ORGANIZACION DE MOTOTAXI JUCHITAN": "ORGANIZACION DE MOTOTAXIS JUCHITAN (OMJ)",
        "OMJ": "ORGANIZACION DE MOTOTAXIS JUCHITAN (OMJ)",

        # STIPA STINUU
        "ORGANIZACION STIPA STINUU A.C": "ORGANIZACION STIPA STINUU",
        "ORGANIAACION STIPA STINUU A.C": "ORGANIZACION STIPA STINUU",
        "STAA STINU": "ORGANIZACION STIPA STINUU",
        "SAA STINU": "ORGANIZACION STIPA STINUU",

        # TE SERVIMOS CON EL CORAZON
        "TE SERVIMOS CON EL CORAZON JUCHITAN DE LAS FLORES": "TE SERVIMOS CON EL CORAZON",
        "TE SERVIMOS CON EL CORAZON": "TE SERVIMOS CON EL CORAZON",

        # SITIO 24 DE FEBRERO
        "SITIO 24 DE FEBRERO": "SITIO 24 DE FEBRERO",
        "SIIOT 24 DE FEBRERO": "SITIO 24 DE FEBRERO",

        # ORGANIZACION SHALOM
        "ORGANIZACIÓN SHALOM": "ORGANIZACION SHALOM",
        "ORGANIZACION SHALOM A.C": "ORGANIZACION SHALOM",

        # ORG DEMOCRATICA INDEPENDIENTE
        "ORG DEMOCRATICA INDEPENDIENTE": "ORGANIZACION DEMOCRATICA INDEPENDIENTE",

        # OCLI 9 SECCION
        "OCLI 9° SECCION": "OCLI 9A SECCION",
        "OCLI (9 SECCION)": "OCLI 9A SECCION",

        # FRENTE LABORAL 1 DE MAYO
        "FRENTE LABORAL 1° DE MAYO": "FRENTE LABORAL 1RO DE MAYO",
        "FRENTE LABORAL 1O DE MAYO": "FRENTE LABORAL 1RO DE MAYO",

        # 28 DE DICIEMBRE NAA ZIAA
        "28 DE DICIEMBRE NAA´ZIAA": "28 DE DICIEMBRE NAA ZIAA",
        "NAA ZIAA 28 DE DICIEMBRE": "28 DE DICIEMBRE NAA ZIAA",

        # 15 DE SEPTIEMBRE
        "15DE SEPTIEMBRE": "15 DE SEPTIEMBRE",
        "15 DE SEPTIEMBRE": "15 DE SEPTIEMBRE",

        # 10 DE FEBRERO / 10 DE BEBRERO
        "10 DE FEBRERO": "ORGANIZACION 10 DE FEBRERO",
        "10 DE BEBRERO": "ORGANIZACION 10 DE FEBRERO",
        "ORG 10 DE FEB": "ORGANIZACION 10 DE FEBRERO",

        # OLI
        "OLI": "ORGANIZACION LIBERTAD DEL ISTMO",

        # LAZARO CARDENAS
        "LAZAR CARDENAS": "LAZARO CARDENAS",

        # MIR / MOVIMIENTO DE IZQUIERDA REVOLUCIONARIA INDEPENDIENTE
        "MOVIMIENTO DE IZQUIERDA REVOLUCIONARIA INDEPENDIENTE": "MOVIMIENTO DE IZQUIERDA REVOLUCIONARIA INDEPENDIENTE (MIR)",
        "MIR": "MOVIMIENTO DE IZQUIERDA REVOLUCIONARIA INDEPENDIENTE (MIR)",
        "MODIMIENTO DE IZQUIERDA REVOLUCIONARIA INDEPENDIENTE": "MOVIMIENTO DE IZQUIERDA REVOLUCIONARIA INDEPENDIENTE (MIR)",

        # HERMANOS FLORES MAGON
        "UNIÓN DE MOTOTAXIS HERMANOS FLORES MAGÓN": "UNION DE MOTOTAXIS HERMANOS FLORES MAGON",
        "HERMANOS FLORES MAGON A.C.": "UNION DE MOTOTAXIS HERMANOS FLORES MAGON",

        # 11 DE MAYO
        "UNIÓN DE MOTOTAXIS 11 DE MAYO": "UNION DE MOTOTAXI 11 DE MAYO",
        "UNION DE MOTO TAXI 11 DE MAYO": "UNION DE MOTOTAXI 11 DE MAYO",

        # GRUPO EBEN EZER
        "GRUPO EBEN EZER": "GRUPO EBEN EZER",
        "GRUPO EBEN": "GRUPO EBEN EZER",
    }

    return mappings.get(val, val)

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

    org_part = display
    sitio_part = display

    if " - " in key:
        display_parts = re.split(r"\s*[-–—]\s*", display, maxsplit=1)
        org_part = display_parts[0].strip()
        sitio_part = display_parts[1].strip() if len(display_parts) > 1 else org_part

    # Normalize both parts individually using our mapping logic
    org_display = normalize_sitio_org_value(org_part)
    sitio_display = normalize_sitio_org_value(sitio_part)

    return {
        "org_display": org_display,
        "sitio_display": sitio_display,
        "org_key": normalize_key(org_display),
        "sitio_key": normalize_key(sitio_display),
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
    text = re.sub(r",.*$", "", text).strip()

    exceptions = {
        "JUCHITAN": "JUCHITAN DE ZARAGOZA",
        "JUCHUTAN": "JUCHITAN DE ZARAGOZA",
        "TEHUANTEPEC": "SANTO DOMINGO TEHUANTEPEC",
        "CD IXTEPEC": "CIUDAD IXTEPEC",
        "CD. IXTEPEC": "CIUDAD IXTEPEC",
        "LA VENTOSA": "LA VENTOSA",
    }

    return exceptions.get(text, text)

def split_full_name(full_name: str):
    if not full_name:
        return "", "", ""

    full_name = re.sub(r'\s+', ' ', full_name.strip())
    words = full_name.split()

    if not words:
        return "", "", ""

    particles = {"de", "la", "las", "el", "los", "del", "y"}

    processed_words = []
    i = 0
    while i < len(words):
        word = words[i]
        if word.lower() in particles and i + 1 < len(words):
            combined = word
            while i + 1 < len(words) and words[i].lower() in particles:
                i += 1
                combined += " " + words[i]
            processed_words.append(combined)
        else:
            processed_words.append(word)
        i += 1

    n = len(processed_words)
    if n == 1:
        return processed_words[0], "", ""
    elif n == 2:
        return processed_words[0], processed_words[1], ""
    elif n == 3:
        return processed_words[0], processed_words[1], processed_words[2]
    else:
        nombre = " ".join(processed_words[:-2])
        paterno = processed_words[-2]
        materno = processed_words[-1]
        return nombre, paterno, materno

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
    cur.execute('ALTER TABLE public.persona ADD COLUMN IF NOT EXISTS "esMigrado" BOOLEAN DEFAULT FALSE;')
    cur.execute('ALTER TABLE public.vehiculo ADD COLUMN IF NOT EXISTS "esMigrado" BOOLEAN DEFAULT FALSE;')

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
        SELECT 
            id, 
            "isActive",
            REGEXP_REPLACE(
                TRIM(
                    COALESCE(nombre, '') || ' ' || 
                    COALESCE("apellidoPaterno", '') || ' ' || 
                    COALESCE("apellidoMaterno", '')
                ),
                '\\s+', ' ', 'g'
            ) AS full_name
        FROM public.persona
        WHERE "deletedAt" IS NULL
        ORDER BY "isActive" ASC
        """
    )

    cache = {}

    for row in cur.fetchall():
        nombre_key = normalize_key(row["full_name"])

        if nombre_key:
            cache[nombre_key] = row["id"]

    return cache

def find_fuzzy_match(nombre_normalizado, persona_cache, threshold=0.88):
    if not nombre_normalizado or not persona_cache:
        return None

    longitud_buscada = len(nombre_normalizado)
    
    # 1. Preparar versión ordenada de palabras para el nombre buscado
    palabras_buscadas = sorted(nombre_normalizado.split())
    sorted_buscado = "".join(palabras_buscadas)
    
    for cache_key, persona_id in persona_cache.items():
        # Filtro de longitud rápido para optimizar rendimiento (+/- 8 caracteres)
        if abs(len(cache_key) - longitud_buscada) > 8:
            continue
            
        # A. Comparación difusa directa
        ratio_directo = difflib.SequenceMatcher(None, nombre_normalizado, cache_key).ratio()
        if ratio_directo >= threshold:
            return persona_id
            
        # B. Comparación difusa con palabras ordenadas (para apellidos invertidos o desordenados)
        palabras_cache = sorted(cache_key.split())
        sorted_cache = "".join(palabras_cache)
        
        ratio_ordenado = difflib.SequenceMatcher(None, sorted_buscado, sorted_cache).ratio()
        if ratio_ordenado >= threshold:
            return persona_id
            
    return None

def get_or_create_persona(cur, nombre, direccion_id=None, activo=True, persona_cache=None, fallecidos_ids=None):
    nombre = clean_text(nombre)

    if not nombre:
        return None

    nombre_key = normalize_key(nombre)

    if persona_cache is not None:
        persona_id = None
        if nombre_key in persona_cache:
            persona_id = persona_cache[nombre_key]
        else:
            # Intentar coincidencia difusa
            persona_id = find_fuzzy_match(nombre_key, persona_cache)
            if persona_id:
                persona_cache[nombre_key] = persona_id

        if persona_id:
            # Si en la fila actual viene como fallecido (activo = False), forzamos su estado
            if not activo:
                if fallecidos_ids is not None:
                    fallecidos_ids.add(persona_id)
                cur.execute(
                    """
                    UPDATE public.persona
                    SET
                        "isActive" = false,
                        "updatedAt" = now()
                    WHERE id = %s
                    """,
                    (persona_id,),
                )
            else:
                # Si viene activo (activo = True), solo lo reactivamos si NO ha sido marcado como fallecido
                if fallecidos_ids is None or persona_id not in fallecidos_ids:
                    cur.execute(
                        """
                        UPDATE public.persona
                        SET
                            "isActive" = true,
                            "updatedAt" = now()
                        WHERE id = %s
                        """,
                        (persona_id,),
                    )

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

    # Split name for DB fields
    nombre_part, paterno_part, materno_part = split_full_name(nombre)
    paterno_part = paterno_part or None
    materno_part = materno_part or None

    existing_id = fetch_one_id(
        cur,
        """
        SELECT id
        FROM public.persona
        WHERE REGEXP_REPLACE(
            TRIM(
                UPPER(
                    COALESCE(nombre, '') || ' ' || 
                    COALESCE("apellidoPaterno", '') || ' ' || 
                    COALESCE("apellidoMaterno", '')
                )
            ),
            '\\s+', ' ', 'g'
        ) = %s
          AND "deletedAt" IS NULL
        LIMIT 1
        """,
        (nombre.upper().strip(),),
    )

    if existing_id:
        if persona_cache is not None:
            persona_cache[nombre_key] = existing_id

        # Si en la fila actual viene como fallecido (activo = False), forzamos su estado
        if not activo:
            if fallecidos_ids is not None:
                fallecidos_ids.add(existing_id)
            cur.execute(
                """
                UPDATE public.persona
                SET
                    "isActive" = false,
                    "updatedAt" = now()
                WHERE id = %s
                """,
                (existing_id,),
            )
        else:
            # Si viene activo (activo = True), solo lo reactivamos si NO ha sido marcado como fallecido
            if fallecidos_ids is None or existing_id not in fallecidos_ids:
                cur.execute(
                    """
                    UPDATE public.persona
                    SET
                        "isActive" = true,
                        "updatedAt" = now()
                    WHERE id = %s
                    """,
                    (existing_id,),
                )

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
                (direccion_id, existing_id),
            )

        return existing_id

    cur.execute(
        """
        INSERT INTO public.persona (
            "createdAt",
            "updatedAt",
            "isActive",
            nombre,
            "apellidoPaterno",
            "apellidoMaterno",
            curp,
            "direccionId",
            "esMigrado"
        )
        VALUES (now(), now(), %s, %s, %s, %s, NULL, %s, true)
        RETURNING id
        """,
        (activo, nombre_part, paterno_part, materno_part, direccion_id),
    )

    persona_id = cur.fetchone()["id"]

    if persona_cache is not None:
        persona_cache[nombre_key] = persona_id

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
          AND "deletedAt" IS NULL
        LIMIT 1
        """,
        (persona_id,),
    )

    if existing_id:
        if organizacion_id:
            cur.execute(
                """
                UPDATE public.propietario 
                SET "organizacionId" = COALESCE("organizacionId", %s),
                    "updatedAt" = now()
                WHERE id = %s
                """,
                (organizacion_id, existing_id)
            )
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

def get_or_create_chofer(cur, nombre, persona_cache=None, fallecidos_ids=None):
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
        activo=True,
        persona_cache=persona_cache,
        fallecidos_ids=fallecidos_ids
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
    if not serie or re.match(r"^S/N\d*$", serie.strip().upper()):
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

def create_vehiculo(cur, row, propietario_id, sitio_id, serie, vehiculo_uuid):
    folio_carpeta = clean_optional(row.get("FOLIO DE CARPETA"))

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
            "propietarioId",
            "esMigrado"
        )
        VALUES (
            now(), now(), true,
            %s::uuid, %s,
            NULL, %s, NULL, NULL, %s, %s,
            true
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

def get_next_sn_counter(cur):
    cur.execute(
        """
        SELECT numero_serie 
        FROM public.vehiculo 
        WHERE numero_serie LIKE 'S/N%'
          AND "deletedAt" IS NULL
        """
    )
    max_val = 0
    for row in cur.fetchall():
        match = re.match(r"^S/N(\d+)$", row["numero_serie"])
        if match:
            val = int(match.group(1))
            if val > max_val:
                max_val = val
    return max_val + 1

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
            
            # Cargar los IDs de personas que ya son inactivas en la base de datos para que no se reactiven
            cur.execute('SELECT id FROM public.persona WHERE "isActive" = false AND "deletedAt" IS NULL')
            fallecidos_ids = set(r["id"] for r in cur.fetchall())
            
            sn_counter = get_next_sn_counter(cur)

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
                        serie = f"S/N{sn_counter}"
                        sn_counter += 1
                    vehiculo_uuid = valid_uuid(clean_optional(row.get("UUID"))) or str(uuid.uuid4())

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
                        fallecidos_ids=fallecidos_ids,
                    )

                    propietario_id = get_or_create_propietario(
                        cur,
                        persona_id,
                        organizacion_id=organizacion_id,
                    )

                    existing_vehiculo_id = vehiculo_exists_by_uuid(cur, vehiculo_uuid)
                    vehiculo_por_serie = find_vehiculo_by_serie(cur, serie)

                    vehiculo_id_to_use = existing_vehiculo_id or (vehiculo_por_serie["id"] if vehiculo_por_serie else None)

                    if vehiculo_id_to_use:
                        vehiculo_id = vehiculo_id_to_use

                        cur.execute(
                            """
                            UPDATE public.vehiculo
                            SET
                                folio_carpeta = COALESCE(folio_carpeta, %s),
                                numero_serie = COALESCE(numero_serie, %s),
                                "sitioId" = COALESCE("sitioId", %s),
                                "propietarioId" = %s,
                                "esMigrado" = true,
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
                        vehiculo_id = create_vehiculo(
                            cur,
                            row,
                            propietario_id=propietario_id,
                            sitio_id=sitio_id,
                            serie=serie,
                            vehiculo_uuid=vehiculo_uuid,
                        )

                    link_vehiculo_propietario(
                        cur,
                        vehiculo_id=vehiculo_id,
                        propietario_id=propietario_id,
                    )

                    chofer_1_id = get_or_create_chofer(cur, row.get("CHOFER 1"), persona_cache=persona_cache, fallecidos_ids=fallecidos_ids)
                    chofer_2_id = get_or_create_chofer(cur, row.get("CHOFER 2"), persona_cache=persona_cache, fallecidos_ids=fallecidos_ids)

                    link_vehiculo_chofer(cur, vehiculo_id, chofer_1_id)
                    link_vehiculo_chofer(cur, vehiculo_id, chofer_2_id)

                    migrated += 1
                    cur.execute(f"RELEASE SAVEPOINT row_{index}")

                    if (index + 1) % 100 == 0:
                        print(f"Migrados: {index + 1}/{total}")

                    # Commit every 500 rows to release locks, free memory, and persist progress
                    if (index + 1) % 500 == 0:
                        conn.commit()

                except (psycopg2.OperationalError, psycopg2.InterfaceError) as conn_error:
                    print(f"\n[ERROR DE CONEXIÓN] Se perdió la conexión con el servidor de base de datos en la fila {row_number}: {conn_error}")
                    raise conn_error

                except Exception as row_error:
                    try:
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
                    except Exception as savepoint_err:
                        print(f"\n[ERROR GRAVE] No se pudo hacer rollback del savepoint en la fila {row_number}: {savepoint_err}")
                        raise row_error

            # Final commit for the remaining rows
            conn.commit()

            print("Migración terminada")
            print(f"Total CSV: {total}")
            print(f"Migrados: {migrated}")
            print(f"Rechazados: {rejected}")

    except (psycopg2.OperationalError, psycopg2.InterfaceError) as conn_error:
        print("\n[ERROR GENERAL] La migración se detuvo debido a un problema de conexión.")
        raise conn_error
    except Exception as e:
        try:
            if conn and not conn.closed:
                conn.rollback()
                print("Error general. Se hizo rollback.")
        except Exception:
            pass
        raise e

    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

if __name__ == "__main__":
    migrate()
