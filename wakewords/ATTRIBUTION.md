# Vendored wake word models

Wake models are drop-in openWakeWord `.onnx` files. `install.sh` copies the
ones in this directory into `DATA_DIR/wakewords/` at install time; users can
add their own alongside them and select one via the `wake_model` config key.

We vendor (rather than download-at-install) so a fresh install is hermetic —
no dependency on a third party's repo staying online, unmoved, and unchanged.

## computer_v2.onnx

- **Word:** "computer"
- **Source:** https://github.com/fwartner/home-assistant-wakewords-collection
  (`en/computer/computer_v2.onnx`)
- **SHA-256:** `5e8157e8a0b2bbf4708452ccf8d0dc3397b6e81fba4dad097a8ba9192894ab3a`
- **License:** MIT (see below)

This is the community-trained model Voice Commander has shipped since its
whisper rebuild. It is stage one of a two-stage wake gate: openWakeWord acks
fast, then whisper's transcript confirms the wake (see `core/wake.py`), so the
model's job is recall + latency, not final precision.

### MIT License

```
MIT License

Copyright (c) 2023 Florian Wartner

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
