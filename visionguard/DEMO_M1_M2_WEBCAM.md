# Demo: Module 1 (vision) + Module 2 (rules) live on a webcam

Three windows, no UI needed: the **M1 camera window** (what the model sees), the **M2 rules engine**, and a readable
**event viewer** (what the rules decided). For the full stack in the dashboard see `MANUAL_TESTING.md`.

---

## 1. Theory (what to say)

### The one-line story
**M1 sees, M2 decides.** M1 turns pixels into facts ("person T3 at x,y; a `no_helmet` box at 0.81"). M2 turns facts
into safety events using written rules ("T3 has had no helmet in 8 of the last 10 frames inside the welding zone").
Keeping them apart means rules change without retraining the model, and every event can be explained and replayed.

### Module 1: vision pipeline
```
Webcam ─► reader thread ─► small queue (drops the OLDEST frame if behind) ─► worker thread
                                                                             ├─ YOLO11n (10 classes)
                                                                             ├─ ByteTrack (anonymous IDs)
                                                                             ├─ 30 s frame buffer (evidence)
                                                                             └─ TrackResult ► JSONL / API / metrics
```
- **Sampling:** the camera runs at 30 fps but M1 analyses 5 frames per second (`target_fps: 5`): enough for safety
  events, and it lets one machine serve many cameras. A slow model never builds a backlog: old frames are dropped.
- **Detection:** one YOLO11n model from Milestone 0, 10 classes (person, helmet, no_helmet, vest, gloves, no_gloves,
  goggles, mask, fire, smoke). Default confidence 0.30; fire and smoke 0.35, tuned on validation data.
- **Tracking:** ByteTrack links boxes across frames, so a person keeps one ID (T1, T2...) while moving. IDs are
  anonymous: no face recognition, no names.
- **Evidence buffer:** the last 30 s of frames stay in memory, so later modules can attach the exact frames that proved
  a violation.
- **Output contract:** one `TrackResult` per frame: `tracks` (people), `ppe_detections`, `hazard_detections`, with
  model version and inference time. Everything downstream reads only this.

### Module 2: rule engine
For every frame, for every person, for every rule in the person's zone:
1. **Associate** PPE boxes with a person by body region (helmet: top third of the person box; gloves: hands area;
   vest: torso).
2. **Observe** one of three things:
   - `PRESENT` - the item is detected on the person;
   - `ABSENT` - an explicit `no_helmet`/`no_gloves` box (**explicit**), or nothing found on a clearly visible person
     (**inferred**, only for big items: helmet, vest);
   - `UNKNOWN` - cannot tell: person too small or too new, low confidence, head cut off at the top, torso cut off at the
     bottom. Unknown never raises or clears anything: **the system does not guess**.
3. **Confirm over time (N of M):** 8 of the last 10 frames for PPE (about 2 s at 5 fps), 3 of 5 for fire/smoke
   (under 1 s, because a fire is more urgent). One bad frame never raises an alarm.
4. **One event per episode:** the episode closes after 15 compliant frames (3 s); a cooldown stops repeats; a spatial
   de-dup stops a tracker ID switch from raising a second incident for the same person.
5. **Flag for review:** confidence under 0.70 or mostly inferred evidence → `needs_review: true` (a human decides).
   Every fire/smoke event is reviewed.
6. **Event contract:** a deterministic `event_id` (the same input always gives the same event, so M3 can drop
   duplicates), the rule and zone, missing PPE or hazard class, the evidence frame IDs, and how it was decided.

Talking points: zones are polygons per camera (`configs/rules.*.yaml`); rules are configuration, not code; the engine is
deterministic (replaying recorded M1 output gives identical events, which makes audits and tests possible).

---

## 2. Before the demo (once)

- **Camera permission:** run any M1 webcam command once from the terminal app you will present from (Terminal, iTerm
  or VS Code). macOS asks for camera access: allow it, quit and reopen that terminal.
- **Free the camera and files:** if the full stack is running, `~/Desktop/VisionAIProject/visionguard/scripts/dev_stack.sh stop`.
  Close other apps using the camera (Zoom, Teams, FaceTime).
- **Laptop plugged in**, lid open. Good front lighting (a window behind you makes you a silhouette).
- **Props:** a phone with a **fire video** and a **smoke video** ready full screen at full brightness (any stock clip of
  flames; a burning log or building works better than a candle); a hard hat if you have one; bare hands.
- **Rehearse once** with the clip (section 5) and once with the webcam.

---

## 3. Bring it up (three terminals)

Paste in **every** terminal first:
```bash
export VG=~/Desktop/VisionAIProject/visionguard PY=~/Desktop/VisionAIProject/visionguard/services/vision_pipeline/.venv/bin/python
```
**Terminal 1 - event viewer** (readable, one line per event; waits for M2):
```bash
$PY $VG/scripts/show_events.py
```
**Terminal 2 - Module 2** (demo rules: same thresholds, short cooldowns of 10 s for PPE and 20 s for fire/smoke):
```bash
cd $VG/services/rule_engine && rm -f out/events.jsonl ../vision_pipeline/out/results/CAM_001.jsonl
$PY -m rule_engine --config configs/rules.combined.demo.yaml --input ../vision_pipeline/out/results/CAM_001.jsonl --output out/events.jsonl --follow
```
**Terminal 3 - Module 1** (opens the camera window; press `q` in the window to stop):
```bash
cd $VG/services/vision_pipeline && $PY -m vision_pipeline --config configs/pipeline.combined.yaml --show
```
Screen layout: camera window large on the left, viewer (terminal 1) on the right, terminal 2 small or hidden (it prints
raw JSON, which is the actual contract to Module 3 - show it once). Stop: `q` in the window, then Ctrl+C in terminals 1-2.

---

## 4. Demo script (about 8 minutes)

| # | You do | Audience sees | You say |
|---|---|---|---|
| 1 | Sit 1.5-2 m back so your upper body **and some space above your head** are in view | `person` box with an ID (e.g. `1 person 0.93`), maybe `no_helmet` on your head | "M1 found a person and gave them an anonymous ID. Every box has a class and a confidence." |
| 2 | Move left and right | The ID stays the same | "That is tracking: the rules follow a person over time, not single frames." |
| 3 | Stay without a helmet ~2 s | Viewer: `PPE missing helmet person T1 conf 0.7x explicit 8/10 frames` | "Not one frame: 8 of the last 10. Explicit means the model saw a bare head; inferred would mean it simply saw no helmet." |
| 4 | Lean forward until your head touches the **top** of the frame | No new event | "Head cut off: the engine says UNKNOWN instead of guessing. No false alarm." |
| 5 | Put on a hard hat (if you have one) for 3+ s, take it off, wait | `helmet` box; after the cooldown a **new** event | "Wearing it for 3 s closes the episode; taking it off starts a new one. Cooldowns stop alert spam (10 s here, 60 s in production)." |
| 6 | Hold the phone with the **fire video** to the camera, 2-3 s | Red `fire 0.4x` box; viewer: red `HAZARD FIRE ... 3/5 frames ... needs review` | "Fire needs only 3 of 5 frames, because it is more urgent. It is always flagged for a human to confirm." |
| 7 | Same with the **smoke video** (wait 20 s after the fire event) | `HAZARD SMOKE` | "Smoke is the hardest class: thin or distant smoke is missed and clouds can look like smoke. That is why every hazard is reviewed." |
| 8 | Hold bare hands up near your chest, then put on gloves | Possible `no_gloves` box; event only if confident (>= 0.50) | "Gloves are small: the rule only fires on an explicit 'no gloves' detection, never on 'I didn't see gloves'." |
| 9 | Stand up and step back until your **whole body** is visible | `missing vest` event if you wear no vest | "Vest absence is only inferred when the whole torso is visible. Sitting at a laptop, it stays UNKNOWN." |
| 10 | In terminal 2 (or `tail -1 out/events.jsonl`), show one raw event | The JSON event | "This is the contract to Module 3: deterministic ID, rule, zone, evidence frame IDs, how it was decided." |

Show one raw event nicely:
```bash
tail -1 $VG/services/rule_engine/out/events.jsonl | $PY -m json.tool
```

---

## 5. Fallback: the recorded clip (no webcam)
Use it to rehearse, or if the camera or lighting lets you down. In terminal 3 run instead:
```bash
cd $VG/services/vision_pipeline && $PY -m vision_pipeline --config configs/pipeline.combined.clip.yaml --show
```
36 s, real time: fire (0-9 s), smoke (9-18 s), workers without helmets (18-24 s), workers with helmets and vests
(24-30 s), no hazard (30-36 s). Rehearsal output (2026-11-13): FIRE, SMOKE, 3 explicit and 1 inferred missing
helmet, 6 inferred missing vest (full-body workers without vests); the compliant workers raise nothing; with the
20 s demo cooldown a storm cloud at 30-33 s raises a second SMOKE event (a known false alarm worth showing).
Clear old output between runs: `rm -f $VG/services/rule_engine/out/events.jsonl $VG/services/vision_pipeline/out/results/CAM_001.jsonl`.

---

## 6. If something goes wrong
| Problem | Fix |
|---|---|
| `cannot open 0` / black window | Camera permission for this terminal app (System Settings → Privacy & Security → Camera), reopen the terminal; close Zoom/Teams. |
| Boxes but no events in the viewer | Is M2 (terminal 2) running and was it started **before** a stale `CAM_001.jsonl` was removed? Restart M2 after the `rm -f`. |
| No helmet event | Your head touches the top of the frame (UNKNOWN), you are too small in the frame (< 15 % of its height), or the cooldown (10 s) is running. |
| Fire not detected | Brighter phone, fuller flames, closer to the lens; screen glare lowers confidence. The clip always works. |
| Old events appear at start | Delete `out/events.jsonl` and `CAM_001.jsonl` before starting (the `rm -f` line). |
| Slow window | Normal on CPU; `pipeline.combined.yaml` uses the Mac GPU automatically. Close other heavy apps. |

## 7. Honest limits to mention
The model is trained on public datasets, not your site's cameras; a laptop webcam indoors is a different view from a
ceiling CCTV camera. Gloves, goggles and smoke are the weakest classes. M1 + M2 decide nothing irreversible: Module 3
turns events into incidents with evidence, and a person reviews them.
