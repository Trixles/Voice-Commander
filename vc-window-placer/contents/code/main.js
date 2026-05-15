/*
 * vc-window-placer/contents/code/main.js
 *
 * Voice Commander KWin helper script.
 *
 * Voice Commander writes a comma-separated queue of target output names into
 * kwinrc under [Script-vc-window-placer] nextScreen, then triggers a reload
 * via the org.kde.kwin.Scripting DBus interface (unloadScript / loadScript /
 * start). loadScript re-reads kwinrc from disk and creates a fresh JS engine,
 * which re-executes this file top to bottom and re-reads the queue.
 *
 * (org.kde.KWin.reconfigure does NOT re-execute user scripts in current
 * Plasma 6 -- only the Scripting interface does.)
 *
 * The _handlerRegistered guard below is inert in practice (each loadScript
 * gets a fresh engine, so the guard always passes) but kept as a defensive
 * no-op in case KWin's behavior changes.
 */

"use strict";

// _queue is re-initialized on every script re-execution. That's exactly
// what we want: Python writes kwinrc -> calls loadScript -> KWin re-runs
// this script -> fresh queue from kwinrc.
var _queue = [];

var raw = readConfig("nextScreen", "").trim();
_queue = raw ? raw.split(",").map(function(s) { return s.trim(); }).filter(Boolean) : [];
print("[vc-window-placer] Queue loaded: " + JSON.stringify(_queue));

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

        var outputName = _queue.shift();
        print("[vc-window-placer] windowAdded: '" + window.caption +
              "' -> placing on " + outputName +
              " (" + _queue.length + " remaining in queue)");

        var target = workspace.screens.find(function(screen) {
            return screen.name === outputName;
        });

        if (!target) {
            print("[vc-window-placer] No screen found with name: " + outputName);
            return;
        }

        workspace.sendClientToScreen(window, target);
    });

    print("[vc-window-placer] windowAdded handler registered");
}
