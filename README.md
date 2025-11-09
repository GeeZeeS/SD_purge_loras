# comfy-purge-loras

A ComfyUI custom node that:
1) Checks filesystem usage for the partition backing your LoRAs folder.
2) If used% is **≥ threshold** (default 90%), it deletes **oldest LoRAs first** until usage drops **below target%** (default 85%).
3) Clears `$HOME/.local/share/Trash/files` after purge (safe `shutil.rmtree`, no `rm -rf`).

## Install

### Via git clone (recommended)
```bash
cd /path/to/ComfyUI/custom_nodes
git clone <YOUR_GIT_URL> comfy-purge-loras
# restart ComfyUI
