/*
 * vc-window-placer/contents/code/main.js
 *
 * Voice Commander KWin helper script.
 *
 * Voice Commander writes a comma-separated queue of target output names into
 * kwinrc under [Script-vc-window-placer] nextScreen, then calls KWin
 * reconfigure.  This script reads that queue on reconfigure and consumes one
 * entry per windowAdded event.
 */

"use strict";

var _queue = [];

function _reload() {
    var raw = readConfig("nextScreen", "").trim();
    if (raw) {
        _queue = raw.split(",").map(function(s) { return s.trim(); }).filter(Boolean);
        print("[vc-window-placer] Queue loaded: " + JSON.stringify(_queue));
    }
}

// Read initial config on script load.
_reload();

// Re-read config whenever KWin reconfigures (Python triggers this via dbus).
workspace.configChanged.connect(_reload);

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
