#!/usr/bin/env python3
"""Build the calendar-export dev test harness (NOT deployed anywhere).

Wraps the REAL _personalize.en.html partial in a minimal page with a
synthetic {{PZ_EVENTS_JSON}} payload covering every event time-form, then
appends an assertion script that drives the actual shipped code through the
DOM (hash personalization → card render → .ics blob capture → RFC 5545
checks, including DST-week dates and the SUPREP clamp).

Usage:
    .venv/bin/python scripts/build_dev_test_page.py
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
        --headless=new --disable-gpu --virtual-time-budget=8000 \
        --dump-dom "file://$HOME/.claude/skills/bowel-prep-generator/dev/calendar-test.en.html" \
        | grep -A40 'CALTEST'
"""
import json
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
PARTIAL = SKILL / "templates" / "partials" / "_personalize.en.html"
OUT = SKILL / "dev" / "calendar-test.en.html"

# Synthetic payload — one event per time-form, plus escaping/folding/clamp
# torture cases. Titles intentionally include markup-ish characters.
EVENTS = {
    "v": 1,
    "events": [
        {"id": "meds_stop", "titleDiscreet": "Pause certain medicines",
         "titleDetailed": "Stop iron, anti-diarrhea medicine",
         "desc": "Stop iron; anti-diarrhea medicine, 7 days before.\nCheck meds.giready.com.",
         "allDay": True, "day": -7},
        {"id": "clears_start", "titleDiscreet": "Clear liquids only — begins",
         "titleDetailed": "Clear liquids only begins — colonoscopy prep",
         "desc": ("After 2:00 PM — clear liquids only. No solid food, no dairy. "
                  "OK: water, apple juice, white grape juice, lemonade, clear soda, "
                  "clear broth, popsicles, plain Jell-O. Nothing red or purple. "
                  "This description is intentionally long enough to require RFC 5545 "
                  "line folding at 75 octets when serialized into the calendar file."),
         "day": -1, "start": "14:00"},
        {"id": "dose1", "titleDiscreet": "Give first dose (evening window)",
         "titleDetailed": "CLENPIQ Dose 1 — drink 1 bottle during this window",
         "desc": "Drink 1 full bottle.", "day": -1, "start": "17:00", "end": "21:00"},
        {"id": "dose2", "titleDiscreet": "Give second dose (morning window)",
         "titleDetailed": "CLENPIQ Dose 2 — 5-9 hours before procedure",
         "desc": "Drink the second bottle.", "offsetMin": -540, "offsetEndMin": -300},
        {"id": "dose2_clamp", "titleDiscreet": "Clamped window",
         "titleDetailed": "Clamped window (detailed)",
         "desc": "Window clamped by latestEndOffsetMin.",
         "day": 0, "start": "03:00", "end": "08:00", "latestEndOffsetMin": -300},
        {"id": "dose2_invert", "titleDiscreet": "Inverted clamp",
         "titleDetailed": "Inverted clamp (detailed)",
         "desc": "Clamp inverts the window; pinned to last allowed hour.",
         "day": 0, "start": "03:00", "end": "08:00", "latestEndOffsetMin": -420},
        {"id": "npo_clears_stop", "titleDiscreet": "Stop all drinks",
         "titleDetailed": "Stop all clear liquids (2 hours before procedure)",
         "desc": "Stop all clear liquids 2 hours before the procedure.",
         "offsetMin": -120},
        {"id": "arrival", "titleDiscreet": "Arrive for appointment",
         "titleDetailed": "Arrive at the Surgery Center — check-in",
         "desc": "Arrive 1 hour before your scheduled procedure.",
         "offsetMin": -60, "offsetEndMin": 0, "loc": "123 Test St, Carmel, IN"},
        {"id": "procedure", "titleDiscreet": "Appointment",
         "titleDetailed": "Colonoscopy", "desc": "Test facility.",
         "offsetMin": 0, "durationMin": 60, "loc": "123 Test St, Carmel, IN"},
    ],
}

ASSERTIONS = r"""
<pre id="caltest-results" style="white-space:pre-wrap;font:12px monospace;"></pre>
<script>
(function() {
  'use strict';
  var R = [];
  var fails = 0;
  function flush() {
    var pre = document.getElementById('caltest-results');
    if (pre) pre.textContent = R.join('\n');
  }
  function ok(cond, label) {
    if (cond) { R.push('PASS  ' + label); }
    else { fails++; R.push('FAIL  ' + label); }
    flush();
  }
  function sleep(ms) { return new Promise(function(r) { setTimeout(r, ms); }); }
  function rowFor(text) {
    var rows = document.querySelectorAll('#pz-cal .pz-cal-list li');
    for (var i = 0; i < rows.length; i++)
      if (rows[i].textContent.indexOf(text) !== -1) return rows[i];
    return null;
  }

  // Capture the .ics blob without a real download.
  var capturedBlob = null;
  var origCreate = URL.createObjectURL.bind(URL);
  URL.createObjectURL = function(b) { capturedBlob = b; return origCreate(b); };
  HTMLAnchorElement.prototype.click = function() {}; // swallow the download

  function readBlob(b) {
    return new Promise(function(resolve) {
      var fr = new FileReader();
      fr.onload = function() { resolve(fr.result); };
      fr.readAsText(b);
    });
  }

  async function run() {
    // --- DST-spring week: US DST began Sun 2026-03-08; procedure Mon 9 AM.
    location.hash = 'd=2026-03-09&t=0900';
    await sleep(200);

    ok(!!document.getElementById('pz-cal'), 'calendar card renders after personalization');
    ok(!!document.getElementById('pz-a2hs'), 'A2HS card renders');

    var npoRow = rowFor('Stop all drinks');
    ok(npoRow && npoRow.textContent.indexOf('Mon, Mar 9 · 7:00 AM') !== -1,
       'NPO offset -120 min → Mon, Mar 9 · 7:00 AM (got: ' + (npoRow ? npoRow.textContent : 'none') + ')');
    var clearsRow = rowFor('Clear liquids only');
    ok(clearsRow && clearsRow.textContent.indexOf('Sun, Mar 8 · 2:00 PM') !== -1,
       'clears_start wall-clock 2:00 PM survives DST transition day');
    var doseRow = rowFor('Give first dose');
    ok(doseRow && doseRow.textContent.indexOf('5:00 PM–9:00 PM') !== -1,
       'dose-1 window shows a time range');
    var gcal = doseRow && doseRow.querySelector('.pz-cal-gcal');
    ok(gcal && gcal.href.indexOf('calendar.google.com/calendar/render?action=TEMPLATE') !== -1
            && gcal.href.indexOf('dates=20260308T170000%2F20260308T210000') === -1
            && gcal.href.indexOf('dates=20260308T170000/20260308T210000') !== -1,
       'Google link uses floating local dates param');

    // Detailed-titles toggle re-renders rows + links.
    var cb = document.getElementById('pz-cal-detailed');
    cb.checked = true;
    cb.dispatchEvent(new Event('change'));
    await sleep(50);
    ok(!!rowFor('CLENPIQ Dose 1'), 'toggle switches to detailed titles');
    var gcal2 = rowFor('CLENPIQ Dose 1').querySelector('.pz-cal-gcal');
    ok(gcal2.href.indexOf('CLENPIQ') !== -1, 'Google link follows the toggle');
    cb.checked = false;
    cb.dispatchEvent(new Event('change'));
    await sleep(50);

    // --- .ics content
    document.getElementById('pz-cal-dl').click();
    await sleep(100);
    ok(!!capturedBlob, '.ics blob produced');
    ok(capturedBlob && capturedBlob.type.indexOf('text/calendar') === 0, 'blob MIME is text/calendar');
    var ics = capturedBlob ? await readBlob(capturedBlob) : '';

    ok(/\r\n/.test(ics), 'CRLF line endings');
    ok(ics.indexOf('\n') === -1 || !/[^\r]\n/.test(ics), 'no bare LFs');
    var enc = new TextEncoder();
    var lines = ics.split('\r\n');
    var tooLong = lines.filter(function(l) { return enc.encode(l).length > 75; });
    ok(tooLong.length === 0, 'every physical line ≤ 75 octets (long: ' + tooLong.length + ')');
    ok(/\r\n [^\r\n]/.test(ics), 'long description actually folded (continuation line present)');
    ok(ics.indexOf('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nCALSCALE:GREGORIAN\r\nMETHOD:PUBLISH') === 0,
       'calendar header');
    ok(ics.indexOf('TZID') === -1, 'floating time — no TZID anywhere');
    ok(ics.indexOf('DTSTART;VALUE=DATE:20260302') !== -1, 'all-day day -7 → VALUE=DATE 2026-03-02');
    ok(ics.indexOf('DTEND;VALUE=DATE:20260303') !== -1, 'all-day DTEND is next day');
    ok(ics.indexOf('DTSTART:20260308T140000') !== -1, 'clears_start DTSTART wall-clock on DST day');
    ok(ics.indexOf('DTSTART:20260309T070000') !== -1, 'NPO DTSTART 7:00 AM day-of');
    ok(ics.indexOf('DTSTART:20260308T170000') !== -1 && ics.indexOf('DTEND:20260308T210000') !== -1,
       'dose-1 clock window 5-9 PM day -1');
    ok(ics.indexOf('DTSTART:20260309T000000') !== -1 && ics.indexOf('DTEND:20260309T040000') !== -1,
       'dose-2 offset window -540..-300 min → 12:00-4:00 AM');
    // clamp: latest = 9:00 - 5h = 4:00 → end 4:00, start stays 3:00
    ok(ics.indexOf('DTSTART:20260309T030000\r\nDTEND:20260309T040000') !== -1,
       'clamped window end pulled to latest-allowed (4:00 AM)');
    // inverted clamp: latest = 2:00 < start 3:00 → pinned [1:00, 2:00]
    ok(ics.indexOf('DTSTART:20260309T010000\r\nDTEND:20260309T020000') !== -1,
       'inverted clamp pinned to last allowed hour');
    ok(/UID:[0-9a-f]{32}@giready\.com/.test(ics), 'random hex UID format');
    var uids = ics.match(/UID:[0-9a-f]{32}@giready\.com/g) || [];
    ok(uids.length === 9 && new Set(uids).size === 9, 'all 9 UIDs present and unique');
    var unfolded = ics.replace(/\r\n /g, '');
    ok(unfolded.indexOf('Stop iron\\; anti-diarrhea medicine\\, 7 days before.\\nCheck meds.giready.com.') !== -1,
       'description escaping: \\; \\, \\n');
    ok(unfolded.indexOf('LOCATION:123 Test St\\, Carmel\\, IN') !== -1, 'LOCATION escaped');
    ok(/TRIGGER:-PT1H/.test(ics), 'timed VALARM -PT1H');
    ok(/TRIGGER:PT9H/.test(ics), 'all-day VALARM PT9H (9 AM same day)');
    ok((ics.match(/BEGIN:VALARM/g) || []).length === 9, 'one VALARM per event');
    ok(unfolded.indexOf('SUMMARY:Pause certain medicines') !== -1, 'default titles are discreet');
    ok((unfolded.match(/DESCRIPTION:[^\r\n]*Full instructions: /g) || []).length >= 9,
       'descriptions carry the page link');

    // --- DST-fall week: US DST ended Sun 2026-11-01; procedure Mon 5 PM.
    location.hash = 'd=2026-11-02&t=1700';
    await sleep(200);
    capturedBlob = null;
    document.getElementById('pz-cal-dl').click();
    await sleep(100);
    var ics2 = capturedBlob ? await readBlob(capturedBlob) : '';
    ok(ics2.indexOf('DTSTART:20261101T140000') !== -1,
       'clears_start wall-clock 2:00 PM on DST fall-back day');
    ok(ics2.indexOf('DTSTART:20261102T150000') !== -1, 'NPO 3:00 PM (procedure 5 PM)');

    // --- Clear resets the cards.
    history.replaceState(null, '', location.pathname);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    await sleep(100);
    ok(!document.getElementById('pz-cal'), 'Clear removes the calendar card');
    ok(!document.getElementById('pz-a2hs'), 'Clear removes the A2HS card');

    var header = 'CALTEST ' + (fails ? 'FAIL (' + fails + ')' : 'PASS') +
                 ' — ' + R.length + ' assertions';
    document.title = header;
    document.getElementById('caltest-results').textContent = header + '\n' + R.join('\n');
  }
  function start() {
    run().catch(function(e) {
      R.push('ERROR ' + (e && e.stack ? e.stack : e));
      fails++;
      flush();
      document.title = 'CALTEST FAIL (error)';
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
</script>
"""


def main():
    partial = PARTIAL.read_text(encoding="utf-8")
    payload = json.dumps(EVENTS, ensure_ascii=False,
                         separators=(",", ":")).replace("</", "<\\/")
    assert "{{PZ_EVENTS_JSON}}" in partial
    partial = partial.replace("{{PZ_EVENTS_JSON}}", payload)
    leftover = [t for t in ("{{",) if t in partial]
    if leftover:
        import re
        raise SystemExit(f"unresolved tokens in partial: "
                         f"{sorted(set(__import__('re').findall(r'{{[A-Z_]+}}', partial)))}")
    page = ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>calendar-test</title></head>\n<body>\n"
            "<div class=\"container\">\n  <div class=\"location\">Test location</div>\n"
            + partial + "\n</div>\n" + ASSERTIONS + "</body></html>\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
