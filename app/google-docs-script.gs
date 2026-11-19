/**
 * VideoGrab: Quellenliste als Google Doc.
 *
 * Einrichtung (einmalig, 5 Minuten):
 *  1. https://script.google.com oeffnen > "Neues Projekt"
 *  2. Diesen kompletten Text in den Editor einfuegen (den Beispielcode vorher loeschen)
 *  3. Unten bei TOKEN ein eigenes Passwort eintragen (beliebige Zeichen)
 *  4. Oben rechts "Bereitstellen" > "Neue Bereitstellung" > Typ "Web-App"
 *     - Ausfuehren als: Ich
 *     - Zugriff: Jeder
 *     > "Bereitstellen", dann einmal den Zugriff fuer dein Google-Konto erlauben
 *  5. Die angezeigte Web-App-URL (endet auf /exec) und das Passwort in VideoGrab
 *     unter Einstellungen > Google Docs eintragen.
 *
 * Alle Dokumente landen im Drive-Ordner "VideoGrab Quellen". Pro Projekt entsteht ein Dokument,
 * das bei jedem Export aktualisiert wird (der Link bleibt gleich).
 */

var TOKEN = "hier-dein-passwort";
var FOLDER_NAME = "VideoGrab Quellen";

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    if (TOKEN && data.token !== TOKEN) {
      return out({ ok: false, error: "Passwort stimmt nicht (Einstellungen > Google Docs)" });
    }
    var folder = getFolder();
    var doc = null;
    if (data.doc_id) {
      try { doc = DocumentApp.openById(data.doc_id); } catch (err) { doc = null; }
    }
    if (!doc) {
      doc = DocumentApp.create("Quellen: " + data.project);
      var file = DriveApp.getFileById(doc.getId());
      try { file.moveTo(folder); } catch (err) { folder.addFile(file); }
    }
    fill(doc, data);
    doc.saveAndClose();
    return out({ ok: true, id: doc.getId(), url: doc.getUrl() });
  } catch (err) {
    return out({ ok: false, error: String(err) });
  }
}

function doGet() {
  return ContentService.createTextOutput("VideoGrab Quellen-Script laeuft.");
}

function getFolder() {
  var it = DriveApp.getFoldersByName(FOLDER_NAME);
  return it.hasNext() ? it.next() : DriveApp.createFolder(FOLDER_NAME);
}

function fill(doc, data) {
  var body = doc.getBody();
  body.clear();
  body.appendParagraph("Quellen: " + data.project).setHeading(DocumentApp.ParagraphHeading.TITLE);
  body.appendParagraph("Stand " + data.generated + " · " + data.rows.length + " Eintraege · erstellt mit VideoGrab")
      .setForegroundColor("#666666");
  body.appendParagraph("");
  var table = [["#", "Titel", "Plattform / Kanal", "Link", "Notiz", "Geladen / Datei"]];
  data.rows.forEach(function (r, i) {
    table.push([
      String(i + 1),
      r.title || "",
      (r.platform || "") + (r.uploader ? " · " + r.uploader : "") + (r.kind === "image" ? " (Bild)" : ""),
      r.url || "",
      r.note || "",
      (r.date || "") + (r.file ? "\n" + r.file : "")
    ]);
  });
  var t = body.appendTable(table);
  t.getRow(0).editAsText().setBold(true);
  for (var i = 1; i < t.getNumRows(); i++) {
    var cell = t.getRow(i).getCell(3);
    var url = cell.getText();
    if (url) cell.editAsText().setLinkUrl(url);
  }
  body.appendParagraph("");
  body.appendParagraph("Nur die Links").setHeading(DocumentApp.ParagraphHeading.HEADING2);
  data.rows.forEach(function (r) {
    if (r.url) body.appendParagraph(r.url).editAsText().setLinkUrl(r.url);
  });
}

function out(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
