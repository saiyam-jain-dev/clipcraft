# ClipCraft Kaggle GPU worker

The worker can render clips and, when enabled, host a small open LLM behind an OpenAI-compatible endpoint. Kaggle GPU availability and speed vary by session; this does not promise a fixed processing time.

## Free Kaggle LLM ranker

The ranker uses **Qwen2.5 7B Instruct**, loaded in 4-bit mode on the notebook GPU. The model reads ClipCraft's short candidate list and returns JSON; it does not transcribe or inspect the full video. Its model card describes structured output and uses the Apache 2.0 license: [Qwen2.5 7B Instruct](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct).

### Start it

1. Open `clipcraft_llm_setup.ipynb` as a Kaggle notebook. It is self-contained and does not need a Dataset upload.
2. In Notebook Settings, enable **GPU** and **Internet**.
3. Run the cells top to bottom. The first run installs packages and downloads the model. Wait for `Model loaded` and the `trycloudflare.com` URL.
4. In ClipCraft → Settings, select **OpenAI-compatible**, then set:
   - **Base URL:** `https://<your-tunnel>.trycloudflare.com/v1`
   - **API Key:** the key printed by the notebook
   - **Model:** `Qwen/Qwen2.5-7B-Instruct`
5. Save. Keep the Kaggle notebook session running while ClipCraft uses the ranker. If the session restarts, use its new URL and key.

The notebook's 30 GPU-hours are a usage allowance, not guaranteed always-on uptime. Kaggle can queue GPU sessions, and a cold start includes model download/load time. The model endpoint is protected with a random bearer key; treat the key like a password.

ClipCraft currently gives LLM ranking an 18-second response budget. A warm Kaggle model may fit; if it does not, ClipCraft falls back to its local ranker. We need a real Kaggle run to measure the response time on your available GPU.

The existing `kaggle_clipcraft.ipynb` is a legacy renderer prototype. `clipcraft_llm_setup.ipynb` runs the LLM endpoint directly. `kaggle_worker.py` remains available for the separate remote rendering service.
