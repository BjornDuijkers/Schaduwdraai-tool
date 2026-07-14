# Schaduwdraaitool

Lokale MVP voor het vergelijken van twee PDF-bundels met loonstroken.

De app blijft lokaal draaien. PDF's worden niet naar een cloudservice gestuurd.

## Wat de app doet

- Upload twee PDF-documenten met loonstroken.
- Extraheert tekst uit digitale PDF's met PyMuPDF.
- Gebruikt RapidOCR als lokale OCR-fallback voor afbeelding-PDF's zonder tekstlaag.
- Probeert daarnaast optioneel OCRmyPDF te gebruiken als die lokaal beschikbaar is.
- Splitst loonstroken per medewerker.
- Matcht medewerkers op medewerkercode plus geboortedatum, met naam plus geboortedatum als fallback.
- Vergelijkt looncomponenten en bedragen.
- Herkent periode, payrollprovider en scenario waar mogelijk.
- Laat verschillen interactief reviewen met status, opmerking en issue-export.
- Onthoudt geleerde componentmappings lokaal in SQLite voor volgende vergelijkingen.
- Exporteert een Excelrapport met overeenkomsten, verschillen, reviewstatussen, issues, ontbrekende loonstroken en extractiewaarschuwingen.

## Installatie

```powershell
cd "C:\Users\BjornDuijkers\OneDrive - Salure BV\Documenten\Codex\schaduwdraaitool"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m app.main
```

Open daarna:

```text
http://127.0.0.1:5057
```

## OCR voor gescande PDF's en afbeelding-PDF's

Digitale loonstrook-PDF's werken direct. Voor scans of afbeelding-PDF's zonder tekstlaag is OCR nodig.

De app gebruikt standaard RapidOCR lokaal. Als `ocrmypdf` beschikbaar is, probeert de app dat eerst. Als OCR niet lukt, geeft het rapport een waarschuwing in plaats van te gokken.

Optionele extra OCR-installatie:

```powershell
python -m pip install ocrmypdf
```

Daarnaast zijn Tesseract en Ghostscript nodig als systeemprogramma's. Installeer die alleen als gescande PDF's verwerkt moeten worden.

## Componentmapping

De app kan een optionele CSV gebruiken om componentnamen uit twee salarissystemen te koppelen.

Voorbeeld:

```csv
document_a,document_b,canonical
1000 Bruto salaris,SAL_BASIS,Bruto salaris
2000 Pensioenpremie,PENS_WN,Pensioen werknemer
```

Zonder mapping vergelijkt de app op componentcode, of anders op genormaliseerde omschrijving.

## Reviewmodus en leerfunctie

Na een vergelijking kun je via **Review verschillen** elke afwijking markeren als `akkoord`, `uitzoeken`, `foutieve match` of `exporteren als issue`. Opmerkingen en issue-markeringen worden lokaal opgeslagen in:

```text
instance/schaduwdraai.db
```

In hetzelfde scherm kun je componentnamen aan elkaar koppelen. Bijvoorbeeld: `Vakantiegeld` en `Reservering vakantietoeslag` met canonieke naam `Vakantiegeld`. Die mapping wordt automatisch gebruikt bij volgende vergelijkingen.
