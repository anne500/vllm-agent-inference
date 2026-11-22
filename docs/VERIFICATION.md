# 🧪 Project 12 Verification

## ✅ GPU and agent checks

| Check | Recorded result |
|---|---|
| GPU | NVIDIA Tesla T4, 15,360 MiB VRAM |
| Runtime | Python 3.12.3, vLLM 0.12.0, PyTorch 2.9.0 with CUDA 12.9 |
| CUDA calculation | `2.0` returned from a GPU tensor operation |
| Server | Qwen/Qwen2.5-0.5B-Instruct on loopback port 8000, FP16, 2,048-token context, eager execution |
| Client | OpenAI Python client received a generated reply from the vLLM server |

**Smoke test prompt:** In one sentence, explain what a CI smoke test checks.

**Observed reply:** “A CI smoke test verifies that the software or application meets predefined requirements and functionality before it is deployed to production.”

The reply verifies the request and response path. A smoke test typically checks that critical functions work after a build or deployment.

## 🔧 Startup fix

Initial startup ended with `FileNotFoundError: [Errno 2] No such file or directory: 'ninja'` during FlashInfer compilation. Ninja was installed inside the virtual environment. The corrected launch adds `/content/vllm-env/bin` to the server process’s `PATH` so its children can locate the executable. The agent subsequently received a reply.

## 📊 Benchmark method

- Four short CI prompts, each sent once as warm-up and then repeated eight times per measured setting.
- One measured pass at each concurrency level, in ascending order: 1, 4, and 8.
- Each HTTP request used temperature 0, up to 64 output tokens, and a 60-second Requests timeout.
- Throughput divides successful HTTP requests by total batch wall time. Latency measures client-side elapsed time per successful HTTP request. p95 uses the nearest-rank method.
- The 2 ms difference in observed p95 between 4 and 8 clients is too small to interpret from one pass of 32 requests per setting. These are single-runtime measurements with a small model and short prompts.

[Recorded benchmark rows](benchmark-results.csv)

## 📈 Extension 2 · Repeatable streaming

Recomputed throughput and latency summaries from all 288 successful request records across nine trials. [Verified streaming results](STREAMING_RESULTS.md)

## 🟣 Extension 3 · Prefix caching

Verified both cache modes in the startup logs and recomputed timing summaries from 48 successful requests. [Verified prefix caching results](PREFIX_CACHE_RESULTS.md)

## 🚦 Extension 4 · Capacity and queue monitoring

Recomputed latency percentiles and throughput from all 192 requests, verified the submitted arrival rates, and checked all 157 queue samples. [Verified capacity results](CAPACITY_RESULTS.md)
