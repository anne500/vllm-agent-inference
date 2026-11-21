"""Run Project 12's serving experiments in an isolated vLLM environment."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from math import ceil
import os
from pathlib import Path
import re
from statistics import median
import subprocess
import sys
from threading import Event, Thread
import time

import requests


BASE_URL = "http://127.0.0.1:8000"
MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
PROMPTS = [
    "In one sentence, explain what a CI smoke test checks.",
    "In one sentence, explain what a build artifact is.",
    "In one sentence, explain why CI records test results.",
    "In one sentence, explain what a release approval checks.",
]
CONCURRENCY = (1, 4, 8)


def chat(messages, *, stream, max_tokens):
    started = time.perf_counter()
    first = None
    try:
        with requests.post(
            f"{BASE_URL}/v1/chat/completions",
            json={
                "model": MODEL, "messages": messages, "temperature": 0,
                "max_tokens": max_tokens, "stream": stream,
            },
            stream=stream, timeout=(5, 60),
        ) as response:
            response.raise_for_status()
            if stream:
                content = []
                finished = False
                for line in response.iter_lines(chunk_size=1):
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        finished = True
                        break
                    choices = json.loads(data)["choices"]
                    if choices and (piece := choices[0]["delta"].get("content")):
                        if first is None:
                            first = time.perf_counter()
                        content.append(piece)
                if not finished:
                    raise ValueError("Stream closed before [DONE]")
                answer = "".join(content)
            else:
                answer = response.json()["choices"][0]["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("Empty model response")
            return {
                "latency_ms": (time.perf_counter() - started) * 1000,
                "ttft_ms": (first - started) * 1000 if first is not None else None,
                "answer": answer,
                "error": None,
            }
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as error:
        return {"latency_ms": (time.perf_counter() - started) * 1000,
                "ttft_ms": None, "answer": None, "error": str(error)}


def prompt_request(prompt):
    return chat([{"role": "user", "content": prompt}], stream=True, max_tokens=64)


def summarize(rows, seconds):
    successes = [row for row in rows if row["error"] is None]
    summary = {"ok": len(successes), "failed": len(rows) - len(successes),
               "wall_seconds": seconds, "requests_per_second": len(successes) / seconds}
    for metric in ("latency_ms", "ttft_ms"):
        values = sorted(row[metric] for row in successes if row[metric] is not None)
        if values:
            summary[f"median_{metric}"] = median(values)
            summary[f"p95_{metric}"] = values[ceil(.95 * len(values)) - 1]
    return summary


def pooled(workload, concurrency, worker):
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        rows = list(pool.map(worker, workload))
    return {"summary": summarize(rows, time.perf_counter() - started), "requests": rows}


def warmup(prompts):
    for prompt in prompts:
        result = prompt_request(prompt)
        if result["error"] is not None:
            raise RuntimeError(f"Warm-up failed: {result['error']}")


def streaming():
    warmup(PROMPTS)
    runs = []
    for repeat in range(3):
        order = CONCURRENCY[repeat:] + CONCURRENCY[:repeat]
        for concurrency in order:
            print(f"Streaming trial {repeat + 1}, concurrency {concurrency}", flush=True)
            runs.append({"trial": repeat + 1, "concurrency": concurrency,
                         **pooled(PROMPTS * 8, concurrency, prompt_request)})
    return {"workload": PROMPTS, "runs": runs}


def metrics():
    response = requests.get(f"{BASE_URL}/metrics", timeout=2)
    response.raise_for_status()
    totals = {}
    for line in response.text.splitlines():
        if not line.startswith("vllm:"):
            continue
        match = re.match(r"^([^\s{]+)(?:\{.*\})?\s+(\S+)", line)
        if match:
            name, value = match.groups()
            totals[name] = totals.get(name, 0.0) + float(value)
    return totals


def cache_counter(values, name):
    return values.get(f"vllm:prefix_cache_{name}_total", values.get(f"vllm:prefix_cache_{name}"))


def counter_delta(before, after):
    deltas = {}
    for name in ("hits", "queries"):
        initial, final = cache_counter(before, name), cache_counter(after, name)
        deltas[name] = final - initial if initial is not None and final is not None else None
    return deltas


def prefix_trial(prefix, *, enabled):
    workload = [
        f"{prefix}\nQuestion {index}: In one sentence, explain why a failed test needs review."
        for index in range(1, 25)
    ]
    warmup([f"{prefix}\nWarm-up: Summarize this release policy in one sentence."])
    before = metrics()
    run = pooled(workload, 1, prompt_request)
    after = metrics()
    return {"cache_enabled": enabled, "concurrency": 1, "shared_prefix_chars": len(prefix),
            "cache_counter_deltas": counter_delta(before, after), **run}


def prefix_comparison(output):
    prefix = "\n".join(
        f"Release policy item {index:02}: Record the build identifier, list the check result,"
        " preserve relevant CI evidence, and send unresolved failures for review."
        for index in range(1, 25)
    )
    runs = []
    for enabled in (False, True):
        with server(output, cache=enabled, label=f"prefix-{enabled}"):
            print(f"Prefix caching {'on' if enabled else 'off'}", flush=True)
            runs.append(prefix_trial(prefix, enabled=enabled))
    return {"shared_prefix": prefix, "runs": runs}


def capacity_run(rate, count):
    samples, scrape_errors = [], []
    stop = Event()
    started = time.perf_counter()

    def sample_queue():
        while not stop.is_set():
            try:
                values = metrics()
                samples.append({
                    "elapsed_seconds": time.perf_counter() - started,
                    "waiting": values["vllm:num_requests_waiting"],
                    "running": values.get("vllm:num_requests_running"),
                })
            except (requests.RequestException, KeyError, ValueError) as error:
                scrape_errors.append(str(error))
            stop.wait(.25)

    monitor = Thread(target=sample_queue)
    monitor.start()
    try:
        with ThreadPoolExecutor(max_workers=count) as pool:
            futures = []
            launch_offsets = []
            for index in range(count):
                target = started + index / rate
                time.sleep(max(0, target - time.perf_counter()))
                launch_offsets.append(time.perf_counter() - started)
                futures.append(pool.submit(prompt_request, PROMPTS[index % len(PROMPTS)]))
            rows = [future.result() for future in futures]
        elapsed = time.perf_counter() - started
    finally:
        stop.set()
        monitor.join()

    return {"target_requests_per_second": rate, "requests": rows,
            "submit_offsets_seconds": launch_offsets, "queue_samples": samples,
            "scrape_errors": scrape_errors, "summary": summarize(rows, elapsed)}


def capacity():
    warmup(PROMPTS)
    runs = []
    latency_target_ms = 1500
    for rate in (2, 6, 12, 18):
        print(f"Capacity sweep at {rate} requested arrivals/sec", flush=True)
        run = capacity_run(rate, count=48)
        summary = run["summary"]
        run["meets_target"] = (
            summary["failed"] == 0 and summary["p95_latency_ms"] <= latency_target_ms
            and bool(run["queue_samples"])
        )
        runs.append(run)
    return {"p95_target_ms": latency_target_ms, "runs": runs}


CASES = [
    {"build": "MAPLE-207", "check": "signing", "status": "FAIL",
     "evidence": "certificate expired"},
    {"build": "BIRCH-309", "check": "packaging", "status": "FAIL",
     "evidence": "disk full"},
    {"build": "CEDAR-112", "check": "smoke", "status": "PASS",
     "evidence": "all smoke tests passed"},
    {"build": "ATLAS-418", "check": "authentication", "status": "FAIL",
     "evidence": "token expired"},
]


def contains_fields(answer, expected):
    if not answer:
        return False
    return all(re.search(rf"(?<!\w){re.escape(value)}(?!\w)", answer, re.IGNORECASE)
               for value in expected.values())


def parse_extraction(answer):
    try:
        extracted = json.loads(answer)
        if isinstance(extracted, dict):
            return {key: str(value).strip() for key, value in extracted.items()}
    except (TypeError, ValueError):
        pass
    return {}


def triage(case):
    started = time.perf_counter()
    log = (
        f"build={case['build']}\ncheck={case['check']}\n"
        f"status={case['status']}\nlog={case['evidence']}"
    )
    extraction = chat(
        [{"role": "user", "content":
          "Extract the four fields from this synthetic CI log. Return one JSON object with "
          f"exact keys build, check, status, evidence. Copy log into evidence.\n\n{log}"}],
        stream=False, max_tokens=128,
    )
    extracted = parse_extraction(extraction["answer"])
    if extraction["error"] is None:
        summary = chat(
            [{"role": "user", "content":
              "Write one CI triage sentence using this record. Include the build ID, "
              "check name, literal PASS or FAIL, and exact evidence phrase. "
              f"Report the check result only.\n\nRecord: {json.dumps(extracted)}"}],
            stream=False, max_tokens=96,
        )
    else:
        summary = {"answer": None, "error": extraction["error"]}
    answer = summary["answer"]
    opposite = "FAIL" if case["status"] == "PASS" else "PASS"
    has_opposite = answer and re.search(rf"\b{opposite}\b", answer, re.IGNORECASE)
    return {
        "case": case, "extraction": extraction["answer"], "parsed_extraction": extracted,
        "draft": answer,
        "extraction_exact": extracted == case,
        "draft_matches_fields": contains_fields(answer, case) and not bool(has_opposite),
        "latency_ms": (time.perf_counter() - started) * 1000,
        "ttft_ms": None, "error": extraction["error"] or summary["error"],
    }


def bri():
    warmup(PROMPTS[:1])
    runs = []
    for concurrency in (1, 4):
        print(f"BRI triage at concurrency {concurrency}", flush=True)
        run = pooled(CASES * 4, concurrency, triage)
        run["summary"]["workflows_per_second"] = run["summary"].pop("requests_per_second")
        for check in ("extraction_exact", "draft_matches_fields"):
            run["summary"][check] = sum(row[check] for row in run["requests"])
        runs.append({"concurrency": concurrency, **run})
    return {"fixtures": CASES, "runs": runs}


@contextmanager
def server(output, *, cache, label):
    env = os.environ.copy()
    env_bin = Path(sys.executable).parent
    env["PATH"] = os.pathsep.join((str(env_bin), env.get("PATH", "")))
    log_path = output / f"server-{label}.log"
    command = [
        str(env_bin / "vllm"), "serve", MODEL, "--host", "127.0.0.1", "--port", "8000",
        "--dtype", "half", "--max-model-len", "2048", "--enforce-eager",
        "--enable-prefix-caching" if cache else "--no-enable-prefix-caching",
    ]
    with log_path.open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        deadline = time.monotonic() + 360
        while process.poll() is None and time.monotonic() < deadline:
            try:
                response = requests.get(f"{BASE_URL}/v1/models", timeout=2)
                if response.ok and MODEL in [item["id"] for item in response.json()["data"]]:
                    break
            except requests.RequestException:
                pass
            time.sleep(2)
        else:
            tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-100:])
            raise RuntimeError(f"vLLM startup failed ({log_path}):\n{tail}")
        if process.poll() is not None:
            raise RuntimeError(f"vLLM exited before the experiment ({log_path})")
        yield
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def save(output, name, result):
    path = output / f"{name}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)
    print(f"Saved {path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", choices=("streaming", "prefix", "capacity", "bri"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    python_path = Path(sys.executable).parent
    subprocess.run([str(python_path / "ninja"), "--version"], check=True)
    gpu = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
        text=True,
    ).strip()
    from importlib.metadata import version
    from socket import create_connection

    try:
        with create_connection(("127.0.0.1", 8000), timeout=.5):
            raise RuntimeError("Port 8000 is occupied. Stop the baseline server or use a fresh runtime.")
    except ConnectionRefusedError:
        pass

    started = datetime.now(timezone.utc).isoformat()
    functions = {"streaming": streaming, "capacity": capacity, "bri": bri}
    if args.experiment == "prefix":
        result = prefix_comparison(args.output)
    else:
        with server(args.output, cache=False, label=args.experiment):
            result = functions[args.experiment]()
    save(args.output, args.experiment, {
        "experiment": args.experiment, "started_utc": started, "model": MODEL,
        "vllm": version("vllm"), "gpu": gpu, "dtype": "half",
        "max_model_len": 2048, "enforce_eager": True,
        "prefix_cache_enabled": None if args.experiment == "prefix" else False,
        **result,
    })


if __name__ == "__main__":
    main()
