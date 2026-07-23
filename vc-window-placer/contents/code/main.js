/*
 * vc-window-placer/contents/code/main.js
 *
 * Voice Commander KWin helper script.
 *
 * Voice Commander writes a comma-separated queue of placement entries into
 * kwinrc under [Script-vc-window-placer] nextScreen, then triggers a reload
 * via the org.kde.kwin.Scripting DBus interface (unloadScript / loadScript /
 * start). loadScript re-reads kwinrc from disk and creates a fresh JS engine,
 * which re-executes this file top to bottom and re-reads the queue.
 *
 * (org.kde.KWin.reconfigure does NOT re-execute user scripts in current
 * Plasma 6 -- only the Scripting interface does.)
 *
 * Queue entries are "output:wm_class_hint" pairs, e.g.
 *   "DP-2:waterfox-g,HDMI-A-1:dolphin"
 *
 * On windowAdded, the handler walks the queue and matches an arriving window
 * to the first entry whose tag substring-matches the window's resourceClass
 * (bidirectional). An empty tag matches anything (preserves head-of-queue
 * behavior for actions with no class hint). If no entry matches, the window
 * is left alone -- we don't steal slots for windows the user didn't ask for.
 *
 * Why match by class instead of order-of-arrival: windowAdded fires when each
 * app *maps* its window, which is fastest-app-wins. The user-intended order
 * (launch order on the Python side) is different from arrival order whenever
 * one of the apps is slower to map than another. Tagging removes the race.
 */

"use strict";

// _queue is re-initialized on every script re-execution. That's exactly
// what we want: Python writes kwinrc -> calls loadScript -> KWin re-runs
// this script -> fresh queue from kwinrc.
var _queue = [];

var raw = readConfig("nextScreen", "").trim();
if (raw) {
    _queue = raw.split(",").map(function(s) {
        var parts = s.trim().split(":");
        return {
            output: (parts[0] || "").trim(),
            tag:    (parts[1] || "").trim().toLowerCase()
        };
    }).filter(function(e) { return e.output; });
}
print("[vcw-window-placer] Queue loaded: " + JSON.stringify(_queue));

// One-shot: move the CURRENTLY ACTIVE window to a named output. Voice Commander
// writes [Script-vc-window-placer] moveActive=<output> and reloads this script;
// "move to {alias}" uses this instead of KWin's numeric "Window to Screen N"
// shortcut. Resolving the screen by NAME against workspace.screens means it
// survives KWin's screen numbering and monitor hotplugs. Runs immediately on
// reload (not on windowAdded) because the target window already exists.
var moveActive = readConfig("moveActive", "").trim();
if (moveActive) {
    var active = workspace.activeWindow;
    var dest = workspace.screens.find(function(screen) {
        return screen.name === moveActive;
    });
    if (active && dest) {
        print("[vcw-window-placer] moveActive: '" + active.caption +
              "' -> " + moveActive);
        workspace.sendClientToScreen(active, dest);
    } else {
        print("[vcw-window-placer] moveActive='" + moveActive + "' ignored: " +
              (active ? "no screen named that" : "no active window"));
    }
}

// Register the windowAdded handler ONCE across the lifetime of this script's
// JS engine instance. Defensive guard: each loadScript invocation creates a
// fresh engine, so this always passes -- but the guard costs nothing and
// protects against future KWin changes that might preserve globals.
if (typeof _handlerRegistered === "undefined") {
    var _handlerRegistered = true;

    workspace.windowAdded.connect(function(window) {
        if (!window.normalWindow) {
            return;
        }
        if (_queue.length === 0) {
            return;
        }

        var cls = (window.resourceClass || "").toLowerCase();

        // Find the first queue entry whose tag matches this window's class.
        // Bidirectional substring: handles "waterfox-g" tag vs "waterfox" class
        // and "dolphin" tag vs "org.kde.dolphin" class.
        // An empty tag matches anything (untagged entries are placement-only).
        var matchIdx = -1;
        for (var i = 0; i < _queue.length; i++) {
            var tag = _queue[i].tag;
            if (!tag) {
                matchIdx = i;
                break;
            }
            if (cls && (cls.indexOf(tag) !== -1 || tag.indexOf(cls) !== -1)) {
                matchIdx = i;
                break;
            }
        }

        if (matchIdx === -1) {
            print("[vcw-window-placer] windowAdded: '" + window.caption +
                  "' resourceClass='" + cls +
                  "' -- no queue entry matched, ignoring");
            return;
        }

        var entry = _queue.splice(matchIdx, 1)[0];
        print("[vcw-window-placer] windowAdded: '" + window.caption +
              "' resourceClass='" + cls +
              "' matched tag='" + entry.tag +
              "' -> placing on " + entry.output +
              " (" + _queue.length + " remaining in queue)");

        var target = workspace.screens.find(function(screen) {
            return screen.name === entry.output;
        });

        if (!target) {
            print("[vcw-window-placer] No screen found with name: " + entry.output);
            return;
        }

        // Move now. sendClientToScreen SILENTLY NO-OPS when the window is
        // still too early in its initial setup (confirmed by instrumentation:
        // on failing runs, window.output is unchanged immediately after the
        // call, with no later outputChanged — KWin never overrides us, the
        // call itself just doesn't take). Whether it takes is checkable
        // synchronously via window.output, so: move, check, and if it didn't
        // stick, re-assert on the window's own frameGeometryChanged signal,
        // which fires as the window finishes its initial configure. Fully
        // event-driven — no timers, and the common case (call takes) costs
        // nothing extra.
        var want = entry.output;
        workspace.sendClientToScreen(window, target);
        if (window.output && window.output.name === want) {
            return; // took immediately -- the fast path, and we're done
        }

        // Bounded re-assert: correct ONLY the initial placement, then get out
        // of the way. The attempt cap means that even if the output can never
        // be reached (e.g. unplugged mid-flight), we stop long before a user
        // could be dragging the window around and find us fighting them.
        var attempts = 0;
        var MAX_ATTEMPTS = 20;
        var reassert = function() {
            attempts++;
            var cur = window.output ? window.output.name : null;
            if (cur !== want && attempts <= MAX_ATTEMPTS) {
                workspace.sendClientToScreen(window, target);
                cur = window.output ? window.output.name : null;
            }
            if (cur === want || attempts >= MAX_ATTEMPTS) {
                window.frameGeometryChanged.disconnect(reassert);
                print("[vcw-window-placer] re-assert " +
                      (cur === want ? "landed on " + want : "gave up") +
                      " after " + attempts + " geometry change(s)");
            }
        };
        window.frameGeometryChanged.connect(reassert);
    });

    print("[vcw-window-placer] windowAdded handler registered");
}
