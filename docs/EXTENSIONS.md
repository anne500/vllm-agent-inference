# 🧪 Project 12 · Extensions 2–5

The [baseline notebook and results](../README.md#results) cover the original concurrency benchmark. Extension 2 is [verified on a Colab Tesla T4](STREAMING_RESULTS.md). Extension 3 is [verified on the same GPU type](PREFIX_CACHE_RESULTS.md). Extension 4 is [verified on a Colab Tesla T4](CAPACITY_RESULTS.md). GPU measurements for Extension 5 are pending.

## 🚀 Run in Colab

Open the [extensions notebook in Colab](https://colab.research.google.com/github/anne500/vllm-agent-inference/blob/main/project12_vllm_extensions.ipynb). Use a fresh **T4 GPU** runtime. Run setup once and execute the four experiment cells separately. The runner keeps all API calls on port 8000 inside the runtime and shuts down each server when its experiment finishes.

Run the export cell to download the JSON observations and logs. Check the Colab output for each “Saved” line. A server startup failure prints a log excerpt and retains the full server log in the output directory.

## 📊 Experiment protocols

| Extension | Protocol | Evidence to inspect |
|---|---|---|
| **2 · Streaming benchmark** | Three trials at each concurrency level of 1, 4, and 8. Four CI prompts, 32 calls per trial, with cyclic ordering to vary which concurrency runs first. | \`streaming.json\` holds every request, per-trial rates, median and p95 time to first text, and total latency. The client times the first streamed text chunk, which can contain multiple tokens. |
| **3 · Prefix caching** | Serve the same model with caching explicitly disabled and then enabled. Warm each server with the shared policy and replay the same 24 requests at concurrency 1. | \`prefix.json\` holds latency and cache counter deltas where exposed. Compare time to first text and inspect cache hits before attributing a change to caching. The two arms run once each. |
| **4 · Capacity monitoring** | Schedule 48 arrivals each at 2, 6, 12, and 18 requests/sec. Sample running and waiting requests about every 250 ms. | \`capacity.json\` holds submit times, achieved successful throughput, p95 total latency, and queue samples. The demo uses an illustrative 1,500 ms p95 target and requires zero request failures and available queue samples. Report the tested rates if no crossing appears. |
| **5 · BRI triage** | At concurrency 1 and 4, run 16 workflows that ask the model to extract fields from a synthetic CI log and draft a sentence from the parsed record. | \`bri.json\` holds workflow latency, throughput, exact extraction counts, and draft field-match counts. The deterministic draft check looks for all expected fields and rejects a conflicting PASS/FAIL marker. Review the drafts for unsupported claims beyond those checks. |

All serving experiments use vLLM 0.12.0, Qwen2.5-0.5B-Instruct, FP16, eager execution, and a 2,048-token context. Prefix caching is disabled outside Extension 3. Use the new measurements for comparisons within each extension. The baseline used a non-streaming client and one pass per concurrency level.

## 📚 References

- [vLLM 0.12.0 prefix caching](https://docs.vllm.ai/en/v0.12.0/design/prefix_caching/)
- [vLLM 0.12.0 serving metrics](https://docs.vllm.ai/en/v0.12.0/design/metrics/)
